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

from backend.data_factory.candidate_generation import TIMING_HOURS
from backend.db.db import EXECUTION_STATES
from backend.engine.phase5_config import (EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS,
                                          IMMEDIATE_TIMING_HOURS,
                                          MAX_SCHEDULE_HORIZON_HOURS,
                                          SECONDS_PER_HOUR)

# action_type -> opportunities.status when the decision executes
STATUS_MAP = {
    "retry": "recovering",
    "reminder": "recovering",
    # Phase 5: payment_link has been a first-class optimizer candidate since
    # Phase 4, with its own cost term and eligibility rules, but had no
    # executor support -- so the optimizer's top pick could be structurally
    # undispatchable. EXECUTION_PLAN.md:206 names it in the executable
    # vocabulary. Like retry and reminder it is customer contact, so it leaves
    # the opportunity in `recovering`.
    "payment_link": "recovering",
    "escalate": "escalated",
    "stop": "stopped",
}

# Actions whose execution closes the opportunity as a terminal policy
# resolution. Kept explicit so the resolution block below can never fire for a
# merely-scheduled action, only for one that has actually executed.
TERMINAL_ACTIONS = ("stop",)

# Phase 5: an approved action is either dispatched now or queued for later.
# Which one is decided here, in the executor, from the timing attribute of the
# candidate the rule engine approved -- "the executor decides timing of an
# already-approved action, never whether to act" (EXECUTION_PLAN.md:83).
#
# Nothing about compliance is reconsidered at this point. execute_action()
# writes what decide_action() decided; it does not re-check cooldown, contact
# hours or eligibility, and must never gain such a check.
SCHEDULED_STATE = "scheduled"

# action_type -> recovery_executions.state. escalate/stop are internal
# routing/policy actions with nothing to "dispatch" to a customer, so they
# go straight to a terminal execution state; retry/reminder are the two
# customer-contact actions.
EXECUTION_STATE_MAP = {
    "retry": "executed",
    "reminder": "executed",
    "payment_link": "executed",
    "escalate": "executed",
    "stop": "executed",
}


def _approved_candidate(conn, candidate_id):
    """The recovery_candidates row the rule engine approved, or None."""
    if candidate_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM recovery_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return dict(row) if row else None


def _schedule_offset_hours(candidate) -> float:
    """
    How far ahead the approved candidate should fire, in hours.

    Read from the candidate's own `timing`, mapped through the shared
    generator's TIMING_HOURS -- the same table the optimizer scored against, so
    the executor cannot develop a second opinion about what "4h" means. A
    decision with no candidate behind it (every pre-Phase-5 caller) has no
    timing and fires immediately, which is the existing behaviour.
    """
    if not candidate:
        return IMMEDIATE_TIMING_HOURS
    timing = candidate.get("timing")
    if timing is None:
        return IMMEDIATE_TIMING_HOURS
    if timing not in TIMING_HOURS:
        raise ValueError(
            f"candidate {candidate.get('candidate_id')} carries timing "
            f"{timing!r}, which is not in the shared generator's TIMING_HOURS "
            f"({sorted(TIMING_HOURS)})")
    hours = TIMING_HOURS[timing]
    if hours > MAX_SCHEDULE_HORIZON_HOURS:
        raise ValueError(
            f"timing {timing!r} is {hours}h, beyond the declared scheduling "
            f"horizon of {MAX_SCHEDULE_HORIZON_HOURS}h")
    return hours


def execute_action(opportunity: dict, decision: dict, conn) -> dict:
    opportunity_id = opportunity["opportunity_id"]
    now = int(time.time())

    cursor = conn.execute(
        """
        INSERT INTO recovery_decisions
        (opportunity_id, candidate_id, action_type, outcome, reasoning,
         triggered_by, ml_recovery_probability, flag_type, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity_id,
            # Phase 5: links the decision to the exact candidate the rule
            # engine approved. NULL for every hardcoded-path decision, which
            # is what the UNIQUE index on this column is built to allow.
            # A candidate_id that does not exist raises a FOREIGN KEY error
            # rather than being silently coerced to NULL -- an invented
            # candidate reference is a defect, not something to paper over.
            decision.get("candidate_id"),
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
    #
    # Phase 5 adds the evaluable-but-not-executable actions to this path. A
    # `do_nothing` decision previously fell through to the executed branch,
    # where EXECUTION_STATE_MAP.get(action, "executed") hit its default and
    # wrote a recovery_executions row asserting a dispatch that never
    # happened -- a fabricated execution record, and a downstream inflation of
    # any count of actions taken. Deciding to act by not acting is a real
    # decision and is logged as one; it is not an execution.
    #
    # Keyed off the declared vocabulary rather than the literal "do_nothing",
    # so an action added to that list is covered here automatically.
    if (decision["outcome"] == "flagged_manual_review"
            or decision["action_type"] is None
            or decision["action_type"] in EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS):
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

        candidate = _approved_candidate(conn, decision.get("candidate_id"))
        offset_hours = _schedule_offset_hours(candidate)

        # An approved action either fires now or waits. Either way this is one
        # row, mutated in place by the dispatcher later -- never a second row,
        # which the UNIQUE index on decision_id enforces.
        if offset_hours > IMMEDIATE_TIMING_HOURS:
            state = SCHEDULED_STATE
            scheduled_for = now + int(offset_hours * SECONDS_PER_HOUR)
            executed_at = None
        else:
            state = EXECUTION_STATE_MAP.get(action_type, "executed")
            scheduled_for = None
            executed_at = now

        if state not in EXECUTION_STATES:
            raise ValueError(
                f"{state!r} is not in the closed execution-state vocabulary "
                f"{EXECUTION_STATES}")

        conn.execute(
            """
            INSERT INTO recovery_executions
            (decision_id, state, scheduled_for, executed_at, channel)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (decision_id, state, scheduled_for, executed_at),
        )

        # `selected` means the rule engine approved this candidate for
        # execution. The optimizer writes every row with selected=0 and has no
        # authority to grant it; this is the one place it is set.
        if candidate is not None:
            conn.execute(
                "UPDATE recovery_candidates SET selected = 1 WHERE candidate_id = ?",
                (decision["candidate_id"],),
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
        # `state != SCHEDULED_STATE` is defensive rather than reachable today:
        # `stop` is never an optimizer candidate, so it always fires
        # immediately. It is guarded anyway because closing an opportunity as
        # unrecovered on the strength of an action that has not happened yet
        # would be a business-outcome write with nothing behind it.
        if action_type in TERMINAL_ACTIONS and state != SCHEDULED_STATE:
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
