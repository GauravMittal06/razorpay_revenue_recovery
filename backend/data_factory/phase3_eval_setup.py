"""
Phase 3 -- Step 1 eval-artifact setup (Decisions A / F / G).

NOT model/training code. This script only:
  1. Regenerates, deterministically, the datasets Phase 3 will consume:
       - baseline profile, seeds 42 / 43 / 44   (Decision F: multi-seed)
       - stress profile, seed 42                 (Decision G: cross-profile holdout)
     all at DF_N_CASES (default 3000 -- the Phase 2 formal-gate size).
  2. Runs the Phase 2 structural validators against seeds 43 and 44 as a
     sanity gate (they must be as structurally valid as seed 42, or the
     multi-seed robustness check is meaningless).
  3. Carves the baseline seed-42 joint dataset into the 4-way split locked in
     locked_thresholds.json['phase3_data_split'] (Decision A):
       training_pool / calibration_holdout / temporal_holdout.
  4. Materializes every frozen artifact to backend/data_factory/phase3_eval/
     and sha256-commits it to phase3_eval/phase3_eval_lock.json (via
     eval_set_lock.py's new lock_phase3_artifact()). NOT under output/, which
     phase2_verify.py wipes on every run.
  5. Re-verifies every committed hash and prints a report.

Run from the directory containing backend/:

    python -m backend.data_factory.phase3_eval_setup

Env:
    DF_N_CASES     -- cases per generation run (default 3000)
    P3_SEEDS       -- comma-separated baseline seeds (default "42,43,44")

Exit code 0 iff every generation succeeded, every seed-43/44 sanity check
passed, the split is case- and customer-disjoint as required, and every
committed hash re-verifies.
"""

import os
import sys
import time
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_ROOT = _THIS_DIR.parent.parent
sys.path.insert(0, str(_ROOT))

import pandas as pd

from backend.data_factory import calibration_profiles as cp
from backend.data_factory import candidate_outcome_dataset as cod
from backend.data_factory import validators as val
from backend.data_factory import eval_set_lock as evl

N_CASES = int(os.environ.get("DF_N_CASES", "3000"))
BASELINE_SEEDS = [int(s) for s in os.environ.get("P3_SEEDS", "42,43,44").split(",")]
PRIMARY_SEED = 42
GENERATOR_VERSION = cod.GENERATOR_VERSION

_results = []
_warnings = []


def check(label, ok, detail=None):
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {label}"
    if detail is not None:
        line += f"  -- {detail}"
    print(line)
    _results.append((label, bool(ok)))
    return bool(ok)


def advisory(label, ok, detail=None):
    """A reported check that does NOT gate the run -- used for the seed-43/44
    Phase-2 ground-truth direction check, which is known-seed-sensitive (see
    STATE_AND_DECISIONS.md) and is NOT the tolerance Phase 3's multi-seed gate
    actually uses (that is the looser, separately-locked phase3_treatment_effect
    per Decision D). Surfaced loudly, never swallowed."""
    status = "ok " if ok else "WARN"
    line = f"[{status}] (advisory) {label}"
    if detail is not None:
        line += f"  -- {detail}"
    print(line)
    if not ok:
        _warnings.append((label, detail))
    return bool(ok)


def section(title):
    print("\n" + "=" * 88 + f"\n{title}\n" + "=" * 88)


def sanity_validate(profile_name, seed, joint_df, truth_df, thresholds):
    """Phase 2 structural validators, re-run against a non-42 seed."""
    r = val.leakage_case_level(joint_df, thresholds)
    check(f"[{profile_name} seed{seed}] case-level leakage zero overlap", r["passed"], r)
    r = val.leakage_customer_level(joint_df, thresholds)
    check(f"[{profile_name} seed{seed}] customer-level leakage zero overlap", r["passed"], r)
    r = val.leakage_temporal_order(joint_df, thresholds)
    check(f"[{profile_name} seed{seed}] temporal-order leakage none", r["passed"], r)
    r = val.hidden_state_once_per_case_check(truth_df)
    check(f"[{profile_name} seed{seed}] hidden state once per case", r["passed"], r)
    r = val.distributional_sanity_check(joint_df, thresholds)
    check(f"[{profile_name} seed{seed}] distributional sanity", r["passed"], r)
    r = val.candidate_timing_validity_check(joint_df, thresholds)
    check(f"[{profile_name} seed{seed}] candidate timing validity", r["passed"], r)
    r = val.fatigue_significance_check(joint_df, thresholds)
    check(f"[{profile_name} seed{seed}] fatigue significance (sign+alpha)", r["passed"],
          {"r": r.get("correlation"), "p": r.get("p_value"), "n": r.get("n")})
    r = val.ground_truth_treatment_effect_check(joint_df, truth_df, thresholds)
    advisory(f"[{profile_name} seed{seed}] ground-truth treatment effect (Phase 2 tolerance, "
             f"informational only)",
             r["passed"],
             {"frac_dir": r["fraction_buckets_matching_direction"],
              "gaps_ok": r["all_gaps_within_tolerance"],
              "dir_scored": r["buckets_direction_scored"],
              "evaluated": r["buckets_evaluated"]})


def main():
    t0 = time.time()
    thresholds = val.load_thresholds()

    if "phase3_data_split" not in thresholds:
        print("FATAL: locked_thresholds.json has no phase3_data_split block -- lock Step 1 thresholds first.")
        return 1

    section(f"Phase 3 eval setup -- N_CASES={N_CASES}, baseline seeds={BASELINE_SEEDS}, "
            f"generator={GENERATOR_VERSION}")

    # ------------------------------------------------------------------ generate
    baseline_by_seed = {}
    for seed in BASELINE_SEEDS:
        section(f"Generate baseline profile, seed={seed}")
        j, t, *_ = cod.generate_dataset(cp.get_profile("baseline"), seed, n_cases=N_CASES)
        baseline_by_seed[seed] = (j, t)
        check(f"baseline seed{seed} generated non-empty", len(j) > 0,
              f"{len(j)} rows / {j['case_id'].nunique()} cases")
        check(f"baseline seed{seed} has do_nothing for every case",
              (j.groupby("case_id")["candidate_action"].apply(lambda s: "do_nothing" in set(s))).all())
        if seed != PRIMARY_SEED:
            sanity_validate("baseline", seed, j, t, thresholds)

    section("Generate stress profile, seed=42 (cross-profile holdout)")
    stress_joint, stress_truth, *_ = cod.generate_dataset(cp.get_profile("stress"), PRIMARY_SEED, n_cases=N_CASES)
    check("stress seed42 generated non-empty", len(stress_joint) > 0,
          f"{len(stress_joint)} rows / {stress_joint['case_id'].nunique()} cases")

    # --------------------------------------------------------------------- carve
    section("Carve baseline seed-42 4-way split (phase3_data_split)")
    b42_joint, b42_truth = baseline_by_seed[PRIMARY_SEED]
    splits, meta = evl.carve_phase3_splits(b42_joint, thresholds)
    for k, v in meta.items():
        print(f"  {k}: {v}")
    check("split is case-disjoint across all 3 slices", meta["case_disjoint"])
    check("training_pool vs calibration_holdout customer overlap == 0",
          meta["training_vs_calibration_customer_overlap"] == 0,
          meta["training_vs_calibration_customer_overlap"])
    cfg = thresholds["phase3_data_split"]
    check("calibration_holdout >= min_holdout_cases_each",
          meta["n_cases_calibration_holdout"] >= cfg["min_holdout_cases_each"],
          f'{meta["n_cases_calibration_holdout"]} >= {cfg["min_holdout_cases_each"]}')
    check("temporal_holdout >= min_holdout_cases_each",
          meta["n_cases_temporal_holdout"] >= cfg["min_holdout_cases_each"],
          f'{meta["n_cases_temporal_holdout"]} >= {cfg["min_holdout_cases_each"]}')
    # every candidate row is in exactly one slice
    total_rows = sum(len(v) for v in splits.values())
    check("split partitions every candidate row exactly once", total_rows == len(b42_joint),
          f"{total_rows} vs {len(b42_joint)}")

    # ---------------------------------------------------------------- hash-commit
    section("Hash-commit every frozen Phase 3 artifact -> phase3_eval/phase3_eval_lock.json")
    common = {"profile": "baseline", "seed": PRIMARY_SEED, "generator_version": GENERATOR_VERSION,
              "n_cases_generation": N_CASES}

    entries = []
    entries.append(evl.lock_phase3_artifact("phase3_baseline_seed42_joint", b42_joint,
                   {**common, "kind": "full_joint_dataset"}))
    entries.append(evl.lock_phase3_artifact("phase3_baseline_seed42_truth", b42_truth,
                   {**common, "kind": "ground_truth_companion"}))
    entries.append(evl.lock_phase3_artifact("phase3_baseline_seed42_training_pool",
                   splits["training_pool"], {**common, "kind": "training_pool_SEEN",
                   "note": "Phase 3 splits train / model-selection-val from this at fit time"}))
    entries.append(evl.lock_phase3_artifact("phase3_baseline_seed42_calibration_holdout",
                   splits["calibration_holdout"], {**common, "kind": "calibration_holdout_UNSEEN",
                   "gate": "phase3_calibration", "carve_seed": meta["carve_seed"]}))
    entries.append(evl.lock_phase3_artifact("phase3_baseline_seed42_temporal_holdout",
                   splits["temporal_holdout"], {**common, "kind": "temporal_holdout_UNSEEN",
                   "gate": "phase3_temporal", "sim_hour_boundary": meta["sim_hour_boundary"]}))
    entries.append(evl.lock_phase3_artifact("phase3_stress_seed42_joint", stress_joint,
                   {"profile": "stress", "seed": PRIMARY_SEED, "generator_version": GENERATOR_VERSION,
                    "n_cases_generation": N_CASES, "kind": "cross_profile_holdout_UNSEEN",
                    "gate": "phase3_cross_profile"}))
    entries.append(evl.lock_phase3_artifact("phase3_stress_seed42_truth", stress_truth,
                   {"profile": "stress", "seed": PRIMARY_SEED, "generator_version": GENERATOR_VERSION,
                    "n_cases_generation": N_CASES, "kind": "ground_truth_companion"}))
    for seed in BASELINE_SEEDS:
        if seed == PRIMARY_SEED:
            continue
        j, t = baseline_by_seed[seed]
        entries.append(evl.lock_phase3_artifact(f"phase3_baseline_seed{seed}_joint", j,
                       {"profile": "baseline", "seed": seed, "generator_version": GENERATOR_VERSION,
                        "n_cases_generation": N_CASES, "kind": "multiseed_SUPPORTING",
                        "gate": "phase3_multiseed"}))
        entries.append(evl.lock_phase3_artifact(f"phase3_baseline_seed{seed}_truth", t,
                       {"profile": "baseline", "seed": seed, "generator_version": GENERATOR_VERSION,
                        "n_cases_generation": N_CASES, "kind": "ground_truth_companion"}))

    for e in entries:
        print(f"  locked {e['artifact']:48s} rows={e['row_count']:>7d} "
              f"cases={str(e['case_count']):>6s} sha256={e['sha256'][:16]}...")

    # ----------------------------------------------------------------- re-verify
    section("Re-verify every committed hash")
    v = evl.verify_all_phase3()
    for name, r in v["results"].items():
        check(f"hash re-verifies: {name}", r["passed"],
              None if r["passed"] else r)
    check(f"ALL {v['n_artifacts']} Phase 3 artifacts hash-verified", v["passed"])

    # ------------------------------------------------------------------- verdict
    section("VERDICT")
    npass = sum(1 for _, ok in _results if ok)
    print(f"{npass}/{len(_results)} gating checks passed   ({time.time() - t0:.1f}s)")
    if _warnings:
        print(f"\n{len(_warnings)} ADVISORY WARNING(S) -- not gating, but must be reviewed before "
              f"Phase 3 relies on the affected artifact:")
        for lbl, detail in _warnings:
            print(f"  - {lbl}  -- {detail}")
    failed = [lbl for lbl, ok in _results if not ok]
    if failed:
        print("\nFAILED (gating):")
        for lbl in failed:
            print(f"  - {lbl}")
        return 1
    print("\nRESULT: Phase 3 eval artifacts generated, split carved, and every hash committed + verified.")
    if _warnings:
        print("        (see advisory warnings above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
