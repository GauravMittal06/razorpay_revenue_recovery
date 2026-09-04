"""
assign_experiment_group(): Phase 6 -- randomized assignment to the live
experiment, at opportunity-creation time.

This is the module the whole incremental-Rs claim rests on. If assignment is
not genuinely random, or not recorded, every number Phase 7 computes is
decoration. So this file does exactly one thing and is bounded accordingly.

--------------------------------------------------------------------------
Authority boundary (permanent, do not weaken)
--------------------------------------------------------------------------
  - This module imports NOTHING with execution authority: not
    engine.decide_action, not engine.execute_action, not engine.core_loop,
    not api.actions, not observe_outcome. Checked mechanically by
    tests/test_permanent_gates.py, the same way optimize.py is.
  - Its ONLY write is `INSERT INTO experiment_assignment`. It never writes
    opportunities, payments, recovery_decisions, recovery_executions or
    recovery_candidates. Assignment records which arm an opportunity is in;
    it does not decide, execute, or resolve anything.
  - It never sets the `allowed` permission bit and holds no opinion about
    whether an action may fire. Control suppression is enforced by the rule
    engine reading this table, not by this module reaching forward into the
    pipeline.

--------------------------------------------------------------------------
Where the randomness comes from
--------------------------------------------------------------------------
Nowhere in this file. The draw is `phase6_config.assigned_group()`, a pure
function of the opportunity id and a locked salt, and the reasoning for that
choice (ruling 2026-09-04, option R2) lives with the constant rather than
here. This module is the persistence half only.

That split is deliberate. It means the group an opportunity is in can be
recomputed by anyone from its id plus the committed salt, without this
module, without the database, and years later -- which is what makes an
assignment auditable rather than merely asserted.

--------------------------------------------------------------------------
Assignment happens ONCE, and the guarantee is the schema's
--------------------------------------------------------------------------
`experiment_assignment.opportunity_id` is the PRIMARY KEY. A second call for
the same opportunity is a no-op that returns the existing row -- it never
re-randomizes, and it never updates `assigned_at`.

This matters more than idempotency usually does. Re-randomizing a live
opportunity mid-flight would silently move it between arms after it had
already been treated (or deliberately not treated), which corrupts the
experiment in a way no downstream check could detect: the row would look
perfectly consistent afterwards. The SELECT below is a cheap fast path; the
UNIQUE guarantee is the primary key, and the IntegrityError handler is what
makes the two agree under concurrency.
"""

import sqlite3
import time

from backend.engine import phase6_config as _cfg


def _existing(conn, opportunity_id):
    row = conn.execute(
        'SELECT opportunity_id, "group", assigned_at, assignment_method '
        "FROM experiment_assignment WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def assign_experiment_group(opportunity_id: str, conn, now: int = None) -> dict:
    """
    Assign one opportunity to `control` or `treatment`, once, and record it.

    Returns:
      {"opportunity_id": str, "group": "control"|"treatment",
       "assigned_at": int, "assignment_method": str,
       "status": "assigned"|"already_assigned"|"opportunity_not_found"}

    `now` exists so a caller that already has a creation timestamp can record
    assignment at the same instant rather than a few milliseconds later. The
    balance gate compares covariates fixed at creation, so this does not
    affect it; it keeps the audit trail honest.
    """
    existing = _existing(conn, opportunity_id)
    if existing is not None:
        return {**existing, "status": "already_assigned"}

    assigned_at = int(time.time()) if now is None else int(now)
    record = {
        "opportunity_id": opportunity_id,
        "group": _cfg.assigned_group(opportunity_id),
        "assigned_at": assigned_at,
        "assignment_method": _cfg.assignment_method_record(),
    }

    try:
        conn.execute(
            'INSERT INTO experiment_assignment '
            '(opportunity_id, "group", assigned_at, assignment_method) '
            "VALUES (:opportunity_id, :group, :assigned_at, :assignment_method)",
            record,
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Two constraints can land here and they need opposite answers, so
        # the exception type alone is not enough to act on:
        #
        #   * the PRIMARY KEY -- a concurrent caller won the race between the
        #     SELECT above and this INSERT. Resolve exactly as the fast path
        #     would have: return the row that actually won. Both callers then
        #     agree on the group, which is the whole point.
        #   * the FOREIGN KEY -- there is no such opportunity. Nothing to
        #     return, and inventing an assignment for a row that does not
        #     exist would put an orphan in the experiment population.
        winner = _existing(conn, opportunity_id)
        if winner is not None:
            return {**winner, "status": "already_assigned"}
        return {
            "opportunity_id": opportunity_id,
            "group": None,
            "assigned_at": None,
            "assignment_method": None,
            "status": "opportunity_not_found",
        }

    return {**record, "status": "assigned"}


def get_assignment(opportunity_id: str, conn) -> dict:
    """
    The recorded assignment, or None if this opportunity is not in the
    experiment.

    Read-only, and deliberately does NOT fall back to recomputing the group
    from the id. An opportunity with no row is not in the experiment (ruling
    2026-09-04, `phase6_config.UNASSIGNED_IS_SUPPRESSED`), and a reader that
    silently derived a group for it would quietly enrol the entire
    pre-Phase-6 population into an experiment it was never randomized for.
    """
    return _existing(conn, opportunity_id)
