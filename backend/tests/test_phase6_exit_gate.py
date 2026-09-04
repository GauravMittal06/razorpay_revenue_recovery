"""
Phase 6 exit gate — the requirements from Phase_Acceptance_Test_Gates.md that
the phase's own DoD did not name.

    Exit gate: "A complete controlled experiment can be simulated/exercised
    end-to-end with valid assignment, suppression and observation evidence,
    [NEW] including a concurrency/duplicate-delivery fixture."

Four gate rows have no home in the earlier Phase 6 test files, because the
Definition of Done in EXECUTION_PLAN.md is narrower than the acceptance-gate
document:

    Assignment            [NEW] proven safe under CONCURRENT attempts, at the
                          application layer, not only via the schema's
                          uniqueness constraint
    Duplicate-outcome     [NEW] submitting the same outcome event twice does
    safety                not double-count recovery or corrupt the fields
    No retroactive        outcome observation never changes historical
    contamination         treatment assignment
    Lineage               every outcome traces to its opportunity and its
                          relevant execution history

The end-to-end exercise itself — assignment, suppression AND observation on
one population — is the last test in this file.
"""

import sqlite3
import threading

import pytest

from backend.data.generate_experiment_outcomes import SOURCE
from backend.db import db
from backend.engine import phase6_config as cfg
from backend.engine.assign_experiment_group import (assign_experiment_group,
                                                    get_assignment)
from backend.engine.observe_outcome import observe_outcome
from backend.tests.conftest import make_opportunity

THREADS = 8


def _run_in_parallel(fn, n=THREADS):
    """Release n workers simultaneously; collect results and exceptions."""
    barrier = threading.Barrier(n)
    results, errors = [], []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            value = fn(index)
        except Exception as exc:            # recorded, never swallowed
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


# --------------------------------------------------------------------------
# Gate: Assignment, proven safe under concurrent attempts
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.exit_gate")
def test_concurrent_assignment_attempts_produce_one_arm(seeded_db, db_path):
    """
    Eight workers race to assign the same opportunity. Exactly one row must
    exist afterwards and every caller must agree on the arm.

    Re-verified at the APPLICATION layer, as the gate requires. The schema's
    primary key already guarantees one row; what this adds is that the
    function's own return contract does not lie to the losers. A caller told
    "assigned" for an arm it did not get would propagate a wrong arm into
    whatever it does next — and the row itself would look perfectly clean.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_race_asg_1")

    def attempt(_index):
        conn = db.get_connection()
        try:
            return assign_experiment_group(opp["opportunity_id"], conn)
        finally:
            conn.close()

    results, errors = _run_in_parallel(attempt)
    assert not errors, f"unhandled exceptions during concurrent assignment: {errors}"

    rows = seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone()[0]
    assert rows == 1, f"{rows} assignment rows for one opportunity"

    arms = {r["group"] for r in results}
    assert len(arms) == 1, f"callers disagreed on the arm: {arms}"

    statuses = [r["status"] for r in results]
    assert statuses.count("assigned") == 1, (
        f"{statuses.count('assigned')} callers were told they made the "
        f"assignment; exactly one did: {statuses}")
    assert arms == {cfg.assigned_group(opp["opportunity_id"])}, \
        "the surviving arm is not the one the locked formula derives"


@pytest.mark.gate("phase6.exit_gate")
def test_concurrent_duplicate_event_delivery_creates_one_opportunity(seeded_db,
                                                                     db_path):
    """
    The duplicate-delivery half of the same fixture, one level up: eight
    concurrent deliveries of ONE upstream event must create one opportunity
    with one assignment, not eight of each.
    """
    from backend.engine.trigger_event import trigger_event

    def deliver(_index):
        conn = db.get_connection()
        try:
            return trigger_event("payment_failed", 4321, conn,
                                 root_cause="expired_card",
                                 event_id="evt_race_1")
        finally:
            conn.close()

    results, errors = _run_in_parallel(deliver)
    assert not errors, f"unhandled exceptions during concurrent delivery: {errors}"

    statuses = [r["status"] for r in results]
    assert statuses.count("ok") == 1, (
        f"{statuses.count('ok')} callers created an opportunity for one event")

    opportunity_ids = {r["opportunity"]["opportunity_id"] for r in results}
    assert len(opportunity_ids) == 1, f"event fanned out: {opportunity_ids}"

    opportunity_id = opportunity_ids.pop()
    assert seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment WHERE opportunity_id = ?",
        (opportunity_id,)).fetchone()[0] == 1


# --------------------------------------------------------------------------
# Gate [NEW]: Duplicate-outcome safety
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.exit_gate")
def test_the_same_outcome_event_twice_does_not_double_count(seeded_db):
    """
    The gate's exact wording: submitting the same outcome-observation event
    twice must not double-count recovery or corrupt the opportunity's outcome
    fields.

    Double-counting is the failure with no visible symptom. The row ends up
    looking entirely well-formed either way; what changes is that a ledger
    trusting the return value counts the same rupees twice, and Phase 7's
    numerator is exactly such a ledger.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_dup_1",
                           amount_at_risk=40000, created_at=1000)

    first = observe_outcome(opp["opportunity_id"], seeded_db,
                            resolution="recovered", source=SOURCE,
                            partial_recovery_amount=25000, now=5000)
    second = observe_outcome(opp["opportunity_id"], seeded_db,
                             resolution="recovered", source=SOURCE,
                             partial_recovery_amount=25000, now=9000)

    assert first["result"] == "observed"
    assert second["result"] == "already_resolved", (
        "the second submission was accepted as a fresh observation")

    row = seeded_db.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone()
    # Not doubled, and not moved.
    assert row["partial_recovery_amount"] == 25000
    assert row["recovered_at"] == 5000
    assert row["time_to_recovery"] == 4000
    assert row["recovered_bool"] == 1
    assert row["resolution_type"] == "recovered"
    assert row["outcome_source"] == SOURCE


@pytest.mark.gate("phase6.exit_gate")
def test_a_conflicting_second_outcome_does_not_corrupt_the_first(seeded_db):
    """
    The nastier duplicate: same opportunity, DIFFERENT outcome. The first
    observation must stand whole — no field may take the second's value.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_dup_2",
                           amount_at_risk=40000, created_at=1000)
    observe_outcome(opp["opportunity_id"], seeded_db, resolution="recovered",
                    source=SOURCE, partial_recovery_amount=40000, now=5000)
    before = dict(seeded_db.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone())

    observe_outcome(opp["opportunity_id"], seeded_db, resolution="lost",
                    source=SOURCE, now=9000)

    after = dict(seeded_db.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone())
    assert after == before, "a conflicting second outcome mutated the first"


@pytest.mark.gate("phase6.exit_gate")
def test_concurrent_duplicate_outcome_submissions_produce_one_winner(seeded_db,
                                                                     db_path):
    """
    The concurrent form. Eight workers submit the same outcome at once;
    exactly one may be told it recorded it.

    The deterministic tests above pass on a check-then-write implementation
    too. Only this one can catch the version where every caller passes the
    pre-read and every caller is told "observed".
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_dup_race",
                           amount_at_risk=50000, created_at=1000)

    def submit(_index):
        conn = db.get_connection()
        try:
            return observe_outcome(opp["opportunity_id"], conn,
                                   resolution="recovered", source=SOURCE,
                                   partial_recovery_amount=50000, now=7000)
        finally:
            conn.close()

    results, errors = _run_in_parallel(submit)
    assert not errors, f"unhandled exceptions: {errors}"

    observed = [r for r in results if r["result"] == "observed"]
    assert len(observed) == 1, (
        f"{len(observed)} callers were told they recorded the outcome; that is "
        "how one recovery gets counted N times by a ledger trusting the "
        "return value")

    row = seeded_db.execute(
        "SELECT partial_recovery_amount, recovered_bool FROM opportunities "
        "WHERE opportunity_id = ?", (opp["opportunity_id"],)).fetchone()
    assert row["partial_recovery_amount"] == 50000
    assert row["recovered_bool"] == 1


# --------------------------------------------------------------------------
# Gate: No retroactive contamination
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.exit_gate")
def test_observing_an_outcome_never_changes_the_assignment(seeded_db):
    """
    If an outcome could move an opportunity between arms, the experiment would
    be measuring a partition drawn AFTER the results were known — the purest
    form of the failure this whole phase exists to prevent, and one that
    leaves no trace in the data.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_retro_1",
                           amount_at_risk=30000)
    assign_experiment_group(opp["opportunity_id"], seeded_db, now=100)
    before = get_assignment(opp["opportunity_id"], seeded_db)

    observe_outcome(opp["opportunity_id"], seeded_db, resolution="recovered",
                    source=SOURCE, now=900)

    assert get_assignment(opp["opportunity_id"], seeded_db) == before

    # The structural half of this claim -- that the outcome writer cannot
    # reach experiment_assignment at all -- is
    # test_permanent_gates.test_the_outcome_writer_is_not_experiment_aware.
    # Delegated rather than duplicated here: a naive grep of the raw source
    # matches the module docstring, which *explains* why it must not use that
    # table, and a check that fires on its own explanation is worse than no
    # check. The permanent gate strips docstrings and comments first.
    from backend.tests import test_permanent_gates as gates
    gates.test_the_outcome_writer_is_not_experiment_aware()


@pytest.mark.gate("phase6.exit_gate")
def test_assignment_timestamps_are_never_moved_by_later_activity(seeded_db):
    """
    `assigned_at` is the instant the counterfactual gate measures activity
    against. If later activity could move it, activity that happened after
    assignment could be relabelled as having happened before.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_retro_2")
    assign_experiment_group(opp["opportunity_id"], seeded_db, now=100)
    for _ in range(3):
        assign_experiment_group(opp["opportunity_id"], seeded_db, now=99999)
    observe_outcome(opp["opportunity_id"], seeded_db, resolution="lost",
                    source=SOURCE, now=99999)
    assert get_assignment(opp["opportunity_id"], seeded_db)["assigned_at"] == 100


# --------------------------------------------------------------------------
# Gate: Lineage
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.exit_gate")
def test_every_outcome_traces_to_its_opportunity_and_execution_history(seeded_db):
    """
    "Every outcome can be traced to its opportunity and relevant execution
    history."

    Exercised as a single join from the outcome back through assignment,
    decision and execution — the query an auditor would actually run. An
    outcome that cannot be walked back to what the system did to earn it is
    not evidence of anything.
    """
    from backend.data.generate_experiment_outcomes import generate
    from backend.data.generate_experiment_volume import generate as volume

    volume(count=40, conn=seeded_db)
    generate(conn=seeded_db)

    rows = seeded_db.execute(
        """
        SELECT o.opportunity_id, o.resolution_type, o.outcome_source,
               a."group" AS arm, a.assigned_at,
               d.decision_id, d.action_type, d.outcome AS compliance_outcome,
               e.state AS execution_state
        FROM opportunities o
        JOIN experiment_assignment a ON a.opportunity_id = o.opportunity_id
        LEFT JOIN recovery_decisions d ON d.opportunity_id = o.opportunity_id
        LEFT JOIN recovery_executions e ON e.decision_id = d.decision_id
        WHERE o.resolution_type IS NOT NULL
        """
    ).fetchall()
    assert rows, "no observed outcome to trace"

    for r in rows:
        assert r["arm"] in cfg.GROUPS
        assert r["assigned_at"] is not None
        assert r["outcome_source"] == SOURCE
        # Every experiment opportunity has at least a compliance decision --
        # a suppression for control, an adjudication for treatment. An
        # outcome with no decision behind it would be untraceable.
        assert r["decision_id"] is not None, (
            f"{r['opportunity_id']} has an outcome but no decision history")
        if r["arm"] == cfg.CONTROL_GROUP:
            assert r["compliance_outcome"] == cfg.SUPPRESSION_OUTCOME
            assert r["execution_state"] is None


# --------------------------------------------------------------------------
# The exit gate itself
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.exit_gate")
def test_a_complete_controlled_experiment_end_to_end(seeded_db, capsys):
    """
    Assignment, suppression AND observation on one population, exercised
    through the real entry points and reported with raw numbers.

    This is the gate's own sentence made executable. Deliberately modest in
    size — the statistical gates live in test_phase6_gates.py; what this
    proves is that the three mechanisms compose on one population rather than
    each working in its own fixture.
    """
    from backend.analytics import counterfactual_consistency as cc
    from backend.data.generate_experiment_outcomes import generate
    from backend.data.generate_experiment_volume import generate as volume

    volume(count=120, conn=seeded_db)
    outcomes = generate(conn=seeded_db)

    assigned = seeded_db.execute(
        'SELECT "group", COUNT(*) FROM experiment_assignment GROUP BY "group"'
    ).fetchall()
    resolved = seeded_db.execute(
        """
        SELECT a."group" AS arm, o.resolution_type, COUNT(*) AS n
        FROM opportunities o
        JOIN experiment_assignment a ON a.opportunity_id = o.opportunity_id
        WHERE o.resolution_type IS NOT NULL
        GROUP BY a."group", o.resolution_type ORDER BY arm, o.resolution_type
        """
    ).fetchall()

    report = cc.consistency_report(seeded_db)
    decision = cc.verdict(report)

    with capsys.disabled():
        print("\n  END-TO-END CONTROLLED EXPERIMENT (synthetic outcomes)")
        print(f"    assigned      : {[tuple(r) for r in assigned]}")
        print(f"    realized      : {outcomes['realized']} "
              f"({outcomes['by_arm']})")
        print(f"    recovered     : {outcomes['recovered_by_arm']}")
        print(f"    resolutions   : {[tuple(r) for r in resolved]}")
        print(f"    actions drawn : {outcomes['actions']}")
        print()
        print(cc.format_report(report, decision))

    # Assignment happened, both arms.
    arms = {r["group"] for r in assigned}
    assert arms == set(cfg.GROUPS)
    # Observation happened, through the one path, labelled synthetic.
    assert outcomes["realized"] > 0
    sources = {r[0] for r in seeded_db.execute(
        "SELECT DISTINCT outcome_source FROM opportunities "
        "WHERE outcome_source IS NOT NULL")}
    assert sources == {SOURCE}
    # Suppression held throughout.
    assert decision["verdict"] == "PASS", decision["reasons"]
    # Both arms produced outcomes -- a control arm with no outcomes would make
    # any incremental comparison undefined.
    assert set(outcomes["by_arm"]) == set(cfg.GROUPS), (
        f"only {sorted(outcomes['by_arm'])} produced outcomes")
