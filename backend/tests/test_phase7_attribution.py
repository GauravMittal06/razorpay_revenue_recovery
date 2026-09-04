"""
Phase 7 — incremental attribution, at aggregate scope.

SCOPE DISCLOSED UP FRONT
    Segmented attribution is not built (ruling 2026-09-04, cut for time), so
    the acceptance gate's per-segment underpowered-refusal test is NOT met.
    What IS met and tested here: the estimator is named and correct, a CI is
    reported, every figure is traceable to a live query, the minimum-N refusal
    is enforced as a permanent gate, predicted and observed are separate
    quantities, and every output is labelled synthetic.

    PHASE7_NOTES.md records the cut explicitly and dated. These tests do not
    pretend to cover it.
"""

import math

import pytest

from backend.analytics import incremental_attribution as ia
from backend.data.generate_experiment_outcomes import SOURCE
from backend.engine import phase6_config as cfg
from backend.tests.conftest import make_opportunity


def _seed_arm(conn, prefix, arm, n, recovered, amount=10000,
              recovered_amount=None):
    """n opportunities in `arm`, of which `recovered` recovered."""
    for i in range(n):
        oid = f"{prefix}_{arm}_{i:04d}"
        make_opportunity(conn, opportunity_id=oid, amount_at_risk=amount,
                         created_at=1000)
        conn.execute(
            'INSERT INTO experiment_assignment '
            '(opportunity_id, "group", assigned_at, assignment_method) '
            "VALUES (?, ?, ?, ?)",
            (oid, arm, 1000, cfg.assignment_method_record()))
        if i < recovered:
            conn.execute(
                "UPDATE opportunities SET resolution_type='recovered', "
                "recovered_bool=1, partial_recovery_amount=?, "
                "recovered_at=2000, resolved_at=2000, status='recovered', "
                "outcome_source=? WHERE opportunity_id=?",
                (recovered_amount if recovered_amount is not None else amount,
                 SOURCE, oid))
        else:
            conn.execute(
                "UPDATE opportunities SET resolution_type='lost', "
                "recovered_bool=0, partial_recovery_amount=0, "
                "resolved_at=2000, status='stopped', outcome_source=? "
                "WHERE opportunity_id=?", (SOURCE, oid))
    conn.commit()


# --------------------------------------------------------------------------
# The minimum-N gate -- permanent, not optional
# --------------------------------------------------------------------------

@pytest.mark.gate("phase7.min_n")
def test_an_underpowered_population_reports_insufficient_data(seeded_db):
    """
    THE estimator-misuse guard, at aggregate scope. A CI computed on a handful
    of rows is not a weak result, it is a misleading one -- wide enough to
    contain anything while still looking like a measurement.
    """
    _seed_arm(seeded_db, "opp_p7_small", cfg.TREATMENT_GROUP, 10, 6)
    _seed_arm(seeded_db, "opp_p7_small", cfg.CONTROL_GROUP, 10, 3)

    report = ia.incremental_report(seeded_db)
    assert report["reportable"] is False
    assert "insufficient data" in report["refusal_reason"]
    # And no number leaked out alongside the refusal.
    assert "incremental_rate" not in report
    assert "incremental_rs_total" not in report
    assert "NOT REPORTABLE" in ia.format_report(report)


@pytest.mark.gate("phase7.min_n")
def test_one_arm_below_the_floor_is_still_a_refusal(seeded_db):
    """A well-powered treatment arm cannot compensate for a thin control arm:
    the difference is only as trustworthy as its weaker side."""
    _seed_arm(seeded_db, "opp_p7_lop", cfg.TREATMENT_GROUP, 200, 120)
    _seed_arm(seeded_db, "opp_p7_lop", cfg.CONTROL_GROUP, 5, 2)
    report = ia.incremental_report(seeded_db)
    assert report["reportable"] is False
    assert "insufficient data" in report["refusal_reason"]


@pytest.mark.gate("phase7.min_n")
def test_an_empty_population_refuses_rather_than_reporting_zero(seeded_db):
    report = ia.incremental_report(seeded_db)
    assert report["reportable"] is False


@pytest.mark.gate("phase7.min_n")
def test_the_floor_agrees_with_its_dated_lock_file_entry():
    """
    The constant and the dated lock must not drift, same discipline as every
    Phase 6 bound. This lock was recorded LATE -- 2026-09-05, retrospectively,
    in the same commit as the evaluation that used it -- and the block itself
    says so. The disclosure is asserted here so it cannot be quietly dropped
    to make the provenance look cleaner than it is.
    """
    import json
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent
            / "data_factory" / "locked_thresholds.json")
    block = json.loads(path.read_text(encoding="utf-8"))[
        "phase7_incremental_attribution"]

    assert block["min_n_per_arm"] == ia.MIN_N_PER_ARM
    assert block["_locked_at_utc"], "the lock carries no date"
    assert "_LOCKED_LATE__DISCLOSURE" in block, (
        "the late-lock disclosure was removed; this threshold does not have "
        "the same provenance as max_abs_smd and the record must say so")
    assert block["min_n_per_arm"] != json.loads(path.read_text(encoding="utf-8"))[
        "phase6_experiment_assignment"]["min_assigned_n"], (
        "the estimator floor and the balance floor answer different questions "
        "and must not be tied together")


@pytest.mark.gate("phase7.min_n")
def test_the_floor_is_a_declared_constant_not_a_literal():
    assert ia.MIN_N_PER_ARM >= 30, (
        "below ~30 per arm the normal approximation behind the Wald interval "
        "is not defensible")
    assert ia.MIN_N_PER_ARM != cfg.MIN_ASSIGNED_N, (
        "the estimator's floor and the balance gate's floor answer different "
        "questions and must not be silently tied together")


# --------------------------------------------------------------------------
# The estimator
# --------------------------------------------------------------------------

@pytest.mark.gate("phase7.estimator")
def test_difference_in_proportions_against_a_hand_computation():
    """
    p_t = 0.60, p_c = 0.40, n = 100 each.
      diff = 0.20
      se   = sqrt(.6*.4/100 + .4*.6/100) = sqrt(0.0048) = 0.0692820323
      ci   = 0.20 +/- 1.959963985 * 0.0692820323
    """
    diff, lo, hi, se = ia.difference_in_proportions(60, 100, 40, 100)
    assert diff == pytest.approx(0.20, abs=1e-12)
    assert se == pytest.approx(math.sqrt(0.0048), abs=1e-12)
    assert lo == pytest.approx(0.20 - 1.959963985 * math.sqrt(0.0048), abs=1e-9)
    assert hi == pytest.approx(0.20 + 1.959963985 * math.sqrt(0.0048), abs=1e-9)


@pytest.mark.gate("phase7.estimator")
def test_no_difference_gives_an_interval_straddling_zero():
    diff, lo, hi, _ = ia.difference_in_proportions(50, 100, 50, 100)
    assert diff == 0.0
    assert lo < 0 < hi, "an interval that excludes zero on identical arms"


@pytest.mark.gate("phase7.estimator")
def test_difference_in_means_uses_unpooled_variances():
    """
    Welch, not pooled. Two arms with deliberately different spreads: a pooled
    estimator would assume away the variance shift an effective intervention
    itself creates.
    """
    xs_t = [100.0, 100.0, 100.0, 100.0]
    xs_c = [0.0, 200.0, 0.0, 200.0]
    diff, lo, hi, se = ia.difference_in_means(xs_t, xs_c)
    assert diff == pytest.approx(0.0)
    # var_t = 0, var_c = 13333.33...; se = sqrt(0/4 + 13333.33/4)
    assert se == pytest.approx(math.sqrt(ia._var(xs_c) / 4), abs=1e-9)
    assert se > 0


@pytest.mark.gate("phase7.estimator")
def test_the_estimator_is_named_in_the_report(seeded_db):
    _seed_arm(seeded_db, "opp_p7_named", cfg.TREATMENT_GROUP, 60, 40)
    _seed_arm(seeded_db, "opp_p7_named", cfg.CONTROL_GROUP, 60, 20)
    report = ia.incremental_report(seeded_db)
    assert "difference in proportions" in report["estimator"]
    assert "Wald" in report["estimator"]
    assert "Welch" in report["estimator"]


@pytest.mark.gate("phase7.estimator")
def test_a_raw_recovery_rate_is_never_labelled_incremental(seeded_db):
    """
    The gate's exact words: "no raw recovery-rate number is mislabeled as
    incremental." The incremental figure must equal the DIFFERENCE, not either
    arm's own rate.
    """
    _seed_arm(seeded_db, "opp_p7_raw", cfg.TREATMENT_GROUP, 100, 70)
    _seed_arm(seeded_db, "opp_p7_raw", cfg.CONTROL_GROUP, 100, 40)
    report = ia.incremental_report(seeded_db)

    assert report["recovery_rate"]["treatment"] == pytest.approx(0.70)
    assert report["recovery_rate"]["control"] == pytest.approx(0.40)
    assert report["incremental_rate"]["estimate"] == pytest.approx(0.30)
    assert report["incremental_rate"]["estimate"] != \
        report["recovery_rate"]["treatment"]


@pytest.mark.gate("phase7.estimator")
def test_a_confidence_interval_accompanies_every_estimate(seeded_db):
    _seed_arm(seeded_db, "opp_p7_ci", cfg.TREATMENT_GROUP, 80, 50)
    _seed_arm(seeded_db, "opp_p7_ci", cfg.CONTROL_GROUP, 80, 30)
    report = ia.incremental_report(seeded_db)
    for key in ("incremental_rate", "incremental_rs_per_opportunity",
                "incremental_rs_total"):
        block = report[key]
        assert "ci_low" in block and "ci_high" in block
        assert block["ci_low"] <= block["estimate"] <= block["ci_high"]


# --------------------------------------------------------------------------
# Traceability and synthetic honesty
# --------------------------------------------------------------------------

@pytest.mark.gate("phase7.traceability")
def test_every_figure_is_reproducible_from_the_traced_query(seeded_db):
    """
    The traceability gate. The module publishes its own SQL; running that SQL
    by hand must reproduce the counts the report is built from. A number that
    can only be obtained by re-running the module is not auditable.
    """
    _seed_arm(seeded_db, "opp_p7_trace", cfg.TREATMENT_GROUP, 90, 55)
    _seed_arm(seeded_db, "opp_p7_trace", cfg.CONTROL_GROUP, 90, 35)
    report = ia.incremental_report(seeded_db)

    rows = seeded_db.execute(ia.sql_trace()["resolved_population"]).fetchall()
    treated = [r for r in rows if r["arm"] == cfg.TREATMENT_GROUP]
    control = [r for r in rows if r["arm"] == cfg.CONTROL_GROUP]

    assert len(treated) == report["n_resolved"]["treatment"]
    assert len(control) == report["n_resolved"]["control"]
    assert sum(1 for r in treated if r["recovered_bool"]) == \
        report["recovered"]["treatment"]

    hand_diff = (sum(1 for r in treated if r["recovered_bool"]) / len(treated)
                 - sum(1 for r in control if r["recovered_bool"]) / len(control))
    assert report["incremental_rate"]["estimate"] == pytest.approx(hand_diff)


@pytest.mark.gate("phase7.traceability")
def test_nothing_is_cached_between_calls(seeded_db):
    """A figure computed once and reused would silently go stale the moment
    another outcome landed."""
    _seed_arm(seeded_db, "opp_p7_cache_a", cfg.TREATMENT_GROUP, 60, 30)
    _seed_arm(seeded_db, "opp_p7_cache_a", cfg.CONTROL_GROUP, 60, 30)
    first = ia.incremental_report(seeded_db)
    assert first["incremental_rate"]["estimate"] == pytest.approx(0.0)

    _seed_arm(seeded_db, "opp_p7_cache_b", cfg.TREATMENT_GROUP, 60, 60)
    second = ia.incremental_report(seeded_db)
    assert second["incremental_rate"]["estimate"] > 0.0, \
        "the report did not move after new outcomes landed"


@pytest.mark.gate("phase7.synthetic_honesty")
def test_every_report_is_labelled_synthetic(seeded_db):
    _seed_arm(seeded_db, "opp_p7_syn", cfg.TREATMENT_GROUP, 60, 40)
    _seed_arm(seeded_db, "opp_p7_syn", cfg.CONTROL_GROUP, 60, 20)
    report = ia.incremental_report(seeded_db)
    assert report["synthetic"] is True
    text = ia.format_report(report)
    assert "SYNTHETIC" in text
    assert "NOT observed from any real payment system" in text
    assert "never be presented as production" in text


@pytest.mark.gate("phase7.synthetic_honesty")
def test_a_mixed_source_population_is_refused(seeded_db):
    """
    Aggregating a synthetic draw together with a confirmed payment event and
    reporting one figure is exactly the "synthetic ground truth presented as
    real-world causal evidence" do-not-proceed condition. The module refuses
    rather than footnoting it.
    """
    _seed_arm(seeded_db, "opp_p7_mix", cfg.TREATMENT_GROUP, 60, 40)
    _seed_arm(seeded_db, "opp_p7_mix", cfg.CONTROL_GROUP, 60, 20)
    seeded_db.execute(
        "UPDATE opportunities SET outcome_source='manual_confirmation' "
        "WHERE opportunity_id = 'opp_p7_mix_treatment_0000'")
    seeded_db.commit()

    report = ia.incremental_report(seeded_db)
    assert report["reportable"] is False
    assert "mixes outcome sources" in report["refusal_reason"]


@pytest.mark.gate("phase7.synthetic_honesty")
def test_the_report_declares_its_aggregate_only_scope(seeded_db):
    _seed_arm(seeded_db, "opp_p7_scope", cfg.TREATMENT_GROUP, 60, 40)
    _seed_arm(seeded_db, "opp_p7_scope", cfg.CONTROL_GROUP, 60, 20)
    report = ia.incremental_report(seeded_db)
    assert report["scope"] == "aggregate"
    assert "no segmentation" in ia.format_report(report)


# --------------------------------------------------------------------------
# Predicted vs observed
# --------------------------------------------------------------------------

@pytest.mark.gate("phase7.prediction_vs_observation")
def test_predicted_and_observed_are_separate_quantities(seeded_db):
    """
    The gate requires they be shown separately with divergence reported, not
    hidden -- and specifically NOT reconciled. A module that adjusted one to
    match the other would destroy the only signal this diagnostic carries.
    """
    _seed_arm(seeded_db, "opp_p7_diag", cfg.TREATMENT_GROUP, 60, 45)
    _seed_arm(seeded_db, "opp_p7_diag", cfg.CONTROL_GROUP, 60, 20)
    # A selected candidate carrying a predicted EIV, for one treated row.
    seeded_db.execute(
        "INSERT INTO recovery_candidates (opportunity_id, action_type, "
        "predicted_eiv, rank, selected, created_at) "
        "VALUES ('opp_p7_diag_treatment_0000', 'retry', 999.0, 1, 1, 1000)")
    seeded_db.commit()

    report = ia.incremental_report(seeded_db)
    diag = report["predicted_vs_observed"]
    assert diag["available"] is True
    assert diag["predicted_per_opportunity"] == pytest.approx(999.0)
    assert diag["observed_per_opportunity"] != diag["predicted_per_opportunity"]
    assert "not reconciled" in diag["note"]

    # BOTH denominators are published, and the like-for-like delta is computed
    # on the common one. Reporting a single delta across two different
    # populations is what made an earlier version of this diagnostic
    # unauditable: predicted was averaged over selected candidates only while
    # observed was averaged over all resolved treatment opportunities.
    assert diag["predicted_n"] == 1, "one selected candidate was seeded"
    assert diag["observed_n"] == report["n_resolved"]["treatment"]
    assert diag["predicted_n"] != diag["observed_n"], (
        "this fixture is meant to exercise the mismatched-denominator case")

    assert diag["predicted_per_treated_opportunity"] == pytest.approx(
        999.0 / diag["observed_n"])
    assert diag["delta_like_for_like"] == pytest.approx(
        diag["observed_per_opportunity"]
        - diag["predicted_per_treated_opportunity"])
    assert diag["delta_over_selected_only"] == pytest.approx(
        diag["observed_per_opportunity"] - diag["predicted_per_opportunity"])
    assert diag["delta_like_for_like"] != diag["delta_over_selected_only"]


@pytest.mark.gate("phase7.prediction_vs_observation")
def test_a_missing_prediction_is_reported_not_defaulted(seeded_db):
    """No selected candidates means no prediction to compare -- which must be
    said, not silently rendered as a zero delta."""
    _seed_arm(seeded_db, "opp_p7_nodiag", cfg.TREATMENT_GROUP, 60, 40)
    _seed_arm(seeded_db, "opp_p7_nodiag", cfg.CONTROL_GROUP, 60, 20)
    report = ia.incremental_report(seeded_db)
    assert report["predicted_vs_observed"]["available"] is False
    assert "no selected candidates" in report["predicted_vs_observed"]["note"]
