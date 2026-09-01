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

Phase 1 (Schema Foundation): operates on an `opportunity` dict (the
economic situation, one row per distinct revenue-at-risk case) plus an
optional `latest_payment` dict (the most recent transactional attempt, for
attempt-specific ML features like `method`). Compliance history is read
from recovery_decisions, keyed by opportunity_id, not from the retired
recovery_actions table keyed by payment_id. None of the compliance rules
themselves changed -- only what they read from and will be written against.
"""

import time
import os
from datetime import datetime

MAX_RETRIES = 3
COOLDOWN_HOURS = 24
AUTO_STOP_DAYS = 7
CONTACT_WINDOW_START = 9   # 9am (9)
CONTACT_WINDOW_END = 20    # 8pm (20)

# Stage 3, Micro-step 1 (locked): LLM intent-confidence threshold.
# Below this, decide_action() never auto-selects an action -- flags for
# manual review instead (SoT section 9c-1).
CONFIDENCE_THRESHOLD = 0.6

# Locked error_reason values (SoT section 6) -- used only for compatibility
# checking against LLM-extracted mentioned_reason, never sent to the LLM.
METHOD_CLASS_ROOT_CAUSES = {"expired_card", "payment_declined", "authentication_failed"}
NON_METHOD_ROOT_CAUSES = {"insufficient_funds", "gateway_timeout", "network_error"}

DAY_SECONDS = 86400
COOLDOWN_SECONDS = COOLDOWN_HOURS * 3600


def _get_history(opportunity_id: str, conn):
    rows = conn.execute(
        "SELECT * FROM recovery_decisions WHERE opportunity_id = ? ORDER BY timestamp ASC",
        (opportunity_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _has_customer_reply(opportunity_id: str, conn) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as c FROM messages WHERE opportunity_id = ? AND sender = 'customer'",
        (opportunity_id,),
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


def _check_intent_compatibility(root_cause, mentioned_reason, extracted_intent):
    """
    Compares LLM-extracted mentioned_reason against the stored root_cause.
    Returns (flag_type, is_blocking):
      - (None, False)                          -- nothing to flag, proceed normally
      - ("root_cause_update_candidate", False) -- payment_method_updated legitimately
        resolves a method-class root cause; log only, never blocks
      - ("mismatch", True)                     -- genuine conflict, blocks auto-action

    Stage 3 Micro-step 1 approved contract. Not a simplistic equality check --
    payment_method_updated against a method-class root cause is treated as a
    legitimate update, not a conflict.
    """
    if mentioned_reason is None or mentioned_reason == root_cause:
        return None, False

    if extracted_intent == "payment_method_updated" and mentioned_reason in METHOD_CLASS_ROOT_CAUSES:
        return "root_cause_update_candidate", False

    return "mismatch", True


def _get_recovery_probability(opportunity, latest_payment, classification, candidate_action,
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
        customer = _get_customer(opportunity.get("customer_id"), conn)

        last_entry = history[-1] if history else None
        last_action_type = last_entry["action_type"] if last_entry else "none"
        hours_since_last_action = (
            (now - last_entry["timestamp"]) / 3600 if last_entry else 0
        )

        days_since_event = (now - opportunity["created_at"]) / 86400
        method = (latest_payment or {}).get("method")

        import pandas as pd
        row = pd.DataFrame([{
            "event_type": opportunity.get("event_type"),
            "root_cause": classification.get("root_cause") if classification else None,
            "amount": opportunity.get("amount_at_risk"),
            "method": method,
            "retry_count": retry_only_count,
            "days_since_event": days_since_event,
            "days_overdue": opportunity.get("days_overdue") or 0,
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


def decide_action(opportunity: dict, classification: dict, conn,
                   latest_payment: dict = None,
                   extracted_intent: str = None,
                   intent_confidence: float = None,
                   mentioned_reason: str = None,
                   dispute_flag: bool = False) -> dict:
    """
    Returns:
    {
      "action_type": "retry"|"reminder"|"escalate"|"stop"|None,
      "allowed": bool,
      "reasoning": str,
      "outcome": "executed"|"blocked_cooldown"|"blocked_max_retries"|
                 "blocked_contact_hours"|"blocked_already_escalated"|
                 "blocked_already_stopped"|"flagged_manual_review",
      "triggered_by": "rule",
      "flag_type": "mismatch"|"root_cause_update_candidate"|"dispute_flag"|None
    }

    `latest_payment` is optional context (the most recent transactional
    attempt on this opportunity) used only for attempt-specific ML
    features like `method` -- never for compliance branching, which is
    entirely opportunity-scoped.

    extracted_intent / intent_confidence / mentioned_reason / dispute_flag are
    optional advisory inputs from the LLM intent-parsing layer (Stage 3). They
    never select or trigger an action directly -- decide_action() remains sole
    compliance/control authority. Defaults preserve pre-Stage-3 behavior
    exactly when omitted (e.g. existing core_loop.py batch calls).
    """
    opportunity_id = opportunity["opportunity_id"]
    event_type = opportunity["event_type"]
    now = int(time.time())

    history = _get_history(opportunity_id, conn)
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
    if event_type == "invoice_overdue" and (opportunity.get("days_overdue") or 0) > 14:
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

    # Stage 3, Micro-step 1 (locked): LLM intent pre-gate. Never selects an
    # action -- only decides whether to hard-stop for manual review.
    pending_flag_type = None
    if dispute_flag:
        return {
            "action_type": None,
            "allowed": False,
            "reasoning": "Customer reply indicates a dispute. Routed to manual review.",
            "outcome": "flagged_manual_review",
            "triggered_by": "rule",
            "flag_type": "dispute_flag",
        }
    if intent_confidence is not None and intent_confidence < CONFIDENCE_THRESHOLD:
        return {
            "action_type": None,
            "allowed": False,
            "reasoning": f"LLM intent confidence {intent_confidence:.2f} below threshold {CONFIDENCE_THRESHOLD}. Routed to manual review.",
            "outcome": "flagged_manual_review",
            "triggered_by": "rule",
            "flag_type": None,
        }
    if extracted_intent is not None or mentioned_reason is not None:
        flag_type, is_blocking_mismatch = _check_intent_compatibility(
            classification.get("root_cause") if classification else None,
            mentioned_reason,
            extracted_intent,
        )
        if is_blocking_mismatch:
            return {
                "action_type": None,
                "allowed": False,
                "reasoning": f"Extracted intent conflicts with stored root_cause (mentioned_reason={mentioned_reason}). Routed to manual review.",
                "outcome": "flagged_manual_review",
                "triggered_by": "rule",
                "flag_type": flag_type,
            }
        pending_flag_type = flag_type

        # auto-stop after 7 days no response -> escalate
    # invoice_overdue uses days_overdue (event-specific timing input,
    # SoT section 3); other event types use age from created_at.
    if event_type == "invoice_overdue":
        no_response_trigger = (opportunity.get("days_overdue") or 0) >= AUTO_STOP_DAYS
    else:
        age_seconds = now - opportunity["created_at"]
        no_response_trigger = age_seconds > AUTO_STOP_DAYS * DAY_SECONDS

    if no_response_trigger and not _has_customer_reply(opportunity_id, conn):
        return {
            "action_type": "escalate",
            "allowed": True,
            "reasoning": f"No customer response after {AUTO_STOP_DAYS} days. Auto-escalating to human queue.",
            "outcome": "executed",
            "triggered_by": "rule",
            "ml_recovery_probability": _get_recovery_probability(
                opportunity, latest_payment, classification, "escalate",
                retry_only_count, history, now, conn
            ),
            "flag_type": pending_flag_type,
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
        simulated_hour = datetime.fromtimestamp(opportunity["created_at"]).hour
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
            opportunity, latest_payment, classification, default_action,
            retry_only_count, history, now, conn
        ),
        "flag_type": pending_flag_type,
    }
