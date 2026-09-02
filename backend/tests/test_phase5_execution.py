"""
Phase 5 / W5 -- the write side of execute_action().

This is the first Phase 5 step that can produce a real side effect: a
persisted execution row, an opportunity whose status moved, a candidate marked
selected. The tests here are correspondingly about what ends up in the
database, not about what a function returned.

Three things are established:

* the execution lifecycle -- an approved action is either dispatched now or
  queued, and a queued one carries scheduled_for and no executed_at;
* the decision/candidate link -- candidate_id on the decision, selected=1 on
  the candidate, and no silent coercion of a bad reference;
* that a decision which is not an execution never writes an execution row.
"""

import sqlite3
import time

import pytest

from backend.data_factory.candidate_generation import TIMING_HOURS
from backend.db.db import EXECUTION_STATES
from backend.engine import phase5_config as cfg
from backend.engine.execute_action import (EXECUTION_STATE_MAP, STATUS_MAP,
                                           execute_action)
from backend.tests.conftest import make_opportunity, recent_in_window_ts

HOUR = 3600


def _executed(action, **extra):
    d = {"action_type": action, "allowed": True, "outcome": "executed",
         "reasoning": "w5 fixture", "triggered_by": "rule"}
    d.update(extra)
    return d


def _candidate_row(conn, opportunity_id, action="reminder", timing="immediate",
                   candidate_id=None):
    """A real recovery_candidates row, since candidate_id is a foreign key."""
    cur = conn.execute(
        """
        INSERT INTO recovery_candidates
        (candidate_id, opportunity_id, action_type, timing, method, channel,
         predicted_eiv, rank, selected, created_at)
        VALUES (?, ?, ?, ?, 'n/a', 'email', 12.5, 1, 0, ?)
        """,
        (candidate_id, opportunity_id, action, timing, int(time.time())),
    )
    conn.commit()
    return cur.lastrowid


def _executions(conn):
    return [dict(r) for r in
            conn.execute("SELECT * FROM recovery_executions").fetchall()]


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.execution_lifecycle")
def test_an_immediate_action_is_executed_not_scheduled(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    cid = _candidate_row(empty_db, opportunity["opportunity_id"],
                         timing="immediate")

    execute_action(opportunity, _executed("reminder", candidate_id=cid), empty_db)

    rows = _executions(empty_db)
    assert len(rows) == 1
    assert rows[0]["state"] == "executed"
    assert rows[0]["scheduled_for"] is None
    assert rows[0]["executed_at"] is not None


@pytest.mark.gate("phase5.execution_lifecycle")
@pytest.mark.parametrize("timing", ["4h", "24h", "3d"])
def test_a_future_timing_is_scheduled_and_has_not_executed(empty_db, timing):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    cid = _candidate_row(empty_db, opportunity["opportunity_id"], timing=timing)
    before = int(time.time())

    execute_action(opportunity, _executed("reminder", candidate_id=cid), empty_db)

    row = _executions(empty_db)[0]
    assert row["state"] == "scheduled"
    assert row["executed_at"] is None, "a scheduled action has not executed"
    expected = before + int(TIMING_HOURS[timing] * HOUR)
    assert abs(row["scheduled_for"] - expected) <= 5


@pytest.mark.gate("phase5.execution_lifecycle")
def test_every_written_state_is_in_the_closed_vocabulary(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    for i, timing in enumerate(("immediate", "4h", "24h", "3d")):
        opp = make_opportunity(empty_db, opportunity_id=f"opp_state_{i}",
                               created_at=recent_in_window_ts())
        cid = _candidate_row(empty_db, opp["opportunity_id"], timing=timing)
        execute_action(opp, _executed("reminder", candidate_id=cid), empty_db)

    for row in _executions(empty_db):
        assert row["state"] in EXECUTION_STATES


@pytest.mark.gate("phase5.execution_lifecycle")
def test_an_unknown_timing_raises_rather_than_defaulting(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    cid = _candidate_row(empty_db, opportunity["opportunity_id"],
                         timing="next_tuesday")

    with pytest.raises(ValueError) as exc:
        execute_action(opportunity, _executed("reminder", candidate_id=cid), empty_db)
    assert "TIMING_HOURS" in str(exc.value)


@pytest.mark.gate("phase5.execution_lifecycle")
def test_a_scheduled_action_leaves_the_opportunity_recovering(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    cid = _candidate_row(empty_db, opportunity["opportunity_id"], timing="24h")

    execute_action(opportunity, _executed("reminder", candidate_id=cid), empty_db)

    status = empty_db.execute(
        "SELECT status FROM opportunities WHERE opportunity_id = ?",
        (opportunity["opportunity_id"],)).fetchone()[0]
    assert status == "recovering"


@pytest.mark.gate("phase5.execution_lifecycle")
def test_a_stop_still_closes_the_opportunity_immediately(empty_db):
    """The terminal-resolution path is unchanged for the hardcoded stop."""
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())

    execute_action(opportunity, _executed("stop"), empty_db)

    row = dict(empty_db.execute(
        "SELECT status, resolved_at, recovered_bool, resolution_type "
        "FROM opportunities WHERE opportunity_id = ?",
        (opportunity["opportunity_id"],)).fetchone())
    assert row["status"] == "stopped"
    assert row["resolved_at"] is not None
    assert row["recovered_bool"] == 0
    assert row["resolution_type"] == "stopped"


# --------------------------------------------------------------------------
# Decision <-> candidate linkage
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.execution_lifecycle")
def test_the_decision_records_the_candidate_and_marks_it_selected(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    cid = _candidate_row(empty_db, opportunity["opportunity_id"])

    result = execute_action(opportunity,
                            _executed("reminder", candidate_id=cid), empty_db)

    stored = empty_db.execute(
        "SELECT candidate_id FROM recovery_decisions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone()[0]
    assert stored == cid

    selected = empty_db.execute(
        "SELECT selected FROM recovery_candidates WHERE candidate_id = ?",
        (cid,)).fetchone()[0]
    assert selected == 1


@pytest.mark.gate("phase5.execution_lifecycle")
def test_unselected_candidates_are_left_alone(empty_db):
    """
    Only the approved candidate is marked. The rest of the ranked set stays
    selected=0 -- that is what makes recovery_candidates an audit of what was
    considered rather than a record of what happened.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    chosen = _candidate_row(empty_db, opportunity["opportunity_id"], "reminder")
    other = _candidate_row(empty_db, opportunity["opportunity_id"], "escalate")

    execute_action(opportunity, _executed("reminder", candidate_id=chosen), empty_db)

    flags = dict(empty_db.execute(
        "SELECT candidate_id, selected FROM recovery_candidates").fetchall())
    assert flags[chosen] == 1
    assert flags[other] == 0


@pytest.mark.gate("phase5.execution_lifecycle")
def test_a_hardcoded_decision_still_writes_a_null_candidate_id(empty_db):
    """Every pre-Phase-5 caller passes no candidate; that must stay valid."""
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())

    result = execute_action(opportunity, _executed("retry"), empty_db)

    stored = empty_db.execute(
        "SELECT candidate_id FROM recovery_decisions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone()[0]
    assert stored is None
    assert _executions(empty_db)[0]["state"] == "executed"


@pytest.mark.gate("phase5.execution_lifecycle")
def test_an_invented_candidate_reference_is_rejected_not_coerced(empty_db):
    """
    Silently writing NULL for a candidate_id that does not exist would hide a
    real defect -- a decision claiming to come from a candidate that was never
    scored.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())

    with pytest.raises(sqlite3.IntegrityError):
        execute_action(opportunity,
                       _executed("reminder", candidate_id=999999), empty_db)


# --------------------------------------------------------------------------
# A decision that is not an execution writes no execution row
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.single_authority")
def test_a_do_nothing_decision_writes_no_execution_row(empty_db):
    """
    The R4 defect. Before the fix this wrote a recovery_executions row with
    state='executed' and a real executed_at -- a fabricated execution record
    for an action that was never dispatched, and an inflation of any
    downstream count of actions taken.

    Deciding to act by not acting is a real decision and is logged as one. It
    is not an execution.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())

    result = execute_action(opportunity, _executed("do_nothing"), empty_db)

    assert result["decision_id"] is not None
    decisions = empty_db.execute(
        "SELECT COUNT(*) FROM recovery_decisions").fetchone()[0]
    assert decisions == 1, "the decision itself must still be logged"
    assert _executions(empty_db) == [], (
        "do_nothing produced an execution row; nothing was dispatched")

    status = empty_db.execute(
        "SELECT status FROM opportunities WHERE opportunity_id = ?",
        (opportunity["opportunity_id"],)).fetchone()[0]
    assert status == opportunity["status"], "do_nothing moved the opportunity"


@pytest.mark.gate("permanent.single_authority")
def test_no_evaluable_only_action_can_write_an_execution_row(empty_db):
    """Generalised: the guard is keyed off the declared vocabulary."""
    for i, action in enumerate(cfg.EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS):
        opp = make_opportunity(empty_db, opportunity_id=f"opp_ev_{i}",
                               created_at=recent_in_window_ts())
        execute_action(opp, _executed(action), empty_db)
    assert _executions(empty_db) == []


@pytest.mark.gate("phase5.execution_lifecycle")
def test_a_blocked_decision_writes_no_execution_row(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    blocked = {"action_type": "retry", "allowed": False,
               "outcome": "blocked_cooldown", "reasoning": "w5 fixture",
               "triggered_by": "rule"}

    execute_action(opportunity, blocked, empty_db)

    assert _executions(empty_db) == []
    assert empty_db.execute(
        "SELECT COUNT(*) FROM recovery_decisions").fetchone()[0] == 1


# --------------------------------------------------------------------------
# Idempotency -- what the UNIQUE index does and does not protect
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.idempotent_dispatch")
def test_one_decision_can_never_carry_two_execution_rows(empty_db):
    """
    The schema-level guarantee W6's sweep will depend on: an execution row
    mutates in place, it is never represented as a second row.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    result = execute_action(opportunity, _executed("retry"), empty_db)

    with pytest.raises(sqlite3.IntegrityError) as exc:
        empty_db.execute(
            "INSERT INTO recovery_executions (decision_id, state, executed_at,"
            " channel) VALUES (?, 'executed', ?, NULL)",
            (result["decision_id"], int(time.time())))
    assert "recovery_executions.decision_id" in str(exc.value)
    assert len(_executions(empty_db)) == 1


@pytest.mark.gate("phase5.idempotent_dispatch")
def test_calling_execute_action_twice_creates_two_decisions_not_one(empty_db):
    """
    Records a real limitation rather than asserting a guarantee that does not
    exist.

    execute_action() mints a new decision row on every call, so calling it
    twice for the same logical action yields two decisions and two executions.
    The UNIQUE index does NOT prevent this -- it only prevents two executions
    hanging off one decision. execute_action() is therefore NOT idempotent at
    the call level, and no caller may rely on it being so.

    This is safe for W6 as designed: the dispatcher advances an execution row
    that already exists rather than calling execute_action() again. If that
    ever changes, an idempotency key on the decision is required, and this
    test is where the assumption is written down.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    decision = _executed("retry")

    first = execute_action(opportunity, decision, empty_db)
    second = execute_action(opportunity, decision, empty_db)

    assert first["decision_id"] != second["decision_id"]
    assert empty_db.execute(
        "SELECT COUNT(*) FROM recovery_decisions").fetchone()[0] == 2
    assert len(_executions(empty_db)) == 2


# --------------------------------------------------------------------------
# Executor vocabulary
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.declared_bounds")
def test_the_two_executor_maps_cover_the_same_actions():
    assert set(STATUS_MAP) == set(EXECUTION_STATE_MAP)


@pytest.mark.gate("phase5.execution_lifecycle")
def test_payment_link_is_dispatchable_end_to_end(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    cid = _candidate_row(empty_db, opportunity["opportunity_id"], "payment_link")

    execute_action(opportunity,
                   _executed("payment_link", candidate_id=cid), empty_db)

    assert _executions(empty_db)[0]["state"] == "executed"
    status = empty_db.execute(
        "SELECT status FROM opportunities WHERE opportunity_id = ?",
        (opportunity["opportunity_id"],)).fetchone()[0]
    assert status == "recovering"
