"""
decide_action(): rule engine, final authority on compliance (SoT section 5).
Single shared function for all three event types.
Stage 1: hard-coded rules only. ML risk score consulted (not overridden) in stage 2.

Compliance rules (SoT section 7, locked, non-negotiable):
- Max 3 retry/reminder attempts per transaction
- Minimum 24hr cooldown between contact attempts
- Auto-stop after 7 days no response -> escalate to human queue
- No contact outside 9am-8pm (simulated)
- Every action logged with a reason -- no silent actions
"""

import time
import os
from datetime import datetime

MAX_RETRIES = 3
COOLDOWN_HOURS = 24
AUTO_STOP_DAYS = 7
CONTACT_WINDOW_START = 9   # 9am
CONTACT_WINDOW_END = 20    # 8pm

DAY_SECONDS = 86400
COOLDOWN_SECONDS = COOLDOWN_HOURS * 3600


def _get_history(payment_id: str, conn):
    rows = conn.execute(
        "SELECT * FROM recovery_actions WHERE payment_id = ? ORDER BY timestamp ASC",
        (payment_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _has_customer_reply(payment_id: str, conn) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as c FROM messages WHERE payment_id = ? AND sender = 'customer'",
        (payment_id,),
    ).fetchone()
    return row["c"] > 0


_ML_MODEL = None
_ML_MODEL_LOAD_ATTEMPTED = False


def _load_ml_model():
    """Lazy-load the ML model once. Never raises -- returns None on any failure."""
    global _ML_MODEL, _ML_MODEL_LOAD_ATTEMPTED
    if _ML_MODEL_LOAD_ATTEMPTED:
        return _ML_MODEL
    _ML_MODEL_LOAD_ATTEMPTED = True
    try:
        import joblib
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "ml", "models", "xgb_model.joblib"
        )
        _ML_MODEL = joblib.load(model_path)
    except Exception:
        _ML_MODEL = None
    return _ML_MODEL


def _get_customer(customer_id, conn):
    if not customer_id:
        return {}
    row = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    return dict(row) if row else {}


def _get_recovery_probability(payment, classification, candidate_action,
                               retry_only_count, history, now, conn):
    """
    Advisory-only ML signal. Scores the single action already selected by
    the rule engine -- does not compare alternative actions, does not
    influence action selection. Returns None if model unavailable or
    scoring fails for any reason.
    """
    model = _load_ml_model()
    if model is None:
        return None
    try:
        customer = _get_customer(payment.get("customer_id"), conn)

        last_entry = history[-1] if history else None
        last_action_type = last_entry["action_type"] if last_entry else "none"
        hours_since_last_action = (
            (now - last_entry["timestamp"]) / 3600 if last_entry else 0
        )

        days_since_event = (now - payment["created_at"]) / 86400

        import pandas as pd
        row = pd.DataFrame([{
            "event_type": payment.get("event_type"),
            "root_cause": classification.get("root_cause") if classification else None,
            "amount": payment.get("amount"),
            "method": payment.get("method"),
            "retry_count": retry_only_count,
            "days_since_event": days_since_event,
            "days_overdue": payment.get("days_overdue") or 0,
            "last_action_type": last_action_type,
            "hours_since_last_action": hours_since_last_action,
            "candidate_action": candidate_action,
            "payment_history_score": customer.get("payment_history_score", 0.5),
            "past_recovery_rate": customer.get("past_recovery_rate", 0.5),
            "preferred_channel": customer.get("preferred_channel", "email"),
        }])
        row["root_cause"] = row["root_cause"].fillna("none")

        proba = model.predict_proba(row)[0, 1]
        return round(float(proba), 4)
    except Exception:
        return None


def decide_action(payment: dict, classification: dict, conn) -> dict:
    """
    Returns:
    {
      "action_type": "retry"|"reminder"|"escalate"|"stop",
      "allowed": bool,
      "reasoning": str,
      "outcome": "executed"|"blocked_cooldown"|"blocked_max_retries"|
                 "blocked_contact_hours"|"blocked_already_escalated"|
                 "blocked_already_stopped",
      "triggered_by": "rule"
    }
    """
    payment_id = payment["id"]
    event_type = payment["event_type"]
    now = int(time.time())

    history = _get_history(payment_id, conn)
    contact_history = [
        h for h in history
        if h["action_type"] in ("retry", "reminder") and h["outcome"] == "executed"
    ]
    contact_count = len(contact_history)
    last_contact_ts = contact_history[-1]["timestamp"] if contact_history else None

    # ML-only signal: actual retry attempts, distinct from contact_count
    # (which combines retry+reminder for the compliance check above).
    retry_only_count = len([
        h for h in history
        if h["action_type"] == "retry" and h["outcome"] == "executed"
    ])

    already_escalated = any(
        h["action_type"] == "escalate" and h["outcome"] == "executed" for h in history
    )
    already_stopped = any(
        h["action_type"] == "stop" and h["outcome"] == "executed" for h in history
    )

    default_action = "reminder" if event_type != "payment_failed" else "retry"
    if event_type == "invoice_overdue" and (payment.get("days_overdue") or 0) > 14:
        default_action = "escalate"

    # already stopped -> stays stopped
    if already_stopped:
        return {
            "action_type": "stop",
            "allowed": False,
            "reasoning": "Case already stopped. No further contact permitted.",
            "outcome": "blocked_already_stopped",
            "triggered_by": "rule",
        }

    # already escalated -> human queue owns it now
    if already_escalated:
        return {
            "action_type": "escalate",
            "allowed": False,
            "reasoning": "Case already escalated to human queue. Automated actions suspended.",
            "outcome": "blocked_already_escalated",
            "triggered_by": "rule",
        }

        # auto-stop after 7 days no response -> escalate
    # invoice_overdue uses days_overdue (event-specific timing input,
    # SoT section 3); other event types use age from created_at.
    if event_type == "invoice_overdue":
        no_response_trigger = (payment.get("days_overdue") or 0) >= AUTO_STOP_DAYS
    else:
        age_seconds = now - payment["created_at"]
        no_response_trigger = age_seconds > AUTO_STOP_DAYS * DAY_SECONDS

    if no_response_trigger and not _has_customer_reply(payment_id, conn):
        return {
            "action_type": "escalate",
            "allowed": True,
            "reasoning": f"No customer response after {AUTO_STOP_DAYS} days. Auto-escalating to human queue.",
            "outcome": "executed",
            "triggered_by": "rule",
            "ml_recovery_probability": _get_recovery_probability(
                payment, classification, "escalate",
                retry_only_count, history, now, conn
            ),
        }

    # max 3 contact attempts -> stop
    if contact_count >= MAX_RETRIES:
        return {
            "action_type": "stop",
            "allowed": True,
            "reasoning": f"Max {MAX_RETRIES} contact attempts reached. Stopping further automated contact.",
            "outcome": "executed",
            "triggered_by": "rule",
        }

    # 24hr cooldown between contact attempts
    if last_contact_ts is not None and (now - last_contact_ts) < COOLDOWN_SECONDS:
        remaining_hrs = round((COOLDOWN_SECONDS - (now - last_contact_ts)) / 3600, 1)
        return {
            "action_type": default_action,
            "allowed": False,
            "reasoning": f"Cooldown active. {remaining_hrs}h remaining before next contact allowed.",
            "outcome": "blocked_cooldown",
            "triggered_by": "rule",
        }

        # no contact outside 9am-8pm, evaluated on the event's simulated
    # clock (created_at), not the real system clock. escalate is
    # internal routing, not customer contact, so it bypasses this check.
    if default_action in ("retry", "reminder"):
        simulated_hour = datetime.fromtimestamp(payment["created_at"]).hour
        if not (CONTACT_WINDOW_START <= simulated_hour < CONTACT_WINDOW_END):
            return {
                "action_type": default_action,
                "allowed": False,
                "reasoning": f"Outside permitted contact window ({CONTACT_WINDOW_START}:00-{CONTACT_WINDOW_END}:00).",
                "outcome": "blocked_contact_hours",
                "triggered_by": "rule",
            }

    # all compliance checks passed
    return {
        "action_type": default_action,
        "allowed": True,
        "reasoning": f"Compliance checks passed. Executing {default_action} (attempt {contact_count + 1}/{MAX_RETRIES}).",
        "outcome": "executed",
        "triggered_by": "rule",
        "ml_recovery_probability": _get_recovery_probability(
            payment, classification, default_action,
            retry_only_count, history, now, conn
        ),
    }