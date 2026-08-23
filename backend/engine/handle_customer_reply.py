"""
handle_customer_reply(): Stage 3, Micro-step 2.
Wires parse_reply_intent() -> decide_action() -> execute_action() for a real
incoming customer reply. Not called by any live entry point yet (dashboard/
console wiring is a separate future micro-step) -- callable/testable directly,
same pattern as core_loop.py.

Does not modify parse_intent.py, decide_action.py, or execute_action.py.
Only sequences existing calls per the approved Micro-step 2 contract.
"""

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from engine.classify import classify
from engine.decide_action import decide_action
from engine.execute_action import execute_action
from engine.deliver_message import deliver_recovery_message
from llm.parse_intent import parse_reply_intent


def handle_customer_reply(payment_id: str, customer_message: str, conn) -> dict:
    now = int(time.time())

    payment_row = conn.execute(
        "SELECT * FROM payments WHERE id = ?", (payment_id,)
    ).fetchone()
    if payment_row is None:
        return {
            "payment_id": payment_id,
            "status": "payment_not_found",
            "error": f"No payment found with id={payment_id}",
        }
    payment = dict(payment_row)

    # conversation_history fetched BEFORE inserting the new message --
    # guarantees the current reply is never included in its own history.
    history_rows = conn.execute(
        "SELECT sender, content, timestamp FROM messages WHERE payment_id = ? ORDER BY timestamp ASC",
        (payment_id,),
    ).fetchall()
    conversation_history = [dict(r) for r in history_rows]

    parsed = parse_reply_intent(customer_message, conversation_history, payment["event_type"])

    # Step 4: persist the message as its own atomic unit. Fail closed --
    # if this insert fails, do not proceed to decide_action/execute_action.
    try:
        cursor = conn.execute(
            """
            INSERT INTO messages
            (payment_id, sender, content, intent_extracted, intent_confidence, mentioned_reason, timestamp)
            VALUES (?, 'customer', ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                customer_message,
                parsed["intent"],
                parsed["confidence"],
                parsed["mentioned_reason"],
                now,
            ),
        )
        conn.commit()
        message_id = cursor.lastrowid
    except Exception as e:
        return {
            "payment_id": payment_id,
            "status": "message_persist_failed",
            "parsed_intent": parsed,
            "error": str(e),
        }

    # Steps 5-7: classify -> decide_action -> execute_action.
    # Wrapped for unexpected failures beyond each component's own
    # already-existing fallback handling. No new outcome value or schema
    # change on error -- message row above remains the durable record.
    try:
        classification = classify(payment)
        dispute_flag = parsed["intent"] == "dispute"
        decision = decide_action(
            payment, classification, conn,
            extracted_intent=parsed["intent"],
            intent_confidence=parsed["confidence"],
            mentioned_reason=parsed["mentioned_reason"],
            dispute_flag=dispute_flag,
        )
        result = execute_action(payment, decision, conn)
        deliver_recovery_message(payment, classification, decision, conn)
    except Exception as e:
        return {
            "payment_id": payment_id,
            "message_id": message_id,
            "parsed_intent": parsed,
            "status": "engine_error",
            "error": str(e),
        }

    return {
        "payment_id": payment_id,
        "message_id": message_id,
        "parsed_intent": parsed,
        "decision": decision,
        "execution_result": result,
        "status": "ok",
    }