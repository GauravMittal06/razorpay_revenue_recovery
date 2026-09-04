"""
observe_outcome(): Phase 6 -- THE single path by which a business outcome is
ever written to an opportunity.

`recovered`, a partial recovery, `stopped`, `lost` -- whatever the source, and
whether that source is a human clicking "confirm" in the console, the
executor's attempt ceiling closing a case by policy, or (in a real
integration) an upstream payment-success event, the write happens here and
nowhere else.

--------------------------------------------------------------------------
Why one path, and not two that agree
--------------------------------------------------------------------------
Before Phase 6 there were three writers of the business-outcome columns:
`mark_opportunity_recovered()`, `execute_action()`'s terminal `stop` branch,
and the bulk seed loader. Two of those are live code, and they had already
drifted -- `mark_opportunity_recovered()` guards its write with a
compare-and-swap against a concurrent terminal transition, `execute_action()`
did not.

That divergence is the failure this module exists to prevent, and the reason
it matters more here than in an ordinary refactor: Phase 7 computes an
incremental-Rs figure by comparing recovery rates across the two experiment
arms. A second write route that resolved opportunities under slightly
different rules would bias that comparison in a way no downstream check could
detect, because the resulting rows look perfectly well-formed.

The bulk seed loader in `db.py` stays outside this module by name, not by
accident: it constructs a world, it does not observe one. It is excluded
explicitly in the static gate.

--------------------------------------------------------------------------
Authority boundary (permanent, do not weaken)
--------------------------------------------------------------------------
  - This module imports NOTHING with execution authority. It records what
    happened; it never decides what may happen next. Checked mechanically by
    tests/test_permanent_gates.py, the same way optimize.py and
    assign_experiment_group.py are.
  - Its only write is to `opportunities`, and only to the outcome columns
    plus `status`. It never writes recovery_decisions, recovery_executions or
    recovery_candidates -- an observer that could edit the compliance record
    could rewrite the history its own numbers are computed from.
  - It is NOT experiment-aware, and this is deliberate. A control opportunity
    can and must be able to recover -- that is the entire point of a control
    arm. An outcome writer that consulted `experiment_assignment` could
    silently suppress control outcomes and drive the measured incremental
    effect to exactly the number the system wanted.

--------------------------------------------------------------------------
Concurrency
--------------------------------------------------------------------------
The UPDATE carries its own precondition in the WHERE clause, so the
read-decide-write is one atomic statement. This is adopted unchanged from
`mark_opportunity_recovered()`, whose comment explains the failure it
prevents: without the guard every concurrent caller passes the pre-check,
every one issues the UPDATE, every one is told "ok", and the row still ends up
looking clean -- which is how one recovery gets counted N times by any ledger
that trusts the return value.
"""

import time

from backend.db import db as _db


# A terminal state is terminal. Once an opportunity is resolved, a second
# observation does not overwrite it -- it is reported as a no-op with the
# existing resolution, so a caller can tell "I resolved this" from "this was
# already resolved" without guessing.
TERMINAL_STATUSES = ("recovered", "stopped")

# resolution_type -> the opportunities.status it implies. The two are not the
# same field and are not collapsed: `status` is the lifecycle position the
# rest of the system filters on, `resolution_type` is how the case ended.
#
# `lost` maps to status `stopped` because `status` is a lifecycle position and
# the system has only one closed-without-recovery lifecycle state. The two stay
# distinguishable through resolution_type, which is the field that carries HOW
# a case ended.
#
# `escalated_resolved` HAS NO PRODUCER. It was named in the Phase 1 column
# comment and no code path has ever written it. Mapping it to a recovery here
# is an ASSUMPTION -- "the escalation was resolved" most naturally means the
# money came back, but a human could equally close an escalation without
# recovering anything. It is recorded as an assumption rather than quietly
# chosen: if a producer is ever added, this mapping must be confirmed by
# whoever adds it, not inherited from here. Flagged in PHASE6_NOTES.md.
STATUS_FOR_RESOLUTION = {
    "recovered": "recovered",
    "stopped": "stopped",
    "lost": "stopped",
    "escalated_resolved": "recovered",
}


def observe_outcome(opportunity_id: str, conn, *, resolution: str,
                    source: str, partial_recovery_amount: int = None,
                    now: int = None) -> dict:
    """
    Record the observed business outcome of one opportunity.

    Returns:
      {"opportunity_id": str, "status": str,
       "resolution_type": str, "recovered_bool": int,
       "partial_recovery_amount": int, "recovered_at": int|None,
       "time_to_recovery": int|None, "outcome_source": str,
       "result": "observed"|"already_resolved"|"opportunity_not_found"}

    `resolution` must be in db.RESOLUTION_TYPES and `source` in
    db.OUTCOME_SOURCES. Both are keyword-only and both are required: a caller
    that could omit the source would make "exactly one ingestion path" true in
    the code and unauditable in the data.

    `partial_recovery_amount` is meaningful only for a recovery. Omitted, a
    recovery is recorded in full. There is no `partially_recovered` resolution
    -- a partial is `recovered` with an amount below amount_at_risk, and that
    inference is exact (ruling 2026-09-04).
    """
    if resolution not in _db.RESOLUTION_TYPES:
        raise ValueError(
            f"{resolution!r} is not in the closed resolution vocabulary "
            f"{_db.RESOLUTION_TYPES}")
    if source not in _db.OUTCOME_SOURCES:
        raise ValueError(
            f"{source!r} is not in the closed outcome-source vocabulary "
            f"{_db.OUTCOME_SOURCES}")

    row = conn.execute(
        "SELECT opportunity_id, status, amount_at_risk, created_at, "
        "resolution_type FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()
    if row is None:
        return {"opportunity_id": opportunity_id,
                "result": "opportunity_not_found"}

    # A fast path and a clear message, not the safety mechanism. The guard is
    # the WHERE clause below: between this read and that write another caller
    # can commit a resolution, and a check up here cannot see it.
    if row["status"] in TERMINAL_STATUSES:
        return _already_resolved(conn, opportunity_id)

    observed_at = int(time.time()) if now is None else int(now)
    recovered = resolution in ("recovered", "escalated_resolved")

    if recovered:
        amount = (row["amount_at_risk"] if partial_recovery_amount is None
                  else int(partial_recovery_amount))
        recovered_at = observed_at
        time_to_recovery = observed_at - row["created_at"]
    else:
        # `stopped` and `lost` both mean no money came back. The amount is 0
        # rather than NULL so that SUM(partial_recovery_amount) over resolved
        # opportunities is the recovered total without a COALESCE every caller
        # would have to remember.
        amount = 0
        recovered_at = None
        time_to_recovery = None

    cursor = conn.execute(
        """
        UPDATE opportunities
        SET status = ?, resolved_at = ?, recovered_bool = ?,
            partial_recovery_amount = ?, recovered_at = ?,
            time_to_recovery = ?, resolution_type = ?, outcome_source = ?
        WHERE opportunity_id = ?
          AND status NOT IN ('recovered', 'stopped')
        """,
        (STATUS_FOR_RESOLUTION[resolution], observed_at, 1 if recovered else 0,
         amount, recovered_at, time_to_recovery, resolution, source,
         opportunity_id),
    )
    conn.commit()

    if cursor.rowcount == 0:
        # Lost the race: another caller reached a terminal state between the
        # read and the write.
        return _already_resolved(conn, opportunity_id)

    return {
        "opportunity_id": opportunity_id,
        "status": STATUS_FOR_RESOLUTION[resolution],
        "resolution_type": resolution,
        "recovered_bool": 1 if recovered else 0,
        "partial_recovery_amount": amount,
        "recovered_at": recovered_at,
        "time_to_recovery": time_to_recovery,
        "outcome_source": source,
        "result": "observed",
    }


def _already_resolved(conn, opportunity_id):
    row = conn.execute(
        "SELECT status, resolution_type, recovered_bool, "
        "partial_recovery_amount, recovered_at, time_to_recovery, "
        "outcome_source FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()
    return {"opportunity_id": opportunity_id, "result": "already_resolved",
            **(dict(row) if row else {})}


def is_partial_recovery(opportunity_row) -> bool:
    """
    The declared derivation of a partial recovery, in one place.

    Partial recovery has no resolution_type of its own (ruling 2026-09-04):
    adding one would force every "was this recovered" query to match two
    values, and any query that forgot the second would under-count. The
    inference is exact, so the value would add no information -- but leaving
    it as an ad-hoc comparison repeated at each call site is how a definition
    drifts. It lives here.
    """
    if opportunity_row["resolution_type"] != "recovered":
        return False
    amount = opportunity_row["partial_recovery_amount"]
    at_risk = opportunity_row["amount_at_risk"]
    if amount is None or at_risk is None:
        return False
    return amount < at_risk
