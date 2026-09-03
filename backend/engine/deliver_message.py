"""
deliver_message.py — Stage 3, Micro-step 4.
Wires generate_recovery_message() into the recovery flow, called after
execute_action() in both core_loop.py (batch) and handle_customer_reply.py
(reply-triggered). Only ever acts on an already-finalized decision -- never
influences or re-selects an action. decide_action() remains sole authority.

Phase 1 (Schema Foundation): messages are keyed by opportunity_id (the
conversation spans the whole case, not one payment attempt). The LLM layer
itself (llm/generate_message.py) is untouched -- it still takes a plain
dict with event_type/amount/currency; this file is responsible for
assembling that dict from the opportunity (+ latest payment attempt for
currency, which stays a per-attempt transactional field).
"""

import time

from backend.llm.generate_message import generate_recovery_message

ELIGIBLE_ACTIONS = {"retry", "reminder"}

# The recovery_executions states in which sending is legitimate. Named rather
# than inlined so the delivery gate, the executor and the dispatcher cannot
# drift onto different ideas of "is firing".
#
#   'executed'   -- the immediate path: execute_action() dispatches inline and
#                   writes this state directly, then delivery follows.
#   'dispatched' -- the scheduled path: the sweep has claimed the row and is
#                   firing it right now; delivery happens here, and the row
#                   completes to 'executed' only once the send returned.
#
# 'scheduled' and 'pending' are absent, which is the whole point of ruling A7:
# an action that has not been picked up yet must not reach the customer.
DELIVERABLE_STATES = ("dispatched", "executed")


def deliver_recovery_message(opportunity: dict, classification: dict, decision: dict, conn,
                              latest_payment: dict = None,
                              decision_id: int = None) -> dict:
    """
    Returns:
    {
      "delivered": bool,
      "status": "ok" | "fallback" | "skipped_ineligible"
                | "skipped_not_executed" | "skipped_unverified_execution"
                | "persist_failed",
      "message": str | None
    }

    Only generates/persists a message when outcome=="executed",
    action_type in ("retry", "reminder"), AND the execution named by
    `decision_id` has actually reached the terminal 'executed' state.
    escalate, stop, all blocked_* outcomes, and flagged_manual_review are
    skipped -- no message, no messages row. Read-only with respect to
    decision -- never mutates it, never re-triggers
    decide_action()/execute_action().

    Phase 5 / ruling A7, 2026-09-03 -- WHY THE SECOND GATE EXISTS.
    `outcome` is a COMPLIANCE verdict: it says the action was permitted, and
    execute_action() writes it the moment the action is approved. Whether the
    action has actually fired lives in recovery_executions.state, and for a
    scheduled action the two disagree for the whole scheduling window -- the
    decision reads 'executed' while the execution sits in 'scheduled' for up
    to 3 days. Gating on `outcome` alone therefore contacted the customer at
    SCHEDULE time, and the dispatcher would contact them again at due time.
    Measured before the fix: 1 agent message written while the execution row
    read state='scheduled', executed_at=None, 4h before it was due.

    Reading a lifecycle answer out of the compliance field is the exact
    conflation the "Execution separation" gate and the five-distinct-concepts
    invariant forbid, so this is a correctness fix, not a new feature.
    """
    if decision.get("outcome") != "executed" or decision.get("action_type") not in ELIGIBLE_ACTIONS:
        return {"delivered": False, "status": "skipped_ineligible", "message": None}

    # Fail closed. Without a decision_id this function cannot know whether the
    # action fired, and guessing in the permissive direction is what produced
    # the double contact. A missed message is visible in the returned status
    # and costs one delayed follow-up; a duplicate contact is a compliance
    # breach against the customer.
    if decision_id is None:
        decision_id = decision.get("decision_id")
    if decision_id is None:
        return {"delivered": False, "status": "skipped_unverified_execution",
                "message": None}

    row = conn.execute(
        "SELECT state FROM recovery_executions WHERE decision_id = ?",
        (decision_id,),
    ).fetchone()
    if row is None or row["state"] not in DELIVERABLE_STATES:
        return {"delivered": False, "status": "skipped_not_executed",
                "message": None}

    # llm/generate_message.py's signature/contract is untouched -- it takes
    # a plain payment-shaped dict. Assemble it here rather than changing
    # the LLM layer's boundary.
    message_context = {
        "event_type": opportunity.get("event_type"),
        "amount": opportunity.get("amount_at_risk"),
        "currency": (latest_payment or {}).get("currency", "INR"),
    }

    generated = generate_recovery_message(message_context, classification, decision["action_type"])
    message_text = generated["message"]
    gen_status = generated["status"]  # "ok" | "fallback" -- both persisted identically

    try:
        conn.execute(
            """
            INSERT INTO messages
            (opportunity_id, sender, content, intent_extracted, intent_confidence, mentioned_reason, timestamp)
            VALUES (?, 'agent', ?, NULL, NULL, NULL, ?)
            """,
            (opportunity["opportunity_id"], message_text, int(time.time())),
        )
        conn.commit()
    except Exception:
        # execute_action() has already committed the recovery decision before
        # this function is ever called -- a persistence failure here cannot
        # roll back or affect the already-executed action.
        return {"delivered": False, "status": "persist_failed", "message": message_text}

    return {"delivered": True, "status": gen_status, "message": message_text}
