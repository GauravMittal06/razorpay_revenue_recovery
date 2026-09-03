"""
dispatch_scheduled.py -- Phase 5 / W6. The periodic sweep that fires actions
which were approved earlier and queued for a later time.

Structurally the same shape as core_loop.run_cycle(): open a connection,
select a work set, loop over it, close. It is the ONLY component permitted to
advance a scheduled execution to dispatched/executed (EXECUTION_PLAN.md:206).

WHAT THIS DECIDES, AND WHAT IT MUST NEVER DECIDE
    It decides *when* an already-approved action fires. It never decides
    *whether* one may (EXECUTION_PLAN.md:83). decide_action() remains the sole
    compliance authority; this module calls back into it and obeys the answer,
    and re-implements not a single rule of its own.


THE HARD CONSTRAINT THIS MODULE IS BUILT AROUND
    execute_action() is NOT idempotent at the call level. Calling it twice
    with the same decision mints TWO decisions and TWO executions, because
    each call inserts a fresh recovery_decisions row to hang the execution
    off; the UNIQUE index on recovery_executions.decision_id cannot stop that,
    since the second row has a different decision_id. Measured and pinned at
    tests/test_phase5_execution.py::
        test_calling_execute_action_twice_creates_two_decisions_not_one

    So this module NEVER calls execute_action(), and never INSERTs into
    recovery_decisions or recovery_executions. Its entire write surface
    against the lifecycle is compare-and-swap UPDATEs of the form

        UPDATE recovery_executions SET state = <next>
        WHERE execution_id = ? AND state = <expected>

    cursor.rowcount == 1 means this sweep won the row; 0 means another sweep
    already advanced it, and this one then does nothing at all -- no message,
    no second side effect. That predicate, not the UNIQUE index, is what makes
    duplicate dispatch safe: the index prevents a second execution ROW, but
    only the CAS prevents a second MESSAGE to the customer.

    Both properties are enforced mechanically, not by convention, in
    tests/test_phase5_dispatch.py.


WHY THE OPTIMIZER IS NOT HERE
    It is not called at all -- inside the lock or outside it. The action was
    ranked, chosen and authorised at schedule time; re-ranking at dispatch
    would be re-deciding. This also means the ~650ms optimize_opportunity()
    call can never land inside opportunity_lock's ~6ms hold, which
    opportunity_lock.py documents as the thing that must never happen.
    phase5_config.OPTIMIZER_ENABLED_BY_ENTRY_POINT["dispatch"] is therefore
    inert by design (ruling A9).


THE LOCK BOUNDARY
    Selection runs outside the lock; a stale read there is harmless because
    the CAS inside the lock is the authority. Revalidation and the claim are
    inside one lock hold. Message delivery is outside it -- it is an outbound
    side effect that calls the LLM, and holding a write lock across it would
    serialise the whole sweep on message generation, the same reasoning
    core_loop.py already records.


AT-MOST-ONCE, DELIBERATELY (ruling A4)
    The sweep selects only state='scheduled'. A row left in 'dispatched' --
    because delivery raised, or the process died between the claim and the
    completion -- is therefore never retried by a later sweep. That is chosen,
    not overlooked: for customer contact, failing to send is recoverable by a
    human reading the queue, while sending twice is not recoverable at all. A
    stuck row is visible as state='dispatched' with executed_at IS NULL, and
    listing them is what `stuck_dispatches()` below is for. There is no
    alerting in this phase; see PHASE5_NOTES.md for the tracked item.
"""

import time

from backend.db.db import get_connection
from backend.engine import phase5_config as _phase5
from backend.engine.classify import classify
from backend.engine.decide_action import decide_action
from backend.engine.deliver_message import deliver_recovery_message
from backend.engine.opportunity_lock import opportunity_lock

# Lifecycle states this module moves rows between. Named here so the CAS
# predicates below cannot silently disagree with each other.
CLAIMABLE_STATE = "scheduled"
CLAIMED_STATE = "dispatched"
COMPLETED_STATE = "executed"
ABANDONED_STATE = "cancelled"

# Opportunity statuses that mean the case is closed and a queued action must
# not fire. Ruling A3, 2026-09-03.
#
# This is a LIVENESS precondition, not a compliance rule -- it asks "does this
# case still exist to be acted on", not "is this action permitted", which is
# decide_action()'s question and stays there. It is needed because
# decide_action() reads compliance history from recovery_decisions and never
# looks at opportunities.status, so an opportunity closed by a path that
# writes no decision row -- mark_opportunity_recovered() being the live
# example -- is invisible to it. Without this the dispatcher would cheerfully
# send a payment reminder to a customer who has already paid.
TERMINAL_OPPORTUNITY_STATUSES = ("recovered", "stopped", "escalated")


def _due_executions(conn, now):
    """
    Scheduled executions whose time has come, oldest first.

    state='scheduled' is both the filter and the claim predicate. Selecting on
    state rather than only on time is what keeps a 'pending', 'cancelled' or
    already-'dispatched' row with a past scheduled_for out of the sweep.
    """
    rows = conn.execute(
        """
        SELECT e.execution_id, e.decision_id, e.scheduled_for,
               d.opportunity_id, d.action_type, d.candidate_id
        FROM recovery_executions e
        JOIN recovery_decisions d ON d.decision_id = e.decision_id
        WHERE e.state = ?
          AND e.scheduled_for IS NOT NULL
          AND e.scheduled_for <= ?
        ORDER BY e.scheduled_for ASC, e.execution_id ASC
        """,
        (CLAIMABLE_STATE, now + _phase5.DISPATCH_DUE_GRACE_SECONDS),
    ).fetchall()
    return [dict(r) for r in rows]


def _advance(conn, execution_id, expected, nxt, executed_at=None, reason=None):
    """
    Compare-and-swap one execution row from `expected` to `nxt`.

    Returns True only if this caller made the transition. A False return means
    somebody else got there first, and the caller must then take NO further
    action for this row -- that is the whole idempotency mechanism.
    """
    cursor = conn.execute(
        """
        UPDATE recovery_executions
        SET state = ?, executed_at = COALESCE(?, executed_at),
            state_reason = COALESCE(?, state_reason)
        WHERE execution_id = ? AND state = ?
        """,
        (nxt, executed_at, reason, execution_id, expected),
    )
    return cursor.rowcount == 1


def _latest_payment(opportunity_id, conn):
    row = conn.execute(
        "SELECT * FROM payments WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def _opportunity(opportunity_id, conn):
    row = conn.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def _still_permitted(opportunity, due, conn, now):
    """
    May this queued action still fire? Returns (permitted, reason).

    Two independent questions, in order:

    1. Does the case still exist to be acted on (ruling A3)? A liveness
       precondition read straight off opportunities.status.
    2. Is the action still compliant *at its due time*? Answered by calling
       back into decide_action() -- never by re-checking any rule here, which
       would make this a second compliance authority.

    `as_of=now` is what makes (2) meaningful. Both of decide_action()'s
    contact-window implementations read the local hour of the opportunity's
    created_at, which does not change between schedule time and due time, so
    without an evaluation clock the revalidation returned the identical
    verdict it gave at scheduling and the 9pm-8am contact ban was
    unenforceable for every scheduled action (ruling A2).
    """
    if opportunity is None:
        return False, "opportunity no longer exists"

    status = opportunity.get("status")
    if status in TERMINAL_OPPORTUNITY_STATUSES:
        return False, f"opportunity is {status}; queued action abandoned"

    if not _phase5.DISPATCH_REVALIDATES_VIA_DECIDE_ACTION:
        return True, None

    latest_payment = _latest_payment(opportunity["opportunity_id"], conn)
    classification = classify(
        opportunity["event_type"],
        latest_payment.get("error_reason") if latest_payment
        else opportunity.get("root_cause"),
    )
    verdict = decide_action(opportunity, classification, conn,
                            latest_payment=latest_payment,
                            as_of=now if _phase5.DISPATCH_EVALUATES_WINDOW_AT_DUE_TIME
                            else None)

    if not verdict["allowed"]:
        return False, f"{verdict['outcome']}: {verdict['reasoning']}"

    # The rule engine may still permit action while having moved to a
    # different one -- an auto-escalation or the attempt ceiling's stop fires
    # on its own terms and supersedes whatever was queued. Firing the queued
    # action anyway would execute an action the authority no longer selects.
    if verdict["action_type"] != due["action_type"]:
        return False, (f"rule engine now selects {verdict['action_type']!r}, "
                       f"not the queued {due['action_type']!r}")

    return True, None


def dispatch_due_execution(conn, due, now):
    """
    Advance one due execution. Returns a result dict describing what happened.

    Never calls execute_action(); never inserts any row. See the module
    docstring for why both of those are hard constraints rather than choices.
    """
    execution_id = due["execution_id"]
    opportunity_id = due["opportunity_id"]

    with opportunity_lock(conn):
        opportunity = _opportunity(opportunity_id, conn)
        permitted, reason = _still_permitted(opportunity, due, conn, now)

        if not permitted:
            # Abandoned, with the reason recorded -- "every action the system
            # takes or declines to take is logged with a reason".
            _advance(conn, execution_id, CLAIMABLE_STATE, ABANDONED_STATE,
                     reason=reason)
            return {"execution_id": execution_id,
                    "opportunity_id": opportunity_id,
                    "state": ABANDONED_STATE, "dispatched": False,
                    "reason": reason}

        # The claim. If another sweep already took this row, rowcount is 0 and
        # we stop here without sending anything.
        if not _advance(conn, execution_id, CLAIMABLE_STATE, CLAIMED_STATE):
            return {"execution_id": execution_id,
                    "opportunity_id": opportunity_id,
                    "state": None, "dispatched": False,
                    "reason": "already claimed by another dispatcher run"}

        classification = classify(
            opportunity["event_type"],
            (_latest_payment(opportunity_id, conn) or {}).get("error_reason")
            or opportunity.get("root_cause"),
        )

    # Outside the lock: the outbound side effect. The row is in 'dispatched'
    # here, which is the state deliver_recovery_message() sends in -- the row
    # completes to 'executed' only after the send has returned, so 'executed'
    # never claims a contact that did not happen.
    #
    # RULING A4, the error path. If delivery raises -- an LLM failure, a
    # persistence failure, anything -- the row is LEFT in 'dispatched' with
    # executed_at NULL and no later sweep retries it, because _due_executions()
    # selects only 'scheduled'. That is deliberate: a claimed row may already
    # have reached the customer, so an automatic retry is exactly the
    # duplicate contact the CAS exists to prevent. The failure is not
    # swallowed -- it is returned in `reason` and the row is enumerable
    # through stuck_dispatches().
    decision = {
        "action_type": due["action_type"],
        "outcome": "executed",
        "reasoning": "dispatched by the scheduled sweep",
        "triggered_by": "rule",
    }
    try:
        delivered = deliver_recovery_message(
            opportunity, classification, decision, conn,
            latest_payment=_latest_payment(opportunity_id, conn),
            decision_id=due["decision_id"])
    except Exception as exc:
        return {"execution_id": execution_id, "opportunity_id": opportunity_id,
                "state": CLAIMED_STATE, "dispatched": True, "delivered": False,
                "reason": f"delivery raised after dispatch, row left stuck "
                          f"in {CLAIMED_STATE!r} for operator review: {exc!r}"}

    with opportunity_lock(conn):
        _advance(conn, execution_id, CLAIMED_STATE, COMPLETED_STATE,
                 executed_at=now)

    return {"execution_id": execution_id, "opportunity_id": opportunity_id,
            "state": COMPLETED_STATE, "dispatched": True,
            "delivered": bool(delivered and delivered.get("delivered")),
            "reason": None}


def stuck_dispatches(conn):
    """
    Executions claimed but never completed -- the at-most-once residue.

    Not alerting, and not a retry queue: firing these automatically is exactly
    the duplicate contact the CAS exists to prevent, because a claimed row may
    already have reached the customer. This is the operator's view of them.
    """
    rows = conn.execute(
        "SELECT * FROM recovery_executions WHERE state = ? AND executed_at IS NULL",
        (CLAIMED_STATE,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_dispatch_cycle(now=None, conn=None):
    """
    One sweep over everything currently due.

    `now` is injectable so tests can place the clock precisely rather than
    sleeping; `conn` is injectable so a caller already holding one (and the
    concurrency tests) can reuse it.
    """
    now = int(time.time()) if now is None else int(now)
    owns_connection = conn is None
    conn = get_connection() if owns_connection else conn

    try:
        results = [dispatch_due_execution(conn, due, now)
                   for due in _due_executions(conn, now)]
    finally:
        if owns_connection:
            conn.close()
    return results


if __name__ == "__main__":
    # Run as: python -m backend.engine.dispatch_scheduled
    for r in run_dispatch_cycle():
        state = r["state"] or "unchanged"
        print(f"{r['opportunity_id']} | execution {r['execution_id']} | "
              f"{state:10s} | {r['reason'] or 'dispatched'}")
