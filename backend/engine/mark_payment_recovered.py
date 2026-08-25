"""
mark_payment_recovered(): records a real payment-success event.
Separate from rule-engine authority -- decide_action()/execute_action()
own compliance/actions; this owns the ground-truth fact that money was
actually recovered. Not called by any live trigger yet (Live Agent
Console wiring is a separate future step).
"""

import time


def mark_payment_recovered(payment_id: str, conn) -> dict:
    row = conn.execute(
        "SELECT id, recovery_status FROM payments WHERE id = ?", (payment_id,)
    ).fetchone()

    if row is None:
        return {
            "payment_id": payment_id,
            "status": "payment_not_found",
        }

    current_status = row["recovery_status"]

    if current_status == "stopped":
        return {
            "payment_id": payment_id,
            "status": "rejected_stopped",
            "recovery_status": current_status,
        }

    if current_status == "recovered":
        return {
            "payment_id": payment_id,
            "status": "already_recovered",
            "recovery_status": current_status,
        }

    now = int(time.time())
    conn.execute(
        "UPDATE payments SET recovery_status = 'recovered', recovered_at = ? WHERE id = ?",
        (now, payment_id),
    )
    conn.commit()

    return {
        "payment_id": payment_id,
        "status": "ok",
        "recovery_status": "recovered",
        "recovered_at": now,
    }