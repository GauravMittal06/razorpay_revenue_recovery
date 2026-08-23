"""
execute_action(): writes decision to recovery_actions (audit log) and
updates payments.recovery_status. Single shared function for all
three event types. No silent actions -- every call logs a row,
whether executed or blocked.
"""

import time

STATUS_MAP = {
    "retry": "recovering",
    "reminder": "recovering",
    "escalate": "escalated",
    "stop": "stopped",
}


def execute_action(payment: dict, decision: dict, conn) -> dict:
    payment_id = payment["id"]
    now = int(time.time())

    conn.execute(
        """
        INSERT INTO recovery_actions
        (payment_id, action_type, timestamp, triggered_by, reasoning, outcome, ml_recovery_probability, flag_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payment_id,
            decision["action_type"],
            now,
            decision["triggered_by"],
            decision["reasoning"],
            decision["outcome"],
            decision.get("ml_recovery_probability"),
            decision.get("flag_type"),
        ),
    )

    # flagged_manual_review is a hard stop: log only, no recovery side effects.
    # Same "log but don't execute" pattern as the existing blocked outcomes.
    if decision["outcome"] == "flagged_manual_review" or decision["action_type"] is None:
        conn.commit()
        return {
            "payment_id": payment_id,
            "action_type": decision["action_type"],
            "outcome": decision["outcome"],
            "reasoning": decision["reasoning"],
        }

    # only advance recovery_status when the action was actually executed
    if decision["outcome"] == "executed":
        new_status = STATUS_MAP.get(decision["action_type"], payment["recovery_status"])
        conn.execute(
            "UPDATE payments SET recovery_status = ? WHERE id = ?",
            (new_status, payment_id),
        )

    conn.commit()

    return {
        "payment_id": payment_id,
        "action_type": decision["action_type"],
        "outcome": decision["outcome"],
        "reasoning": decision["reasoning"],
    }