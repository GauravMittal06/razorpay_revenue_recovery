"""
Phase 6 / X5 -- the two hard gates.

    randomization balance     : are the arms comparable at assignment time?
    counterfactual consistency: is control suppression real, not cosmetic?

WHY THE BALANCE GATE IS OPT-IN
    It needs `MIN_ASSIGNED_N` opportunities created through the real
    `trigger_event()` path -- 3500 of them, about ten minutes. That is not a
    per-invocation cost the rest of the suite can carry, and faking the
    population would defeat the gate: the whole point is that the system
    assigned these rows, not that a fixture labelled them.

    So it carries `@pytest.mark.slow` (run with `-m slow`) and its raw output
    is committed to tests/evidence/phase6_x5_gate_evidence.txt as the standing
    record. The counterfactual gate is cheap enough to run every time and is
    not marked.

A NOTE ON RE-RUNNING A FAILURE
    At exactly n = MIN_ASSIGNED_N the gate passes 98.0% of the time under a
    known-correct randomizer (measured, 500 trials). A single failing run is
    therefore expected roughly once in fifty and is not by itself evidence of
    a regression -- re-run it before treating it as one. That is a property of
    the floor being chosen at a 95% criterion rather than a 100% one, and it
    is disclosed rather than papered over.

NO SELF-CERTIFICATION
    Both gates print their raw figures -- per-arm n, distribution summaries,
    the full level x arm contingency table, every signed SMD, and every probe
    count -- before any verdict is derived. The verdict is computed by
    `verdict()` from those same numbers against bounds locked in
    phase6_config, and the numbers are printed whether it passes or fails.
"""

import pytest

from backend.analytics import counterfactual_consistency as cc
from backend.analytics import randomization_balance as rb
from backend.engine import phase6_config as cfg
from backend.tests.conftest import make_opportunity


def _assign(conn, opportunity_id, group):
    conn.execute(
        'INSERT INTO experiment_assignment '
        '(opportunity_id, "group", assigned_at, assignment_method) '
        "VALUES (?, ?, ?, ?)",
        (opportunity_id, group, 1, cfg.assignment_method_record()))
    conn.commit()


# --------------------------------------------------------------------------
# The balance gate
# --------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.gate("phase6.randomization_balance")
def test_randomization_balance_on_a_real_assigned_population(seeded_db, capsys):
    from backend.data.generate_experiment_volume import generate

    summary = generate(count=cfg.MIN_ASSIGNED_N, conn=seeded_db)
    assert summary["created"] == cfg.MIN_ASSIGNED_N, summary

    report = rb.balance_report(seeded_db)
    decision = rb.verdict(report)
    with capsys.disabled():
        print()
        print(rb.format_report(report, decision))

    assert report["n_total"] >= cfg.MIN_ASSIGNED_N
    assert decision["verdict"] == "PASS", decision["reasons"]


@pytest.mark.gate("phase6.randomization_balance")
def test_the_gate_refuses_to_certify_a_sample_too_small(seeded_db, capsys):
    """
    The floor doing its job. This is what the gate should have said at n=240
    instead of FAIL -- "cannot tell" is the honest verdict on a sample too
    small for the bound to be meaningful, and reporting it as a failure is
    what nearly triggered an unnecessary escalation to stratified assignment.
    """
    for i in range(20):
        opp = make_opportunity(seeded_db, opportunity_id=f"opp_bal_{i:04d}")
        _assign(seeded_db, opp["opportunity_id"],
                cfg.CONTROL_GROUP if i % 2 else cfg.TREATMENT_GROUP)

    report = rb.balance_report(seeded_db)
    decision = rb.verdict(report)
    with capsys.disabled():
        print(f"\n  n={report['n_total']} -> {decision['verdict']}")
    assert decision["verdict"] == "NOT_EVALUABLE"
    assert any("MIN_ASSIGNED_N" in r for r in decision["reasons"])


@pytest.mark.gate("phase6.randomization_balance")
def test_an_empty_experiment_is_not_evaluable(seeded_db):
    """Zero assigned opportunities must not read as perfect balance."""
    decision = rb.verdict(rb.balance_report(seeded_db))
    assert decision["verdict"] == "NOT_EVALUABLE"


@pytest.mark.gate("phase6.randomization_balance")
def test_the_degenerate_smd_conventions_hold():
    """
    A level present in one arm and absent from the other is maximal
    imbalance. Returning nan would compare False against every bound and pass
    silently, which is the failure this convention exists to prevent.
    """
    assert rb.categorical_smd(0.0, 0.0) == cfg.DEGENERATE_SMD_BALANCED
    assert rb.categorical_smd(1.0, 1.0) == cfg.DEGENERATE_SMD_BALANCED
    assert rb.categorical_smd(1.0, 0.0) == cfg.DEGENERATE_SMD_IMBALANCED
    assert rb.categorical_smd(0.0, 1.0) == cfg.DEGENERATE_SMD_IMBALANCED
    assert cfg.DEGENERATE_SMD_IMBALANCED > cfg.MAX_ABS_SMD


@pytest.mark.gate("phase6.randomization_balance")
def test_unassigned_opportunities_are_excluded_from_the_population(seeded_db):
    """
    The 150 seeded opportunities were never randomized. Including them would
    silently enrol a never-randomized population into the comparison, and
    every one of them would then inform an incremental number it has no
    business informing.
    """
    seeded = seeded_db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    assert seeded > 0
    opp = make_opportunity(seeded_db, opportunity_id="opp_bal_only_one")
    _assign(seeded_db, opp["opportunity_id"], cfg.TREATMENT_GROUP)

    report = rb.balance_report(seeded_db)
    assert report["n_total"] == 1, \
        f"unassigned opportunities leaked into the balance population: {report['n_total']}"


# --------------------------------------------------------------------------
# The counterfactual gate
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.counterfactual_consistency")
def test_counterfactual_consistency_on_a_real_population(seeded_db, capsys):
    """
    Cheap enough to run every time. 60 opportunities is well below the balance
    gate's floor, which is fine -- this gate counts violations rather than
    estimating a statistic, so it needs enough rows to exercise both arms, not
    enough to bound sampling error.
    """
    from backend.data.generate_experiment_volume import generate

    generate(count=60, conn=seeded_db)
    report = cc.consistency_report(seeded_db)
    decision = cc.verdict(report)
    with capsys.disabled():
        print()
        print(cc.format_report(report, decision))

    assert report["n"][cfg.CONTROL_GROUP] > 0
    assert report["n"][cfg.TREATMENT_GROUP] > 0
    assert decision["verdict"] == "PASS", decision["reasons"]


@pytest.mark.gate("phase6.counterfactual_consistency")
def test_the_gate_fails_when_control_shows_an_executed_action(seeded_db):
    """
    Negative control for the gate itself. A gate never seen to fail is not
    evidence, so a control-arm executed decision is injected directly and the
    verdict must flip.
    """
    from backend.tests.conftest import insert_decision
    from backend.data.generate_experiment_volume import generate

    generate(count=40, conn=seeded_db)
    control = seeded_db.execute(
        'SELECT opportunity_id, assigned_at FROM experiment_assignment '
        'WHERE "group" = ? LIMIT 1', (cfg.CONTROL_GROUP,)).fetchone()
    assert control, "no control opportunity to corrupt"

    # Stamped at the assignment instant, not "now". Every probe is bounded at
    # `assigned_at` -- activity strictly before assignment is legitimately not
    # a violation -- and the volume generator pins creation to midday, so a
    # decision carrying the real wall clock can land BEFORE the assignment it
    # is meant to violate and the gate then correctly ignores it.
    #
    # That is exactly what happened when clock pinning was introduced: this
    # negative control started reporting PASS. The gate was right and the
    # fixture was wrong, which is the failure mode a negative control exists
    # to expose -- in this case about itself.
    insert_decision(seeded_db, control["opportunity_id"], "retry",
                    outcome="executed", timestamp=control["assigned_at"] + 1)
    decision = cc.verdict(cc.consistency_report(seeded_db))
    assert decision["verdict"] == "FAIL"
    assert any("executed_decisions" in r for r in decision["reasons"])


@pytest.mark.gate("phase6.counterfactual_consistency")
def test_a_system_that_acts_on_nobody_does_not_pass(seeded_db):
    """
    THE reason the treatment arm is measured at all. "Control shows nothing"
    is unfalsifiable on its own -- a broken optimizer, a no-op executor or a
    misconfigured entry point would satisfy a control-only check perfectly.
    Here both arms are assigned and nothing is ever executed; the gate must
    refuse.
    """
    for i in range(20):
        opp = make_opportunity(seeded_db, opportunity_id=f"opp_cf_{i:04d}")
        _assign(seeded_db, opp["opportunity_id"],
                cfg.CONTROL_GROUP if i % 2 else cfg.TREATMENT_GROUP)

    decision = cc.verdict(cc.consistency_report(seeded_db))
    assert decision["verdict"] == "FAIL"
    assert any("unfalsifiable" in r for r in decision["reasons"])


@pytest.mark.gate("phase6.counterfactual_consistency")
def test_suppression_that_logged_nothing_does_not_pass(seeded_db):
    """
    A control arm with no suppression decision rows would mean suppression was
    implemented by returning early and logging nothing, breaking the invariant
    that every action the system declines to take is logged with a reason.
    Silence would otherwise look identical to correct suppression.
    """
    from backend.data.generate_experiment_volume import generate

    generate(count=40, conn=seeded_db)
    seeded_db.execute("DELETE FROM recovery_decisions WHERE outcome = ?",
                      (cfg.SUPPRESSION_OUTCOME,))
    seeded_db.commit()

    decision = cc.verdict(cc.consistency_report(seeded_db))
    assert decision["verdict"] == "FAIL"
    assert any("logged with a reason" in r for r in decision["reasons"])


# --------------------------------------------------------------------------
# The committed evidence
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.randomization_balance")
def test_the_committed_evidence_file_records_both_gates(project_root):
    """
    The balance gate is opt-in, so the evidence file is its standing record.
    An evidence file that drifted out of existence would leave the phase
    resting on a claim nobody can re-read.
    """
    path = project_root / "backend" / "tests" / "evidence" / \
        "phase6_x5_gate_evidence.txt"
    assert path.exists(), "the X5 gate evidence file is missing"
    text = path.read_text(encoding="utf-8")
    for required in ("RANDOMIZATION BALANCE -- RAW FIGURES",
                     "COUNTERFACTUAL CONSISTENCY -- RAW COUNTS",
                     "VERDICT:"):
        assert required in text, f"evidence file lacks {required!r}"
