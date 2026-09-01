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

    conn.execute(
        """
        UPDATE opportunities
        SET status = 'recovered', recovered_bool = 1, recovered_at = ?,
            resolved_at = ?, partial_recovery_amount = ?, resolution_type = 'recovered',
            time_to_recovery = ?
        WHERE opportunity_id = ?
        """,
        (now, now, amount, time_to_recovery, opportunity_id),
    )
    conn.commit()

    return {
        "opportunity_id": opportunity_id,
        "status": "ok",
        "opportunity_status": "recovered",
        "recovered_at": now,
        "partial_recovery_amount": amount,
    }
