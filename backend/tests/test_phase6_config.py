"""
Phase 6 / X0 -- the declared bounds are enforced, not just written down.

Same two kinds of check as `test_phase5_config.py`:

1. **Agreement checks.** `phase6_config.py` is the executable source of truth
   and `locked_thresholds.json` is the dated, human-auditable record of the
   same numbers. Two copies of a threshold is two chances to change one and
   not the other, so every shared value is asserted equal here. This is what
   makes the JSON block evidence rather than decoration.

2. **Ruling checks.** Values encoding a recorded 2026-09-04 ruling rather than
   a measurement. These pin the ruling so reversing it has to be deliberate
   and visible in a diff.

Plus one property check the randomization actually needs: the bucketing has to
be uniform and independent of the covariates it will later be balance-tested
on. That claim is structural (see phase6_config.assignment_bucket), but a
structural argument that is never exercised is just a comment.
"""

import json
from pathlib import Path

import pytest

from backend.db import db
from backend.engine import phase6_config as cfg

LOCK_PATH = (Path(__file__).resolve().parent.parent
             / "data_factory" / "locked_thresholds.json")


@pytest.fixture(scope="module")
def lock():
    with open(LOCK_PATH, encoding="utf-8") as f:
        return json.load(f)["phase6_experiment_assignment"]


@pytest.fixture(scope="module")
def cf_lock():
    with open(LOCK_PATH, encoding="utf-8") as f:
        return json.load(f)["phase6_counterfactual_consistency"]


# --------------------------------------------------------------------------
# Agreement -- the two records of the same number must not drift
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.declared_bounds")
def test_the_lock_file_block_exists_and_is_dated():
    """A threshold with no lock date cannot be shown to predate its result."""
    with open(LOCK_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    for block in ("phase6_experiment_assignment",
                  "phase6_counterfactual_consistency"):
        assert block in raw, f"{block} missing from locked_thresholds.json"
        assert raw[block].get("_locked_at_utc"), f"{block} carries no lock date"


@pytest.mark.gate("phase6.declared_bounds")
def test_assignment_parameters_agree_with_the_lock_file(lock):
    assert cfg.ASSIGNMENT_METHOD == lock["assignment_method"]
    assert cfg.ASSIGNMENT_SALT == lock["assignment_salt"]
    assert cfg.HOLDOUT_FRACTION == lock["holdout_fraction"]
    assert cfg.MIN_ASSIGNED_N == lock["min_assigned_n"]


@pytest.mark.gate("phase6.declared_bounds")
def test_balance_gate_parameters_agree_with_the_lock_file(lock):
    assert cfg.MAX_ABS_SMD == lock["max_abs_smd"]
    assert cfg.MIN_EXPECTED_ARM_COUNT == lock["min_expected_arm_count"]
    assert cfg.MAX_EXCLUDED_COVERAGE == lock["max_excluded_coverage"]
    assert list(cfg.CONTINUOUS_COVARIATES) == lock["continuous_covariates"]

    locked_cat = lock["categorical_covariates"]
    assert set(cfg.CATEGORICAL_COVARIATES) == set(locked_cat)
    for name, levels in cfg.CATEGORICAL_COVARIATES.items():
        assert sorted(levels) == sorted(locked_cat[name]["levels"]), \
            f"{name} level list has drifted between config and lock file"


@pytest.mark.gate("phase6.declared_bounds")
def test_counterfactual_gate_parameters_agree_with_the_lock_file(cf_lock):
    assert cfg.COUNTERFACTUAL_CONTROL_EXPECTED == cf_lock["control_expected_count"]
    assert cfg.COUNTERFACTUAL_TREATMENT_MIN == cf_lock["treatment_min_count"]


@pytest.mark.gate("phase6.declared_bounds")
def test_the_degenerate_smd_convention_agrees_with_the_lock_file(lock):
    conv = lock["degenerate_smd_convention"]
    assert cfg.DEGENERATE_SMD_BALANCED == conv["zero_denominator_zero_numerator"]
    assert cfg.DEGENERATE_SMD_IMBALANCED == float("inf")
    assert "FAIL" in conv["zero_denominator_nonzero_numerator"]


# --------------------------------------------------------------------------
# Derivation -- the declared level list must track its named source
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.declared_bounds")
def test_diagnosis_levels_cover_the_entry_points_accepted_vocabulary():
    """
    The balance gate's level list is declared, not derived from observed data,
    so a level drawing zero rows still reports as absent. The cost of
    declaring it is that it can fall behind the vocabulary the entry point
    actually accepts -- at which point the gate silently stops covering a root
    cause that real opportunities are still being created with.
    """
    from backend.engine.trigger_event import (VALID_EVENT_TYPES,
                                              VALID_ROOT_CAUSES)
    expected = set(VALID_ROOT_CAUSES) | (set(VALID_EVENT_TYPES)
                                         - {"payment_failed"})
    assert set(cfg.CATEGORICAL_COVARIATES["diagnosis"]) == expected


@pytest.mark.gate("phase6.declared_bounds")
def test_is_payment_failed_is_not_a_duplicate_of_a_diagnosis_level():
    """
    is_payment_failed earns its place only because it is a COARSENING of
    diagnosis, not a copy of one of its levels. If it ever became the latter
    it would be gating the same signal twice, which is exactly why the
    original event_type covariate was cut down to this binary.
    """
    assert set(cfg.CATEGORICAL_COVARIATES["is_payment_failed"]) == {"yes", "no"}
    overlap = (set(cfg.CATEGORICAL_COVARIATES["is_payment_failed"])
               & set(cfg.CATEGORICAL_COVARIATES["diagnosis"]))
    assert not overlap, f"level names collide across covariates: {overlap}"


# --------------------------------------------------------------------------
# Rulings -- pinned so reversing one has to be deliberate
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.declared_bounds")
def test_suppression_outcome_is_in_the_closed_compliance_vocabulary():
    assert cfg.SUPPRESSION_OUTCOME in db.DECISION_OUTCOMES
    assert cfg.SUPPRESSION_OUTCOME != "flagged_manual_review", (
        "reusing flagged_manual_review for holdout suppression would inject "
        "the entire control arm into the manual-review queue")


@pytest.mark.gate("phase6.declared_bounds")
def test_the_compliance_and_execution_vocabularies_stay_disjoint():
    """
    Phase 5's separation gate, re-asserted after Phase 6 widened
    DECISION_OUTCOMES. A value in both tables' vocabularies would let a query
    conflate a compliance verdict with a lifecycle state.
    """
    overlap = set(db.DECISION_OUTCOMES) & set(db.EXECUTION_STATES)
    assert overlap == {"executed"}, (
        "the only permitted shared token is 'executed', whose two meanings are "
        f"documented; found {sorted(overlap)}")


@pytest.mark.gate("phase6.declared_bounds")
def test_resolution_vocabulary_carries_lost_and_not_partially_recovered():
    assert "lost" in db.RESOLUTION_TYPES, (
        "`lost` (money definitively gone, observed) is not a synonym for "
        "`stopped` (case closed by policy); ruling 2026-09-04")
    assert "partially_recovered" not in db.RESOLUTION_TYPES, (
        "partial recovery is inferred from partial_recovery_amount; adding a "
        "value would force every recovery query to match two")
    assert cfg.PARTIAL_RECOVERY_IS_INFERRED


@pytest.mark.gate("phase6.declared_bounds")
def test_outcome_sources_are_a_closed_vocabulary_naming_every_caller():
    assert set(db.OUTCOME_SOURCES) == {
        "manual_confirmation", "executor_stop", "payment_event",
        "synthetic_potential_outcome"}


@pytest.mark.gate("phase6.declared_bounds")
def test_the_synthetic_source_is_distinct_from_every_real_one():
    """
    A synthetic outcome must be distinguishable from a confirmed one in the
    DATA, not merely in the narration around it. Folding it into
    `payment_event` would make "is this figure synthetic?" unanswerable by
    query, and presenting a synthetic result as a production one is the single
    most damaging claim this project could make.
    """
    assert "synthetic_potential_outcome" in db.OUTCOME_SOURCES
    assert "synthetic_potential_outcome" != "payment_event"
    from backend.data.generate_experiment_outcomes import SOURCE
    assert SOURCE == "synthetic_potential_outcome"
    assert SOURCE in db.OUTCOME_SOURCES


@pytest.mark.gate("phase6.declared_bounds")
def test_unassigned_opportunities_are_not_suppressed():
    """
    The one deliberate fail-OPEN in a system that otherwise fails closed,
    recorded as an exception rather than left looking like an oversight.
    Failing closed would freeze all 150 pre-Phase-6 seeded opportunities,
    none of which was ever randomized.
    """
    assert cfg.UNASSIGNED_IS_SUPPRESSED is False


@pytest.mark.gate("phase6.declared_bounds")
def test_the_smd_bound_is_the_balance_convention_not_the_effect_size_one():
    """
    0.20 is Cohen's small-EFFECT-SIZE convention and measures a different
    construct. An earlier draft conflated the two; this pins the correction so
    the bound cannot drift back.
    """
    assert cfg.MAX_ABS_SMD == 0.10


# --------------------------------------------------------------------------
# The randomization property the balance gate will later be asked to confirm
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.declared_bounds")
def test_bucketing_is_deterministic_and_salt_dependent():
    ids = [f"opp_{i:012x}" for i in range(500)]
    assert all(cfg.assignment_bucket(i) == cfg.assignment_bucket(i) for i in ids)
    assert all(0.0 <= cfg.assignment_bucket(i) < 1.0 for i in ids)

    # The live function must agree with the formula recorded in the lock file.
    assert all(cfg.assignment_bucket(i) == _bucket_with(cfg.ASSIGNMENT_SALT, i)
               for i in ids)

    # A different salt must produce a different partition, or the salt is
    # decorative and the bucketing is a bare hash of a public identifier.
    # Computed through the local formula rather than by mutating module state.
    moved = sum(1 for i in ids
                if _bucket_with(cfg.ASSIGNMENT_SALT + "-perturbed", i)
                != _bucket_with(cfg.ASSIGNMENT_SALT, i))
    assert moved > len(ids) * 0.9, (
        f"only {moved}/{len(ids)} buckets moved when the salt changed")


def _bucket_with(salt, opportunity_id):
    import hashlib
    digest = hashlib.blake2b(
        (salt + ":" + opportunity_id).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


@pytest.mark.gate("phase6.declared_bounds")
def test_bucketing_is_uniform_enough_to_hit_the_declared_holdout():
    """
    Not a balance test -- that is X5's hard gate, against real assigned
    opportunities. This is the far weaker precondition that the draw itself is
    uniform, checked on ids drawn the way trigger_event mints them. If this
    fails, the balance gate cannot be interpreted at all.
    """
    import uuid
    ids = ["opp_" + uuid.uuid4().hex[:12] for _ in range(20000)]
    control = sum(1 for i in ids
                  if cfg.assigned_group(i) == cfg.CONTROL_GROUP)
    share = control / len(ids)
    # +/- 0.02 around the declared fraction. At n=20000 the standard error of
    # a 0.5 proportion is ~0.0035, so this is a ~5.7-sigma band -- loose
    # enough never to flake, tight enough to catch a real bias.
    assert abs(share - cfg.HOLDOUT_FRACTION) < 0.02, (
        f"control share {share:.4f} vs declared {cfg.HOLDOUT_FRACTION}")


@pytest.mark.gate("phase6.declared_bounds")
def test_assignment_method_record_is_self_describing():
    """
    A stored assignment must stay interpretable if this config is later
    amended for a different population, so the row records the method, salt
    and fraction that actually produced it.
    """
    record = cfg.assignment_method_record()
    assert cfg.ASSIGNMENT_METHOD in record
    assert cfg.ASSIGNMENT_SALT in record
    assert str(cfg.HOLDOUT_FRACTION) in record
