"""
C1 -- ranking correctness, re-implemented.

WHAT THE OLD TEST DID WRONG
    test_phase4_optimizer.py::test_higher_true_incremental_value_ranks_above_lower
    compared the generator's PROBABILITY-space ground truth against the model's
    RUPEE-space output:

        truth = a["analytic_p"]           - b["analytic_p"]            # probability
        model = a["incremental_amount"]   - b["incremental_amount"]    # rupees

    Those orderings are allowed to disagree: E[amount|recovered] varies per
    candidate, which is the measured ~16% rupee-space pair-order sensitivity
    disclosed in PHASE4_NOTES section 8.6. Phase 4 isolated the fault exactly
    (section 8.2) -- same contexts and same ground truth, varying only the
    comparison axis, scored 0.958 in probability space and 0.812 in rupee
    space. The comparison space was the bug, not the ground truth.

    It also evaluated on hand-constructed contexts rather than data the model
    was fit on -- the same error that produced the retracted C2 finding.

WHAT THIS DOES INSTEAD
    Implements `locked_thresholds.json / phase3_temporal /
    ranking_pair_definition` verbatim. That definition was locked
    2026-08-30T15:32:43Z -- before Phase 4 began, and long before this
    measurement was conceived:

        within one holdout case, an ordered candidate pair (A, B) is eligible
        when the generator's analytic TREATMENT EFFECT for A
        (analytic_p(A) - analytic_p(do_nothing)) exceeds that for B by at
        least ranking_effect_size_floor (0.05); the model passes the pair when
        the model's estimated treatment effect for A
        (model_p(A) - model_p(do_nothing)) is >= that for B.

    Like-for-like: probability-space treatment effect on both sides, both
    measured against the same do_nothing baseline within the same case, on
    frozen held-out data the model never trained on.

    The pairing and effect computation are not reimplemented here -- they come
    from ml/evaluate_outcome_model.compute_effects(), the same function the
    trusted phase3_temporal gate uses. Reusing it is the point: a second
    implementation of "what is a treatment effect" is how two gates end up
    measuring different things while claiming to measure one.

ON THRESHOLDS, AND WHY NO BAR WAS LOOSENED
    Asserted at TWO operating points, so the result cannot depend on which bar
    is chosen:

      floor 0.05, bar 0.85  -- the locked phase3_temporal pair definition and
                              its own locked bar
      floor 0.12, bar 0.90  -- the floor and bar Phase 4's G7 reported against
                              (PHASE4_NOTES section 8.1)

    The old test carried a 0.90 bar borrowed, by its own comment, from "Phase
    3's own 0.90 bar" -- which belongs to ground_truth_treatment_effect,
    a bucket-level direction measurement, not pairwise within-case ranking.
    Agreement rises monotonically with the effect floor (measured: 0.889 at
    0.05, 0.928 at 0.08, 0.951 at 0.12, 0.991 at 0.20 on the temporal
    holdout), so a bar is only meaningful alongside the floor it was set at.
    Both bars are met at their own floors; neither was moved.
"""

import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
EVAL_DIR = BACKEND / "data_factory" / "phase3_eval"
MODEL_PATH = BACKEND / "ml" / "models" / "outcome_model.joblib"
LOCKS = json.loads((BACKEND / "data_factory" / "locked_thresholds.json")
                   .read_text(encoding="utf-8"))["phase3_temporal"]

LOCKED_FLOOR = LOCKS["ranking_effect_size_floor"]              # 0.05
LOCKED_BAR = LOCKS["min_ranking_direction_agreement"]          # 0.85
MIN_PAIRS = LOCKS["min_ranking_pairs"]                         # 500

# The operating point Phase 4's G7 reported against, kept so this test also
# covers the claim the hand-off actually makes.
G7_FLOOR, G7_BAR = 0.12, 0.90

HOLDOUTS = [
    ("temporal_holdout", "phase3_baseline_seed42_temporal_holdout.csv"),
    ("calibration_holdout", "phase3_baseline_seed42_calibration_holdout.csv"),
]

needs_data = pytest.mark.skipif(
    not MODEL_PATH.exists()
    or not (EVAL_DIR / "phase3_baseline_seed42_truth.csv").exists(),
    reason="frozen Phase 3 eval data / model artifact absent (both gitignored)")


def _agreement(eff, floor):
    """Ordered within-case pairs, per the locked definition."""
    n_correct = n_pairs = 0
    for _case_id, sub in eff.groupby("case_id"):
        recs = sub[["analytic_effect", "model_effect"]].to_dict("records")
        for i in range(len(recs)):
            for j in range(len(recs)):
                if i == j:
                    continue
                a, b = recs[i], recs[j]
                if a["analytic_effect"] - b["analytic_effect"] >= floor:
                    n_pairs += 1
                    if a["model_effect"] >= b["model_effect"]:
                        n_correct += 1
    return n_correct, n_pairs


@pytest.fixture(scope="module")
def effects(tmp_path_factory):
    """
    compute_effects() per holdout -- the same function phase3_temporal uses.

    The network-health lookup is built from an ISOLATED, schema-only database,
    never the ambient recovery.db. Two reasons:

      - determinism. The lookup feeds four model features, so a gate whose
        number moves with whatever happens to be in the developer's local DB is
        not a gate. Observed while wiring this up: the same assertions reported
        0.8886 against an empty DB and 0.8890 against a populated one.
      - isolation. A correctness gate must not read, and cannot be allowed to
        write, real application state.

    With no observations the lookup returns known=False for every row, so all
    rows are evaluated in one consistent regime. That regime is a property of
    the measurement, not of the machine it runs on, which is what makes the
    numbers here comparable across runs and across checkouts.
    """
    import joblib
    import pandas as pd

    from backend.db import db as db_module
    from backend.ml import evaluate_outcome_model as ev
    from backend.ml import outcome_features as feats

    db_path = tmp_path_factory.mktemp("ranking") / "isolated.db"
    original_path = db_module.DB_PATH
    db_module.DB_PATH = db_path
    try:
        conn = db_module.get_connection()
        db_module.create_schema(conn)
    finally:
        db_module.DB_PATH = original_path

    artifact = joblib.load(MODEL_PATH)
    health = feats.NetworkHealthLookup(feats.load_health_observations(conn))
    truth = pd.read_csv(EVAL_DIR / "phase3_baseline_seed42_truth.csv")

    out = {}
    for label, fname in HOLDOUTS:
        path = EVAL_DIR / fname
        if not path.exists():
            continue
        out[label] = ev.compute_effects(artifact, health, pd.read_csv(path), truth)
    conn.close()
    return out


@needs_data
@pytest.mark.gate("phase4.ranking")
@pytest.mark.parametrize("label,_fname", HOLDOUTS)
def test_ranking_direction_agreement_at_the_locked_floor(effects, label, _fname, capsys):
    """
    The locked phase3_temporal criterion, applied to held-out data.

    The measured number is printed whether it passes or fails, so the result is
    visible rather than collapsed into a boolean.
    """
    eff = effects.get(label)
    if eff is None:
        pytest.skip(f"{label} holdout not present")

    n_correct, n_pairs = _agreement(eff, LOCKED_FLOOR)
    with capsys.disabled():
        print(f"\n  [{label}] floor {LOCKED_FLOOR}: "
              f"{n_correct}/{n_pairs} = {n_correct/max(n_pairs,1):.4f} "
              f"(bar {LOCKED_BAR})")

    assert n_pairs >= MIN_PAIRS, (
        f"{label}: {n_pairs} decisive pairs < locked minimum {MIN_PAIRS}; "
        "too few to conclude anything")
    agreement = n_correct / n_pairs
    assert agreement >= LOCKED_BAR, (
        f"{label}: ranking-direction agreement {agreement:.4f} < locked bar "
        f"{LOCKED_BAR} over {n_pairs} decisive pairs at effect floor "
        f"{LOCKED_FLOOR}")


@needs_data
@pytest.mark.gate("phase4.ranking")
@pytest.mark.parametrize("label,_fname", HOLDOUTS)
def test_ranking_direction_agreement_at_the_g7_operating_point(effects, label,
                                                               _fname, capsys):
    """
    The same measurement at the floor and bar Phase 4's G7 reported against, so
    this test also covers the claim the hand-off makes rather than only the
    locked one. Asserting both is what makes the choice of bar irrelevant.
    """
    eff = effects.get(label)
    if eff is None:
        pytest.skip(f"{label} holdout not present")

    n_correct, n_pairs = _agreement(eff, G7_FLOOR)
    with capsys.disabled():
        print(f"  [{label}] floor {G7_FLOOR}: "
              f"{n_correct}/{n_pairs} = {n_correct/max(n_pairs,1):.4f} "
              f"(bar {G7_BAR})")

    assert n_pairs >= 100, f"{label}: only {n_pairs} pairs at floor {G7_FLOOR}"
    agreement = n_correct / n_pairs
    assert agreement >= G7_BAR, (
        f"{label}: ranking-direction agreement {agreement:.4f} < {G7_BAR} over "
        f"{n_pairs} decisive pairs at effect floor {G7_FLOOR}")


@needs_data
@pytest.mark.gate("phase4.ranking")
def test_agreement_rises_with_the_effect_floor(effects, capsys):
    """
    A sanity property, and the reason a bar is meaningless without its floor:
    pairs the generator separates more decisively must be ordered correctly at
    least as often. If this ever inverted, the measurement itself would be
    suspect -- it would mean the model does worse on the easier pairs.
    """
    eff = effects.get("temporal_holdout")
    if eff is None:
        pytest.skip("temporal holdout not present")

    rates = []
    for floor in (0.05, 0.08, 0.12):
        n_correct, n_pairs = _agreement(eff, floor)
        rates.append((floor, n_correct / max(n_pairs, 1), n_pairs))
    with capsys.disabled():
        print("  [temporal_holdout] agreement by effect floor: "
              + ", ".join(f"{f}: {r:.4f} (n={n})" for f, r, n in rates))

    assert rates[0][1] <= rates[1][1] <= rates[2][1], (
        "agreement did not rise with the effect floor: "
        + ", ".join(f"{f}: {r:.4f}" for f, r, _ in rates))
