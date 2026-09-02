"""
mark_opportunity_recovered(): records a real payment-success event.
Separate from rule-engine authority -- decide_action()/execute_action()
own compliance/actions; this owns the ground-truth fact that money was
actually recovered. Not called by any live trigger yet (Live Agent
Console wiring is a separate future step).

Phase 1 (Schema Foundation): renamed from mark_payment_recovered() and
retargeted at opportunities, since "was this recovered" is a business
outcome of the whole case (Section 3), never a per-payment-attempt field.
Writes recovered_bool / recovered_at / time_to_recovery / resolution_type
/ status / resolved_at -- all opportunity-level, all fields that never
appear on recovery_decisions or recovery_executions (see execute_action.py
docstring for why that separation is enforced by schema, not convention).
By default this records a full recovery (partial_recovery_amount ==
amount_at_risk); pass `partial_recovery_amount` explicitly for a partial one.
"""

import time


def mark_opportunity_recovered(opportunity_id: str, conn, partial_recovery_amount: int = None) -> dict:
    row = conn.execute(
        "SELECT opportunity_id, status, amount_at_risk, created_at FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()

    if row is None:
        return {
            "opportunity_id": opportunity_id,
            "status": "opportunity_not_found",
        }

    current_status = row["status"]

    # These two early returns are a fast path and a clear error message, not
    # the safety mechanism. The real guard is the WHERE clause below: between
    # this read and that write, another caller can commit a recovery, and a
    # check up here cannot see it.
    if current_status == "stopped":
        return {
            "opportunity_id": opportunity_id,
            "status": "rejected_stopped",
            "opportunity_status": current_status,
        }

    if current_status == "recovered":
        return {
            "opportunity_id": opportunity_id,
            "status": "already_recovered",
            "opportunity_status": current_status,
        }

    now = int(time.time())
    amount = partial_recovery_amount if partial_recovery_amount is not None else row["amount_at_risk"]
    time_to_recovery = now - row["created_at"]

    # Compare-and-swap. The UPDATE repeats the precondition the SELECT above
    # relied on, so the read-decide-write becomes one atomic statement: SQLite
    # applies a single UPDATE indivisibly, and exactly one concurrent caller
    # can match a row whose status is still neither 'recovered' nor 'stopped'.
    #
    # Without the WHERE guard every concurrent caller passed the check above,
    # every one issued this UPDATE, and every one was told "ok" -- while the
    # row itself ended up looking perfectly clean with a single 'recovered'
    # status. That is what makes this class of bug survive inspection, and how
    # one recovery gets counted N times by any ledger that trusts the return
    # value.
    cursor = conn.execute(
        """
        UPDATE opportunities
        SET status = 'recovered', recovered_bool = 1, recovered_at = ?,
            resolved_at = ?, partial_recovery_amount = ?, resolution_type = 'recovered',
            time_to_recovery = ?
        WHERE opportunity_id = ?
          AND status NOT IN ('recovered', 'stopped')
        """,
        (now, now, amount, time_to_recovery, opportunity_id),
    )
    conn.commit()

    if cursor.rowcount == 0:
        # Lost the race: someone else moved this opportunity to a terminal
        # state between the read and the write. Re-read to report which.
        after = conn.execute(
            "SELECT status FROM opportunities WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        final_status = after["status"] if after else None
        return {
            "opportunity_id": opportunity_id,
            "status": "rejected_stopped" if final_status == "stopped"
                      else "already_recovered",
            "opportunity_status": final_status,
        }

    return {
        "opportunity_id": opportunity_id,
        "status": "ok",
        "opportunity_status": "recovered",
        "recovered_at": now,
        "partial_recovery_amount": amount,
    }
