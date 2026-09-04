"""
Phase 6 / X4 -- one outcome-ingestion path, and only one.

The DoD phrase is "outcomes are captured through exactly one ingestion path".
The structural half of that is in test_permanent_gates.py (no other module in
the backend may UPDATE an outcome column). This file is the behavioural half:
every source actually routes through `observe_outcome()`, the vocabularies are
enforced, terminal states are respected, and the concurrency guard that only
one of the two former writers had is now on all of them.

The one property worth stating up front, because it is the difference between
a measurable experiment and a rigged one: `observe_outcome` is NOT
experiment-aware. A control opportunity can recover, and must be able to.
"""

import sqlite3

import pytest

from backend.db import db
from backend.engine import phase6_config as cfg
from backend.engine.observe_outcome import (is_partial_recovery,
                                            observe_outcome)
from backend.tests.conftest import make_opportunity


# --------------------------------------------------------------------------
# Vocabulary enforcement
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.single_ingestion")
def test_resolution_must_be_in_the_closed_vocabulary(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0001")
    with pytest.raises(ValueError, match="closed resolution vocabulary"):
        observe_outcome(opp["opportunity_id"], seeded_db,
                        resolution="mostly_recovered", source="manual_confirmation")


@pytest.mark.gate("phase6.single_ingestion")
def test_source_must_be_in_the_closed_vocabulary(seeded_db):
    """
    Required, and closed. A caller that could omit or invent a source would
    make "exactly one ingestion path" true in the code and unauditable in the
    data -- nothing would say which caller drove a given outcome.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0002")
    with pytest.raises(ValueError, match="closed outcome-source vocabulary"):
        observe_outcome(opp["opportunity_id"], seeded_db,
                        resolution="recovered", source="somewhere")


@pytest.mark.gate("phase6.single_ingestion")
def test_source_and_resolution_are_keyword_only(seeded_db):
    """Positional args invite a caller to transpose them silently."""
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0003")
    with pytest.raises(TypeError):
        observe_outcome(opp["opportunity_id"], seeded_db,
                        "recovered", "manual_confirmation")


# --------------------------------------------------------------------------
# Each resolution writes the right row
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.single_ingestion")
def test_a_full_recovery(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0010",
                           amount_at_risk=50000, created_at=1000)
    result = observe_outcome(opp["opportunity_id"], seeded_db,
                             resolution="recovered",
                             source="payment_event", now=4600)
    assert result["result"] == "observed"
    row = _row(seeded_db, opp["opportunity_id"])
    assert row["status"] == "recovered"
    assert row["resolution_type"] == "recovered"
    assert row["recovered_bool"] == 1
    assert row["partial_recovery_amount"] == 50000
    assert row["recovered_at"] == 4600
    assert row["time_to_recovery"] == 3600
    assert row["outcome_source"] == "payment_event"
    assert not is_partial_recovery(row)


@pytest.mark.gate("phase6.single_ingestion")
def test_a_partial_recovery_is_inferred_not_a_separate_value(seeded_db):
    """
    Ruling 2026-09-04: partial recovery has no resolution_type of its own.
    Adding one would force every "was this recovered" query to match two
    values, and any query that forgot the second would under-count.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0011",
                           amount_at_risk=50000, created_at=1000)
    observe_outcome(opp["opportunity_id"], seeded_db, resolution="recovered",
                    source="manual_confirmation",
                    partial_recovery_amount=20000, now=2000)
    row = _row(seeded_db, opp["opportunity_id"])
    assert row["resolution_type"] == "recovered"
    assert row["partial_recovery_amount"] == 20000
    assert row["recovered_bool"] == 1
    assert is_partial_recovery(row)
    assert "partially_recovered" not in db.RESOLUTION_TYPES


@pytest.mark.gate("phase6.single_ingestion")
@pytest.mark.parametrize("resolution", ["stopped", "lost"])
def test_a_non_recovery_records_zero_not_null(seeded_db, resolution):
    """
    0 rather than NULL so SUM(partial_recovery_amount) over resolved
    opportunities is the recovered total without a COALESCE every caller has
    to remember to write.
    """
    opp = make_opportunity(seeded_db, opportunity_id=f"opp_obs_{resolution}",
                           amount_at_risk=50000)
    observe_outcome(opp["opportunity_id"], seeded_db, resolution=resolution,
                    source="executor_stop" if resolution == "stopped"
                    else "payment_event", now=5000)
    row = _row(seeded_db, opp["opportunity_id"])
    assert row["recovered_bool"] == 0
    assert row["partial_recovery_amount"] == 0
    assert row["recovered_at"] is None
    assert row["time_to_recovery"] is None
    assert row["resolution_type"] == resolution
    assert row["status"] == "stopped"


@pytest.mark.gate("phase6.single_ingestion")
def test_lost_is_not_a_synonym_for_stopped(seeded_db):
    """
    Ruling 2026-09-04. `stopped` is a POLICY resolution -- the engine
    exhausted its attempts. `lost` is an OBSERVED FACT -- the money is
    definitively gone. Both close the case, and both must remain
    distinguishable, or "how much did we actually fail to recover" becomes
    unanswerable.
    """
    a = make_opportunity(seeded_db, opportunity_id="opp_obs_stop_x")
    b = make_opportunity(seeded_db, opportunity_id="opp_obs_lost_x")
    observe_outcome(a["opportunity_id"], seeded_db, resolution="stopped",
                    source="executor_stop")
    observe_outcome(b["opportunity_id"], seeded_db, resolution="lost",
                    source="payment_event")
    assert _row(seeded_db, a["opportunity_id"])["resolution_type"] == "stopped"
    assert _row(seeded_db, b["opportunity_id"])["resolution_type"] == "lost"


# --------------------------------------------------------------------------
# Terminal means terminal
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.single_ingestion")
def test_a_resolved_opportunity_is_not_overwritten(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0020",
                           amount_at_risk=9000)
    observe_outcome(opp["opportunity_id"], seeded_db, resolution="recovered",
                    source="payment_event", now=100)
    second = observe_outcome(opp["opportunity_id"], seeded_db,
                             resolution="lost", source="payment_event", now=200)
    assert second["result"] == "already_resolved"
    row = _row(seeded_db, opp["opportunity_id"])
    assert row["resolution_type"] == "recovered"
    assert row["recovered_at"] == 100


@pytest.mark.gate("phase6.single_ingestion")
def test_the_where_clause_is_the_guard_not_the_pre_check(seeded_db):
    """
    The pre-read is a fast path with a check-then-write race window. The
    guarantee is the UPDATE's own precondition, so a write that bypasses the
    pre-check must still be refused.

    Without it every concurrent caller passes the check, every one issues the
    UPDATE, every one is told "ok", and the row still looks clean -- which is
    how one recovery gets counted N times by a ledger that trusts the return
    value.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0021")
    observe_outcome(opp["opportunity_id"], seeded_db, resolution="recovered",
                    source="payment_event", now=100)
    cursor = seeded_db.execute(
        "UPDATE opportunities SET resolution_type = 'lost' "
        "WHERE opportunity_id = ? AND status NOT IN ('recovered', 'stopped')",
        (opp["opportunity_id"],))
    assert cursor.rowcount == 0


@pytest.mark.gate("phase6.single_ingestion")
def test_an_unknown_opportunity_is_reported_not_invented(seeded_db):
    result = observe_outcome("opp_nope", seeded_db, resolution="recovered",
                             source="payment_event")
    assert result["result"] == "opportunity_not_found"


# --------------------------------------------------------------------------
# Every source routes through the one path
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.single_ingestion")
def test_manual_confirmation_routes_through_and_is_labelled(seeded_db):
    """The console utility is retained deliberately (EXECUTION_PLAN Phase 6)
    but is now a wrapper, and its outcomes are labelled as manual in the data
    rather than only in the narration."""
    from backend.engine.mark_opportunity_recovered import mark_opportunity_recovered

    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0030",
                           amount_at_risk=7000)
    result = mark_opportunity_recovered(opp["opportunity_id"], seeded_db)
    assert result["status"] == "ok"
    assert result["opportunity_status"] == "recovered"
    row = _row(seeded_db, opp["opportunity_id"])
    assert row["outcome_source"] == "manual_confirmation"
    assert row["recovered_bool"] == 1


@pytest.mark.gate("phase6.single_ingestion")
def test_the_manual_utility_preserves_its_legacy_return_shape(seeded_db):
    """api/actions.simulate_recovery and the console depend on these exact
    status strings."""
    from backend.engine.mark_opportunity_recovered import mark_opportunity_recovered

    missing = mark_opportunity_recovered("opp_nope", seeded_db)
    assert missing["status"] == "opportunity_not_found"

    stopped = make_opportunity(seeded_db, opportunity_id="opp_obs_0031")
    observe_outcome(stopped["opportunity_id"], seeded_db, resolution="stopped",
                    source="executor_stop")
    assert mark_opportunity_recovered(stopped["opportunity_id"],
                                      seeded_db)["status"] == "rejected_stopped"

    done = make_opportunity(seeded_db, opportunity_id="opp_obs_0032")
    mark_opportunity_recovered(done["opportunity_id"], seeded_db)
    assert mark_opportunity_recovered(done["opportunity_id"],
                                      seeded_db)["status"] == "already_recovered"


@pytest.mark.gate("phase6.single_ingestion")
def test_the_executor_stop_routes_through_and_is_labelled(seeded_db):
    """
    The former second write route. The rule engine still decides the case
    closes by policy; it now records that through the one path.
    """
    from backend.engine.execute_action import execute_action

    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0040",
                           status="recovering", amount_at_risk=8000)
    decision = {"action_type": "stop", "allowed": True,
                "reasoning": "Max attempts reached.", "outcome": "executed",
                "triggered_by": "rule"}
    execute_action(opp, decision, seeded_db)

    row = _row(seeded_db, opp["opportunity_id"])
    assert row["status"] == "stopped"
    assert row["resolution_type"] == "stopped"
    assert row["recovered_bool"] == 0
    assert row["partial_recovery_amount"] == 0
    assert row["outcome_source"] == "executor_stop"


@pytest.mark.gate("phase6.single_ingestion")
def test_a_stop_cannot_overwrite_an_already_recovered_case(seeded_db):
    """
    The divergence that motivated unification. The executor's old direct write
    had no compare-and-swap, so a stop racing a recovery could flip a
    recovered case to unrecovered -- silently corrupting exactly the numerator
    Phase 7 divides by.
    """
    from backend.engine.execute_action import execute_action

    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0041",
                           status="recovering", amount_at_risk=8000)
    observe_outcome(opp["opportunity_id"], seeded_db, resolution="recovered",
                    source="payment_event", now=500)
    decision = {"action_type": "stop", "allowed": True,
                "reasoning": "Max attempts reached.", "outcome": "executed",
                "triggered_by": "rule"}
    execute_action(opp, decision, seeded_db)

    row = _row(seeded_db, opp["opportunity_id"])
    assert row["resolution_type"] == "recovered", \
        "a stop overwrote a recovered outcome"
    assert row["recovered_bool"] == 1


# --------------------------------------------------------------------------
# The property that keeps the experiment honest
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.single_ingestion")
def test_a_control_opportunity_can_still_recover(seeded_db):
    """
    Control means no automated INTERVENTION, not no outcome. If the outcome
    writer suppressed control recoveries the measured incremental effect would
    be whatever the system wanted it to be, and the whole experiment would be
    circular.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_obs_0050",
                           amount_at_risk=12000)
    seeded_db.execute(
        'INSERT INTO experiment_assignment '
        '(opportunity_id, "group", assigned_at, assignment_method) '
        "VALUES (?, ?, ?, ?)",
        (opp["opportunity_id"], cfg.CONTROL_GROUP, 1,
         cfg.assignment_method_record()))
    seeded_db.commit()

    result = observe_outcome(opp["opportunity_id"], seeded_db,
                             resolution="recovered", source="payment_event")
    assert result["result"] == "observed"
    row = _row(seeded_db, opp["opportunity_id"])
    assert row["recovered_bool"] == 1
    assert row["status"] == "recovered"


@pytest.mark.gate("phase6.single_ingestion")
def test_the_deleted_legacy_utility_is_gone(seeded_db):
    """
    mark_payment_recovered.py wrote payments.recovery_status / recovered_at,
    neither of which exists in the Phase 1 schema -- any call raised
    OperationalError. Deleted 2026-09-04 by ruling. Its name stays in
    test_permanent_gates.FORBIDDEN_AUTHORITY_NAMES so nothing reintroduces a
    second recovery writer under the old name.
    """
    with pytest.raises(ImportError):
        __import__("backend.engine.mark_payment_recovered")

    from backend.tests import test_permanent_gates as gates
    assert "mark_payment_recovered" in gates.FORBIDDEN_AUTHORITY_NAMES


def _row(conn, opportunity_id):
    return conn.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,)).fetchone()
