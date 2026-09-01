"""
Data Factory -- generation + validation entrypoint. Phase 2.

Run from the directory containing backend/ (same convention as
test_everything.py):

    python -m backend.data_factory.run_generation

Generates the joint candidate-outcome dataset under BOTH calibration
profiles (baseline, stress), runs every validator in validators.py
against each, checks cross-profile divergence, records both runs in the
dataset registry, and writes CSV output to backend/data_factory/output/.

Every check below prints its own PASS/FAIL line with the actual computed
number, not just a claim -- mirrors test_everything.py's discipline.
Exit code 0 if every check passed, 1 otherwise.
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
_ROOT = _BACKEND_DIR.parent
sys.path.insert(0, str(_ROOT))

from backend.data_factory import calibration_profiles as cp
from backend.data_factory import candidate_outcome_dataset as cod
from backend.data_factory import validators as val
from backend.data_factory import dataset_registry as registry
from backend.data_factory import eval_set_lock as evl

OUTPUT_DIR = _THIS_DIR / "output"
SEED = 42
DATASET_NAME = "joint_candidate_outcome"
DATASET_VERSION = "2.0.0"

results = []


def check(label, condition, detail=None):
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {label}"
    if detail is not None:
        line += f"  -- {detail}"
    print(line)
    results.append((label, bool(condition)))
    return condition


def section(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def run_for_profile(profile_name, n_cases):
    profile = cp.get_profile(profile_name)
    joint_df, truth_df, world, health_index, health_obs = cod.generate_dataset(
        profile, SEED, n_cases=n_cases
    )
    import pandas as pd
    health_obs_df = pd.DataFrame([o.__dict__ for o in health_obs])
    return joint_df, truth_df, world, health_obs_df


def main():
    n_cases = int(os.environ.get("DF_N_CASES", "3000"))
    thresholds = val.load_thresholds()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    section("STEP 0: Static authority-boundary check (no execution authority in Data Factory)")
    authority = val.static_no_execution_authority_check()
    check("No forbidden imports/calls to execution-authority code in data_factory/", authority["passed"],
          f"{len(authority['violations'])} violations across {len(authority['files_scanned'])} files scanned")

    section(f"STEP 1: Generate joint dataset -- profile=baseline, seed={SEED}, n_cases={n_cases}")
    t0 = time.time()
    baseline_joint, baseline_truth, baseline_world, baseline_health_df = run_for_profile("baseline", n_cases)
    print(f"Generated {len(baseline_joint)} candidate rows across {baseline_joint['case_id'].nunique()} cases "
          f"in {time.time() - t0:.1f}s")
    check("Baseline dataset non-empty", len(baseline_joint) > 0)
    check("Baseline dataset includes do_nothing for every case",
          set(baseline_joint.groupby("case_id")["candidate_action"].apply(lambda s: "do_nothing" in set(s))) == {True})

    section(f"STEP 2: Generate joint dataset -- profile=stress, seed={SEED}, n_cases={n_cases}")
    stress_joint, stress_truth, stress_world, stress_health_df = run_for_profile("stress", n_cases)
    print(f"Generated {len(stress_joint)} candidate rows across {stress_joint['case_id'].nunique()} cases")
    check("Stress dataset non-empty", len(stress_joint) > 0)

    section("STEP 3: Leakage checks (case-level, customer-level, temporal-order) -- baseline")
    r = val.leakage_case_level(baseline_joint, thresholds)
    check("Case-level leakage: zero overlap between grouped train/test split", r["passed"], r)
    r = val.leakage_customer_level(baseline_joint, thresholds)
    check("Customer-level leakage: zero overlap between grouped train/test split", r["passed"], r)
    r = val.leakage_temporal_order(baseline_joint, thresholds)
    check("Temporal-order leakage: no negative hours_since_last_action", r["passed"], r)

    section("STEP 4: Reproducibility -- identical seed+profile+generator_version, two-run diff")
    r = val.reproducibility_check(cod.generate_dataset, cp.get_profile("baseline"), SEED, thresholds,
                                   n_cases=min(n_cases, 500))
    check("Two independent runs with identical seed/profile produce byte-identical output", r["passed"], r)

    section("STEP 5: Ground-truth treatment-effect check -- baseline")
    r = val.ground_truth_treatment_effect_check(baseline_joint, baseline_truth, thresholds)
    check("Empirical treatment effect matches generator's analytic effect (within locked tolerance)",
          r["passed"], {"fraction_buckets_matching_direction": r["fraction_buckets_matching_direction"],
                         "all_gaps_within_tolerance": r["all_gaps_within_tolerance"],
                         "buckets_evaluated": r["buckets_evaluated"],
                         "buckets_skipped_insufficient_data": r["buckets_skipped_insufficient_data"],
                         "buckets_direction_scored": r["buckets_direction_scored"],
                         "buckets_effect_too_small_for_direction_test": r["buckets_effect_too_small_for_direction_test"]})
    print(f"  Granular buckets (action_type x event_type x root_cause_effect_class x method_changed):")
    for b in r["per_bucket"]:
        if b["skipped"]:
            print(f"    (SKIPPED -- NOT ENOUGH DATA) {b['bucket_key']}: n={b['n_cases']} -- {b['reason']}")
        elif not b["direction_scored"]:
            print(f"    (GAP-TESTED, DIRECTION NOT SCORED) {b['bucket_key']}: n={b['n_cases']} "
                  f"empirical={b['empirical_effect']:+.4f} analytic={b['analytic_effect']:+.4f} "
                  f"gap={b['gap']:.4f} -- {b['direction_not_scored_reason']}")
        else:
            print(f"    {b['bucket_key']}: n={b['n_cases']} empirical={b['empirical_effect']:+.4f} "
                  f"analytic={b['analytic_effect']:+.4f} gap={b['gap']:.4f} "
                  f"direction_match={b['direction_match']}")
    print(f"  Descriptive rollup by action_type (NOT used for pass/fail -- see per-bucket above for that):")
    for action, detail in r["per_action"].items():
        if detail.get("skipped"):
            print(f"    (skipped: {action} -- {detail['reason']}, "
                  f"{detail['n_sub_buckets_skipped']} sub-buckets all had insufficient data)")
        else:
            print(f"    {action}: n={detail['n_cases']} "
                  f"({detail['n_sub_buckets_evaluated']} sub-buckets evaluated, "
                  f"{detail['n_sub_buckets_skipped_insufficient_data']} skipped for insufficient data) "
                  f"empirical={detail['empirical_effect']:+.4f} analytic={detail['analytic_effect']:+.4f} "
                  f"gap={detail['gap']:.4f} direction_match={detail['direction_match']}")

    section("STEP 6: Validator robustness self-test (deliberate corruption)")
    r = val.validator_robustness_self_test(baseline_truth)
    check("hidden_state_once_per_case_check passes on clean data AND correctly fails on deliberately corrupted data",
          r["passed"], {"clean_passed": r["clean_result"]["passed"],
                         "corrupted_correctly_flagged": not r["corrupted_result"]["passed"],
                         "corrupted_violating_cases": r["corrupted_result"]["violating_case_count"]})

    section("STEP 7: Hidden-state-once-per-case (real check, on real data)")
    r = val.hidden_state_once_per_case_check(baseline_truth)
    check("Hidden state identical across every candidate within each case (baseline)", r["passed"], r)
    r2 = val.hidden_state_once_per_case_check(stress_truth)
    check("Hidden state identical across every candidate within each case (stress)", r2["passed"], r2)

    section("STEP 8: Distributional sanity + directional relationship checks")
    r = val.distributional_sanity_check(baseline_joint, thresholds)
    check("Amounts/health scores/methods within valid ranges (baseline)", r["passed"], r)
    r = val.directional_relationship_check(baseline_joint, thresholds)
    check("Lower network health measurably correlates with lower recovery for technical retries",
          r["passed"], r)
    r = val.candidate_timing_validity_check(baseline_joint, thresholds)
    check("Every candidate's (timing, timing_hours) pair is a valid locked bucket", r["passed"], r)

    section("STEP 8b: Fatigue significance (named statistic, predeclared sign + alpha + min-N)")
    r = val.fatigue_significance_check(baseline_joint, thresholds)
    check("Prior-contact count is significantly, negatively correlated with recovery "
          "(predeclared sign + alpha, not a descriptive claim)", r["passed"], r)

    section("STEP 8c: Per-validator corruption self-tests (one per validator, not a general claim)")
    r = val.leakage_case_level_robustness_self_test(baseline_joint, thresholds)
    check("Case-level leakage check correctly flags a deliberately duplicated case_id across splits",
          r["passed"], {"corrupted_overlap": r["corrupted_result"]["overlap_count"]})
    r = val.leakage_customer_level_robustness_self_test(baseline_joint, thresholds)
    check("Customer-level leakage check correctly flags a deliberately duplicated customer_id across splits",
          r["passed"], {"corrupted_overlap": r["corrupted_result"]["overlap_count"]})
    r = val.leakage_temporal_order_robustness_self_test(baseline_joint, thresholds)
    check("Temporal-order check correctly flags an injected negative hours_since_last_action",
          r["passed"], {"corrupted_violations": r["corrupted_result"].get("violations")})
    r = val.distributional_sanity_robustness_self_test(baseline_joint, thresholds)
    check("Distributional sanity check correctly flags an injected out-of-range amount",
          r["passed"])
    r = val.candidate_timing_validity_robustness_self_test(baseline_joint, thresholds)
    check("Timing validity check correctly flags an injected out-of-range timing bucket ('9d')",
          r["passed"])
    r = val.ground_truth_check_robustness_self_test(baseline_joint, baseline_truth, thresholds)
    check("Ground-truth check is measurably worse on data with recovered_amount randomly permuted "
          "than on clean data", r["passed"],
          {"clean_fraction_matching": r["clean_fraction_matching"],
           "corrupted_fraction_matching": r["corrupted_fraction_matching"]})

    section("STEP 8d: Eval-set lock -- generate, hash, and commit the temporal holdout before Phase 3")
    manifest_entry = evl.lock_eval_set(baseline_joint, "baseline", SEED, cod.GENERATOR_VERSION, thresholds)
    check("Baseline temporal holdout generated and hashed", manifest_entry is not None,
          {"holdout_case_count": manifest_entry["holdout_case_count"],
           "sha256": manifest_entry["sha256"][:16] + "..."})
    verify_result = evl.verify_eval_set("baseline", thresholds)
    check("Eval-set integrity check: recomputed hash matches the committed manifest", verify_result["passed"],
          verify_result)

    section("STEP 9: Calibration profile divergence -- baseline vs stress must differ materially")
    r = val.calibration_profile_divergence_check(baseline_health_df, stress_health_df, baseline_joint, stress_joint, thresholds)
    check("Baseline and stress profiles differ materially (not just by seed)", r["passed"], r)

    section("STEP 10: Export CSVs + register both runs in the dataset registry")
    baseline_joint_path = OUTPUT_DIR / f"joint_baseline_seed{SEED}.csv"
    baseline_truth_path = OUTPUT_DIR / f"truth_baseline_seed{SEED}.csv"
    stress_joint_path = OUTPUT_DIR / f"joint_stress_seed{SEED}.csv"
    stress_truth_path = OUTPUT_DIR / f"truth_stress_seed{SEED}.csv"
    baseline_joint.to_csv(baseline_joint_path, index=False)
    baseline_truth.to_csv(baseline_truth_path, index=False)
    stress_joint.to_csv(stress_joint_path, index=False)
    stress_truth.to_csv(stress_truth_path, index=False)
    check("Baseline CSV written", baseline_joint_path.exists())
    check("Stress CSV written", stress_joint_path.exists())

    db_path = _BACKEND_DIR / "db" / "recovery.db"
    db_arg = str(db_path) if db_path.exists() else None

    baseline_validator_results = {
        "leakage_case_level": val.leakage_case_level(baseline_joint, thresholds),
        "leakage_customer_level": val.leakage_customer_level(baseline_joint, thresholds),
        "leakage_temporal_order": val.leakage_temporal_order(baseline_joint, thresholds),
        "ground_truth_treatment_effect": val.ground_truth_treatment_effect_check(baseline_joint, baseline_truth, thresholds),
        "hidden_state_once_per_case": val.hidden_state_once_per_case_check(baseline_truth),
        "distributional_sanity": val.distributional_sanity_check(baseline_joint, thresholds),
        "directional_relationship": val.directional_relationship_check(baseline_joint, thresholds),
        "candidate_timing_validity": val.candidate_timing_validity_check(baseline_joint, thresholds),
        "fatigue_significance": val.fatigue_significance_check(baseline_joint, thresholds),
        "eval_set_integrity": evl.verify_eval_set("baseline", thresholds),
    }
    manifest_baseline = registry.register_run(
        DATASET_NAME, DATASET_VERSION, SEED, "baseline", cod.GENERATOR_VERSION,
        row_count=len(baseline_joint), case_count=int(baseline_joint["case_id"].nunique()),
        validator_results=baseline_validator_results, db_path=db_arg,
    )
    check("Baseline run registered in dataset registry", manifest_baseline is not None,
          manifest_baseline.get("_manifest_path"))

    stress_validator_results = {
        "leakage_case_level": val.leakage_case_level(stress_joint, thresholds),
        "hidden_state_once_per_case": val.hidden_state_once_per_case_check(stress_truth),
        "distributional_sanity": val.distributional_sanity_check(stress_joint, thresholds),
    }
    manifest_stress = registry.register_run(
        DATASET_NAME, DATASET_VERSION, SEED, "stress", cod.GENERATOR_VERSION,
        row_count=len(stress_joint), case_count=int(stress_joint["case_id"].nunique()),
        validator_results=stress_validator_results, db_path=db_arg,
    )
    check("Stress run registered in dataset registry", manifest_stress is not None,
          manifest_stress.get("_manifest_path"))

    if db_arg:
        import sqlite3
        conn = sqlite3.connect(db_arg)
        n = conn.execute("SELECT COUNT(*) FROM dataset_registry").fetchone()[0]
        conn.close()
        check(f"dataset_registry table in {db_arg} has >= 2 rows after this run", n >= 2, f"n={n}")
    else:
        print("(no recovery.db found -- dataset_registry table write skipped, JSON manifest still written)")

    section("SUMMARY")
    passed = sum(1 for _, ok in results if ok)
    failed = [label for label, ok in results if not ok]
    print(f"{passed}/{len(results)} checks passed.")
    if failed:
        print("FAILED CHECKS:")
        for label in failed:
            print(f"  - {label}")
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())