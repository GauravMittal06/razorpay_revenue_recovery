"""
deliver_message.py — Stage 3, Micro-step 4.
Wires generate_recovery_message() into the recovery flow, called after
execute_action() in both core_loop.py (batch) and handle_customer_reply.py
(reply-triggered). Only ever acts on an already-finalized decision -- never
influences or re-selects an action. decide_action() remains sole authority.
"""

import time

from llm.generate_message import generate_recovery_message

ELIGIBLE_ACTIONS = {"retry", "reminder"}


def deliver_recovery_message(payment: dict, classification: dict, decision: dict, conn) -> dict:
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

    generated = generate_recovery_message(payment, classification, decision["action_type"])
    message_text = generated["message"]
    gen_status = generated["status"]  # "ok" | "fallback" -- both persisted identically

    try:
        conn.execute(
            """
            INSERT INTO messages
            (payment_id, sender, content, intent_extracted, intent_confidence, mentioned_reason, timestamp)
            VALUES (?, 'agent', ?, NULL, NULL, NULL, ?)
            """,
            (payment["id"], message_text, int(time.time())),
        )
        conn.commit()
    except Exception:
        # execute_action() has already committed the recovery decision before
        # this function is ever called -- a persistence failure here cannot
        # roll back or affect the already-executed action.
        return {"delivered": False, "status": "persist_failed", "message": message_text}

    return {"delivered": True, "status": gen_status, "message": message_text}