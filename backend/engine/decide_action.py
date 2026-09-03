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

# The kill switch is reached through the module, NOT from-imported, because a
# from-import binds the value at import time and a mid-run flip would then
# silently fail to take effect. The constants below are the opposite case:
# they are recorded rulings that must NOT vary at runtime, so binding them at
# import is the point.
from backend.engine import phase5_config as _phase5

# Phase 5 declared bounds. Imported, never re-derived here: the method-change
# boundary and the exhaustion outcome are recorded rulings, and a second
# inline definition of either is how a boundary drifts.
from backend.engine.phase5_config import (
    EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS,
    EXECUTABLE_ACTIONS,
    EXHAUSTION_OUTCOME,
    MAX_FALLTHROUGH_CANDIDATES,
    METHOD_CHANGE_IS_EXECUTABLE,
)

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


def _undelivered_decision_ids(opportunity_id: str, conn) -> set:
    """
    Decisions whose execution row positively says the contact has NOT reached
    the customer. Amendment A1, dated 2026-09-03.

    execute_action() writes an outcome='executed' decision at SCHEDULE time,
    before anything is sent, so without this a scheduled action counts as a
    contact already made and blocks its own dispatch on cooldown (measured:
    "Cooldown active. 20.0h remaining" on a 4h-scheduled reminder) and burns
    an attempt against the MAX_RETRIES ceiling.

    Phrased as an exclusion on purpose. A decision counts as contact unless
    its execution row exists AND names a not-yet-delivered state, so a
    decision with no execution row -- every row in the pre-Phase-5 golden
    corpus -- keeps counting exactly as it did. Absence of evidence is treated
    as contact made, the safe direction for a compliance rule.

    This adds no new rule and moves no threshold. Cooldown is still 24h and
    the ceiling is still 3; only the question "has this contact happened" is
    answered from the execution lifecycle, which is the table that owns it.
    """
    placeholders = ",".join("?" * len(_phase5.CONTACT_NOT_YET_DELIVERED_STATES))
    rows = conn.execute(
        f"""
        SELECT e.decision_id
        FROM recovery_executions e
        JOIN recovery_decisions d ON d.decision_id = e.decision_id
        WHERE d.opportunity_id = ? AND e.state IN ({placeholders})
        """,
        (opportunity_id, *_phase5.CONTACT_NOT_YET_DELIVERED_STATES),
    ).fetchall()
    return {r["decision_id"] for r in rows}


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


# --------------------------------------------------------------------------
# Phase 5: optimizer-driven candidate adjudication
#
# Everything below is reached ONLY when a caller supplies ranked_candidates.
# With ranked_candidates=None the function body below is untouched and byte-
# identical to its pre-Phase-5 form, which the W1 golden corpus pins.
# --------------------------------------------------------------------------

# Customer-facing actions, which the contact-hours window applies to.
# `escalate` is deliberately absent: SoT section 7 exempts it because it is
# internal routing to a human queue, not customer contact. `payment_link` IS
# here -- it delivers a link to the customer over a channel, so it is contact
# by the same definition, even though the pre-Phase-5 code never had to
# classify it (payment_link could not be a hardcoded default_action, so it
# never reached the window check).
CONTACT_ACTIONS = ("retry", "reminder", "payment_link")


def _within_contact_window(created_at) -> bool:
    """
    The 9am-8pm check, evaluated on the event's simulated clock exactly as the
    hardcoded path does.

    This deliberately duplicates the condition at the bottom of decide_action()
    rather than extracting a shared helper, because the hardcoded body is
    required to stay literally unmodified. The duplication is pinned by
    test_contact_window_helper_agrees_with_the_hardcoded_branch, which asserts
    the two agree at all 24 hours -- so a change to one that is not mirrored in
    the other is a test failure, not a silent divergence.
    """
    hour = datetime.fromtimestamp(created_at).hour
    return CONTACT_WINDOW_START <= hour < CONTACT_WINDOW_END


def _is_method_change(candidate: dict, current_method) -> bool:
    """
    A method change is not an action type -- it is a retry carrying a payment
    method other than the one on the opportunity's latest attempt. See
    PHASE5_NOTES.md section 0.1.

    Derived from the method values themselves rather than trusting the
    generator's `method_changed` flag alone; the flag is cross-checked, and a
    disagreement is treated as a method change (the safe direction) rather
    than resolved in favour of either source.
    """
    method = candidate.get("method")
    if method in (None, "n/a"):
        return False
    derived = current_method is not None and method != current_method
    flagged = bool(candidate.get("method_changed", False))
    return derived or flagged


def _candidate_block_reason(candidate: dict, current_method, created_at):
    """
    Why this candidate cannot be executed, or None if it can.

    Executability only. Every opportunity-scoped compliance rule (cooldown,
    attempt ceiling, already-stopped/escalated, confidence and mismatch
    gating) has already been adjudicated by the unchanged hardcoded path
    before this is ever called -- those rules block every candidate equally,
    so they cannot be a reason to prefer one candidate over another.
    """
    action = candidate.get("action_type")

    if action in EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS:
        return f"{action} is evaluable but not executable"

    if action not in EXECUTABLE_ACTIONS:
        return f"{action} is outside the executable action vocabulary"

    if _is_method_change(candidate, current_method):
        if METHOD_CHANGE_IS_EXECUTABLE:
            raise ValueError(
                "METHOD_CHANGE_IS_EXECUTABLE is True; autonomous payment-method "
                "switching is a permanent structural boundary")
        return "payment-method change is evaluable but never executable"

    if action in CONTACT_ACTIONS and not _within_contact_window(created_at):
        return (f"outside permitted contact window "
                f"({CONTACT_WINDOW_START}:00-{CONTACT_WINDOW_END}:00)")

    return None


def _decide_action_from_ranked(opportunity, classification, conn,
                               ranked_candidates, baseline, latest_payment,
                               as_of=None):
    """
    Walk the optimizer's ranked list and select the first executable
    candidate, given a baseline verdict that has already cleared every
    opportunity-scoped compliance rule.

    The list is consumed **in the order given**. Nothing here sorts, reverses
    or otherwise reorders it, and nothing here reads predicted_eiv: ranking
    authority belongs to the optimizer exclusively (EXECUTION_PLAN.md:83), and
    the rupee-space ordering carries a measured ~16% pair-order sensitivity
    (PHASE4_NOTES.md section 8.6) that the rule engine must disclose rather
    than silently correct. Both properties are enforced statically by
    tests/test_phase5_fallthrough.py.
    """
    opportunity_id = opportunity["opportunity_id"]
    # Amendment A2, 2026-09-03: the clock the contact window is judged
    # against. `created_at` when no caller supplied one, which is what the
    # hardcoded path uses and what the golden corpus pins.
    created_at = opportunity["created_at"] if as_of is None else as_of
    current_method = (latest_payment or {}).get("method")

    if len(ranked_candidates) > MAX_FALLTHROUGH_CANDIDATES:
        raise ValueError(
            f"ranked list of {len(ranked_candidates)} exceeds the declared "
            f"ceiling of {MAX_FALLTHROUGH_CANDIDATES} "
            f"(opportunity {opportunity_id}); the optimizer's own candidate "
            "bound was breached upstream")

    selected, selected_position, skipped = None, None, []
    for position, candidate in enumerate(ranked_candidates):
        reason = _candidate_block_reason(candidate, current_method, created_at)
        if reason is None:
            selected, selected_position = candidate, position
            break
        skipped.append(f"rank {candidate.get('rank', position + 1)} "
                       f"{candidate.get('action_type')}: {reason}")

    if selected is None:
        # If the hardcoded path had already blocked this opportunity for a
        # specific reason, that reason is more informative than the generic
        # exhaustion outcome and is what gets recorded. Reaching here with a
        # blocked baseline means the fallthrough was attempted (a contact-hours
        # block) and found nothing outside the window's reach -- the original
        # block is still the true and narrower answer.
        if not baseline["allowed"]:
            return baseline
        return {
            "action_type": None,
            "allowed": False,
            "reasoning": (
                f"No executable candidate among {len(ranked_candidates)} ranked. "
                + "; ".join(skipped) + ". Routed to manual review."),
            "outcome": EXHAUSTION_OUTCOME,
            "triggered_by": "rule",
            "flag_type": baseline.get("flag_type"),
            "candidate_id": None,
        }

    action = selected["action_type"]
    rank = selected.get("rank", selected_position + 1)

    detail = f"Selected rank {rank} ({action}) from {len(ranked_candidates)} ranked candidates."
    if skipped:
        detail += f" Fell through {len(skipped)}: " + "; ".join(skipped) + "."
    # Disclosure, never a gate: where the fallthrough landed is only
    # interpretable against how confident the ranking was at that point. See
    # PHASE4_HANDOFF section 3 -- eiv_confidence is display metadata and must
    # never become a compliance input.
    confidence = selected.get("eiv_confidence")
    if confidence is not None:
        detail += f" Ranking confidence at selection: {confidence}"
        gap = selected.get("eiv_gap_to_next")
        if gap is not None:
            detail += f" (gap to next {gap:.4f})"
        detail += "."

    return {
        "action_type": action,
        "allowed": True,
        "reasoning": detail,
        "outcome": "executed",
        "triggered_by": "rule",
        # The advisory ML field keeps one meaning across both paths: it is the
        # legacy scorer's read on the action actually selected. The optimizer's
        # own richer predictions are NOT copied here -- they already live in
        # recovery_candidates, reachable through candidate_id, and folding them
        # into this column would give one field two provenances.
        "ml_recovery_probability": _get_recovery_probability(
            opportunity, latest_payment, classification, action,
            len([h for h in _get_history(opportunity_id, conn)
                 if h["action_type"] == "retry" and h["outcome"] == "executed"]),
            _get_history(opportunity_id, conn), int(time.time()), conn
        ),
        "flag_type": baseline.get("flag_type"),
        # Deliberately no `method` key on any branch: the executor must have no
        # field through which a payment-method change could ride.
        "candidate_id": selected.get("candidate_id"),
    }


def decide_action(opportunity: dict, classification: dict, conn,
                   latest_payment: dict = None,
                   extracted_intent: str = None,
                   intent_confidence: float = None,
                   mentioned_reason: str = None,
                   dispute_flag: bool = False,
                   ranked_candidates: list = None,
                   as_of: int = None) -> dict:
    """
    Returns:
    {
      "action_type": "retry"|"reminder"|"escalate"|"stop"|None,
      "allowed": bool,
      "reasoning": str,
      "outcome": "executed"|"blocked_cooldown"|"blocked_contact_hours"|
                 "blocked_already_escalated"|"blocked_already_stopped"|
                 "flagged_manual_review",
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

    ranked_candidates (Phase 5) is the optimizer's FULL ranked list, not just
    its top pick, so that a blocked top candidate can fall through to the next
    executable one. It is advisory: the optimizer proposes an order, this
    function decides. The list is consumed in the order given and is never
    re-sorted here.

    When ranked_candidates is None -- the default, and what every pre-Phase-5
    caller passes -- execution proceeds directly into the body below, which is
    unchanged from its pre-Phase-5 form. That equivalence is pinned
    byte-for-byte by the W1 golden corpus
    (tests/golden/phase5_decide_action_golden.json).

    When a list IS supplied, the hardcoded path still runs first and still has
    the final say on compliance: the ranked path only chooses *which* action,
    and only in the ordinary case where the hardcoded path had already cleared
    the opportunity to act with a retry or reminder. It never overturns a
    block, and never overrides a terminal or safety policy (auto-escalation
    after no response, the attempt ceiling's stop, a deeply-overdue
    escalation).
    """
    # Read at call time, never captured at import, so flipping it mid-run
    # takes effect on the very next decision. With it False a supplied ranked
    # list is ignored entirely and execution falls through to the hardcoded
    # body below -- byte-for-byte the pre-Phase-5 behaviour, no code revert
    # needed.
    if ranked_candidates is not None and _phase5.OPTIMIZER_PATHWAY_ENABLED:
        # One recursion with ranked_candidates=None runs the unmodified
        # hardcoded path and yields the authoritative compliance verdict.
        baseline = decide_action(
            opportunity, classification, conn,
            latest_payment=latest_payment,
            extracted_intent=extracted_intent,
            intent_confidence=intent_confidence,
            mentioned_reason=mentioned_reason,
            dispute_flag=dispute_flag,
            ranked_candidates=None,
            as_of=as_of,
        )
        # Almost every blocking rule is opportunity-scoped -- cooldown,
        # attempt ceiling, already stopped/escalated, confidence and mismatch
        # gating all block every candidate equally. Falling through one of
        # those would be the ranked path overturning a compliance decision.
        #
        # blocked_contact_hours is the single exception, and it is exactly the
        # case the plan's fallthrough exists for: the hardcoded path applies
        # the window only to customer-contact actions and lets escalate bypass
        # it, so a non-contact candidate can still be legitimately executable
        # here. Falling through is not overturning the block -- the block
        # stands for every action it actually covers.
        if not baseline["allowed"] and baseline["outcome"] != "blocked_contact_hours":
            return baseline
        # allowed=True can still mean a policy fired rather than the ordinary
        # pass-through: auto-escalation, the attempt ceiling's stop, or a
        # deeply-overdue escalation. Restricting substitution to retry/reminder
        # keeps the optimizer out of every one of those without needing to
        # re-derive which branch produced the verdict.
        if baseline["action_type"] not in ("retry", "reminder"):
            return baseline
        return _decide_action_from_ranked(
            opportunity, classification, conn,
            ranked_candidates, baseline, latest_payment, as_of)

    opportunity_id = opportunity["opportunity_id"]
    event_type = opportunity["event_type"]
    now = int(time.time())

    history = _get_history(opportunity_id, conn)
    # Amendment A1, 2026-09-03: a decision that was approved but whose
    # execution is still queued (or was cancelled) is not a contact. See
    # _undelivered_decision_ids(). Empty for every pre-Phase-5 opportunity,
    # which is why the golden corpus reproduces unchanged.
    undelivered = _undelivered_decision_ids(opportunity_id, conn)
    contact_history = [
        h for h in history
        if h["action_type"] in ("retry", "reminder") and h["outcome"] == "executed"
        and h["decision_id"] not in undelivered
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
        # Amendment A2, 2026-09-03: evaluated against `as_of` when the caller
        # supplies one -- the dispatcher passes the moment the action would
        # actually fire, so a 3-day-scheduled action is checked against 3am,
        # not against the noon it was created at. With as_of None this is
        # `created_at`, byte-for-byte the pre-amendment expression.
        simulated_hour = datetime.fromtimestamp(
            opportunity["created_at"] if as_of is None else as_of).hour
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
