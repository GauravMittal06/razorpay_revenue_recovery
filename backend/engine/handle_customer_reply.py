"""
handle_customer_reply(): Stage 3, Micro-step 2.
Wires parse_reply_intent() -> decide_action() -> execute_action() for a real
incoming customer reply. Callable/testable directly, same pattern as
core_loop.py.

Does not modify parse_intent.py, decide_action.py, or execute_action.py.
Only sequences existing calls per the approved Micro-step 2 contract.

Phase 1 (Schema Foundation): addressed by opportunity_id now, since the
conversation belongs to the whole case, not one payment attempt. root_cause
for classify() is read from the opportunity (diagnosed once, at creation)
rather than re-derived from a payment's error_reason each time.
"""

import time

from backend.engine.pipeline import run_recovery_pipeline
from backend.llm.parse_intent import parse_reply_intent

# Request-synchronous entry point. Uses opportunity_lock (it reads cooldown
# then acts on it, exactly like the batch loop); optimizer stays off here
# while the latency budget is unmet.
ENTRY_POINT = "customer_reply"


def _latest_payment(opportunity_id: str, conn):
    row = conn.execute(
        "SELECT * FROM payments WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def handle_customer_reply(opportunity_id: str, customer_message: str, conn) -> dict:
    now = int(time.time())

    opportunity_row = conn.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()
    if opportunity_row is None:
        return {
            "opportunity_id": opportunity_id,
            "status": "opportunity_not_found",
            "error": f"No opportunity found with id={opportunity_id}",
        }
    opportunity = dict(opportunity_row)
    latest_payment = _latest_payment(opportunity_id, conn)

    # conversation_history fetched BEFORE inserting the new message --
    # guarantees the current reply is never included in its own history.
    history_rows = conn.execute(
        "SELECT sender, content, timestamp FROM messages WHERE opportunity_id = ? ORDER BY timestamp ASC",
        (opportunity_id,),
    ).fetchall()
    conversation_history = [dict(r) for r in history_rows]

    parsed = parse_reply_intent(customer_message, conversation_history, opportunity["event_type"])

    # Step 4: persist the message as its own atomic unit. Fail closed --
    # if this insert fails, do not proceed to decide_action/execute_action.
    try:
        cursor = conn.execute(
            """
            INSERT INTO messages
            (opportunity_id, sender, content, intent_extracted, intent_confidence, mentioned_reason, timestamp)
            VALUES (?, 'customer', ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
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
            "opportunity_id": opportunity_id,
            "status": "message_persist_failed",
            "parsed_intent": parsed,
            "error": str(e),
        }

    # Steps 5-7: classify -> decide_action -> execute_action.
    # Wrapped for unexpected failures beyond each component's own
    # already-existing fallback handling. No new outcome value or schema
    # change on error -- message row above remains the durable record.
    try:
        # W7: the identical shared pipeline core_loop.py and trigger_event.py
        # run. The intent fields are this entry point's distinguishing input
        # -- they are what arms decide_action()'s confidence and
        # intent-mismatch gates, which no other entry point triggers.
        #
        # The try/except around this stays HERE rather than moving into the
        # pipeline: converting an unexpected failure into
        # status="engine_error" is this function's own contract, and hoisting
        # it would impose it on the other two entry points, which currently
        # let exceptions propagate.
        outcome = run_recovery_pipeline(
            opportunity, conn,
            entry_point=ENTRY_POINT,
            latest_payment=latest_payment,
            extracted_intent=parsed["intent"],
            intent_confidence=parsed["confidence"],
            mentioned_reason=parsed["mentioned_reason"],
            dispute_flag=parsed["intent"] == "dispute",
        )
        decision = outcome["decision"]
        result = outcome["execution_result"]
    except Exception as e:
        return {
            "opportunity_id": opportunity_id,
            "message_id": message_id,
            "parsed_intent": parsed,
            "status": "engine_error",
            "error": str(e),
        }

    return {
        "opportunity_id": opportunity_id,
        "message_id": message_id,
        "parsed_intent": parsed,
        "decision": decision,
        "execution_result": result,
        "status": "ok",
    }
