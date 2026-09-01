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


def deliver_recovery_message(opportunity: dict, classification: dict, decision: dict, conn,
                              latest_payment: dict = None) -> dict:
    """
    Returns:
    {
      "delivered": bool,
      "status": "ok" | "fallback" | "skipped_ineligible" | "persist_failed",
      "message": str | None
    }

    Only generates/persists a message when outcome=="executed" and
    action_type in ("retry", "reminder"). escalate, stop, all blocked_*
    outcomes, and flagged_manual_review are skipped -- no message, no
    messages row. Read-only with respect to decision -- never mutates it,
    never re-triggers decide_action()/execute_action().
    """
    if decision.get("outcome") != "executed" or decision.get("action_type") not in ELIGIBLE_ACTIONS:
        return {"delivered": False, "status": "skipped_ineligible", "message": None}

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
