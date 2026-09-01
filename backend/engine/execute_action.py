"""
execute_action(): writes the compliance outcome to recovery_decisions, and
-- only for outcome=="executed" with a real action_type -- writes a
lifecycle row to recovery_executions and advances opportunities.status.
Single shared function for all three event types. No silent actions --
every call logs a recovery_decisions row, whether executed or blocked.

Phase 1 (Schema Foundation): this is the file where the "decision vs
execution vs business outcome" three-way separation actually gets written.
recovery_decisions.outcome only ever says whether the proposed action was
compliant. recovery_executions.state only ever says where in its lifecycle
the *approved* action currently is. Neither table is ever the place a
"was money recovered" fact gets written -- that lives exclusively on
opportunities, and only mark_opportunity_recovered() (a real payment-success
signal, not a rule-engine action) is permitted to set it -- except for the
terminal "stop" action, which is itself the resolution: the case is closed
as unrecovered by policy, not by a later payment-success event.
"""

import time

# action_type -> opportunities.status when the decision executes
STATUS_MAP = {
    "retry": "recovering",
    "reminder": "recovering",
    "escalate": "escalated",
    "stop": "stopped",
}

# action_type -> recovery_executions.state. escalate/stop are internal
# routing/policy actions with nothing to "dispatch" to a customer, so they
# go straight to a terminal execution state; retry/reminder are the two
# customer-contact actions.
EXECUTION_STATE_MAP = {
    "retry": "executed",
    "reminder": "executed",
    "escalate": "executed",
    "stop": "executed",
}


def execute_action(opportunity: dict, decision: dict, conn) -> dict:
    opportunity_id = opportunity["opportunity_id"]
    now = int(time.time())

    cursor = conn.execute(
        """
        INSERT INTO recovery_decisions
        (opportunity_id, candidate_id, action_type, outcome, reasoning,
         triggered_by, ml_recovery_probability, flag_type, timestamp)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity_id,
            decision["action_type"],
            decision["outcome"],
            decision["reasoning"],
            decision["triggered_by"],
            decision.get("ml_recovery_probability"),
            decision.get("flag_type"),
            now,
        ),
    )
    decision_id = cursor.lastrowid

    # flagged_manual_review is a hard stop: log only, no recovery side effects.
    # Same "log but don't execute" pattern as the existing blocked outcomes.
    if decision["outcome"] == "flagged_manual_review" or decision["action_type"] is None:
        conn.commit()
        return {
            "opportunity_id": opportunity_id,
            "decision_id": decision_id,
            "action_type": decision["action_type"],
            "outcome": decision["outcome"],
            "reasoning": decision["reasoning"],
        }

    # only advance opportunity state when the action was actually executed
    if decision["outcome"] == "executed":
        action_type = decision["action_type"]

        conn.execute(
            """
            INSERT INTO recovery_executions (decision_id, state, executed_at, channel)
            VALUES (?, ?, ?, NULL)
            """,
            (decision_id, EXECUTION_STATE_MAP.get(action_type, "executed"), now),
        )

        new_status = STATUS_MAP.get(action_type, opportunity["status"])

        # "stop" is a terminal policy resolution -- the case is closed as
        # unrecovered by rule (max attempts exhausted), not by a payment
        # event. Set the same business-outcome fields
        # mark_opportunity_recovered() would set on the positive path, so
        # every closed opportunity ends up with a consistent, queryable
        # resolution regardless of which path closed it. "escalate" is
        # deliberately NOT resolved here -- it hands off to a human queue,
        # outcome still pending.
        if action_type == "stop":
            conn.execute(
                """
                UPDATE opportunities
                SET status = ?, resolved_at = ?, recovered_bool = 0,
                    partial_recovery_amount = 0, resolution_type = 'stopped'
                WHERE opportunity_id = ?
                """,
                (new_status, now, opportunity_id),
            )
        else:
            conn.execute(
                "UPDATE opportunities SET status = ? WHERE opportunity_id = ?",
                (new_status, opportunity_id),
            )

    conn.commit()

    return {
        "opportunity_id": opportunity_id,
        "decision_id": decision_id,
        "action_type": decision["action_type"],
        "outcome": decision["outcome"],
        "reasoning": decision["reasoning"],
    }
