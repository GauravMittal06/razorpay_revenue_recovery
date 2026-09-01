"""
Data Factory validators. Phase 2.

Every check here returns a dict: {"passed": bool, ...details...}. Nothing
in this module prints a narrative claim without a corresponding computed
result attached -- run_generation.py is responsible for turning these
into the PHASE2_NOTES.md "verified, on a completely fresh rebuild"
section, but the numbers themselves are produced here, not asserted there.

Thresholds are read from locked_thresholds.json, committed before any of
these checks were first run against real generated output (see that
file's _lock_notice and PHASE2_NOTES.md's "Locked-before-use" section).
"""

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from sklearn.model_selection import GroupShuffleSplit

THIS_DIR = Path(__file__).resolve().parent
THRESHOLDS_PATH = THIS_DIR / "locked_thresholds.json"


def load_thresholds():
    with open(THRESHOLDS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Leakage checks -- case-level, customer-level, temporal-order. Each is a
# DISTINCT failure mode (per execution plan Section 8) and checked
# independently, not folded into one combined pass/fail.
# ---------------------------------------------------------------------

def _group_overlap_result(train_groups: set, test_groups: set, max_overlap: int, group_label: str):
    """Shared by the real leakage checks and their corruption self-tests
    below -- the SAME overlap-counting arithmetic is what must correctly
    flag a corrupted split, not a separate, untested code path."""
    overlap = train_groups & test_groups
    passed = len(overlap) <= max_overlap
    return {"passed": passed, "overlap_count": len(overlap),
            f"train_{group_label}": len(train_groups), f"test_{group_label}": len(test_groups)}


def leakage_case_level(joint_df, thresholds, random_state=42):
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=random_state)
    train_idx, test_idx = next(splitter.split(joint_df, groups=joint_df["case_id"]))
    train_cases = set(joint_df.iloc[train_idx]["case_id"])
    test_cases = set(joint_df.iloc[test_idx]["case_id"])
    return _group_overlap_result(train_cases, test_cases,
                                  thresholds["leakage"]["case_level_max_overlap"], "cases")


def leakage_customer_level(joint_df, thresholds, random_state=42):
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=random_state)
    train_idx, test_idx = next(splitter.split(joint_df, groups=joint_df["customer_id"]))
    train_customers = set(joint_df.iloc[train_idx]["customer_id"])
    test_customers = set(joint_df.iloc[test_idx]["customer_id"])
    return _group_overlap_result(train_customers, test_customers,
                                  thresholds["leakage"]["customer_level_max_overlap"], "customers")


def leakage_temporal_order(joint_df, thresholds):
    """Proxy check on the exported dataset: hours_since_last_action, when
    present, must never be negative (a negative value would mean a
    'prior' contact was actually computed from a time after sim_hour --
    i.e. information from the future leaking into a decision-time
    feature). Combined with the structural guarantee already enforced at
    generation time inside entities.Customer.contacts_in_window() (strict
    `t < as_of_hour`) and bank_health_timeseries.HealthIndex.lookup()
    (`obs.window_start <= sim_hour`), this is checking the guarantee
    actually held on the exported data, not re-deriving it from scratch."""
    col = joint_df["hours_since_last_action"].dropna()
    violations = int((col < 0).sum())
    passed = violations <= thresholds["leakage"]["temporal_order_violations_allowed"]
    return {"passed": passed, "violations": violations, "checked_rows": int(col.shape[0])}


# ---------------------------------------------------------------------
# Reproducibility -- identical seed+profile+generator_version -> identical
# output, proven with an actual two-run diff.
# ---------------------------------------------------------------------

def reproducibility_check(generate_fn, profile, seed, thresholds, **gen_kwargs):
    df1, truth1, *_ = generate_fn(profile, seed, **gen_kwargs)
    df2, truth2, *_ = generate_fn(profile, seed, **gen_kwargs)

    joint_equal = df1.equals(df2)
    truth_equal = truth1.equals(truth2)

    diff_cells = 0
    if not joint_equal:
        try:
            diff_mask = (df1 != df2) & ~(df1.isna() & df2.isna())
            diff_cells = int(diff_mask.sum().sum())
        except Exception:
            diff_cells = -1  # shape mismatch or similar; still a failure

    passed = joint_equal and truth_equal
    return {"passed": passed, "joint_dataset_identical": bool(joint_equal),
            "truth_dataset_identical": bool(truth_equal), "diff_cells": diff_cells}


# ---------------------------------------------------------------------
# Ground-truth treatment-effect check -- the dataset's empirical effect
# must match the generator's own analytic effect function, by construction.
# ---------------------------------------------------------------------

# Provably-minimal root-cause equivalence classes for the ground-truth check's
# bucketing, derived directly from outcome_model.py's action_effectiveness()
# and timing_term() -- the ONLY two functions in the outcome model that
# branch on root_cause at all (decay_term, amount_friction, fatigue_term,
# network_health_term, retry_count_penalty do not). Two root causes are only
# merged into one class here if BOTH functions treat them identically for
# that action_type -- verified by reading both functions' branches, not
# assumed. gateway_timeout/network_error merge everywhere (both functions
# always group them together); authentication_failed/expired_card merge
# everywhere; insufficient_funds and payment_declined never merge with
# anything, since at least one of the two functions treats them distinctly
# from every other root cause in every action branch.
_TRANSIENT_CAUSES = {"gateway_timeout", "network_error"}
_NEEDS_ACTION_CAUSES = {"authentication_failed", "expired_card"}


def _root_cause_effect_class(action_type, event_type, root_cause):
    if action_type == "do_nothing" or event_type != "payment_failed":
        return "n/a"
    if action_type == "escalate":
        # action_effectiveness() returns a flat 0.35 for every payment_failed
        # root cause, and timing_term() returns 0.0 for escalate regardless
        # of root_cause -- genuinely no root-cause distinction to bucket by.
        return "n/a"
    if root_cause in _TRANSIENT_CAUSES:
        return "transient"
    if root_cause == "insufficient_funds":
        return "insufficient_funds"
    if root_cause in _NEEDS_ACTION_CAUSES:
        return "needs_action"
    return "payment_declined"  # the one remaining root_cause value


def ground_truth_treatment_effect_check(joint_df, truth_df, thresholds):
    """
    Buckets by (action_type, event_type, root_cause_effect_class,
    method_changed) -- a provably-minimal partition derived from
    outcome_model.py's action_effectiveness() and timing_term() (see
    _root_cause_effect_class above and locked_thresholds.json's
    ground_truth_treatment_effect._amendment_reason for the full,
    evidence-based justification). NOT action_type alone, and NOT raw
    root_cause either -- both were tried and shown to either pool
    opposite-signed effects together or spuriously split effects the
    generator does not actually distinguish.

    Two independent "don't pretend it's fine" gates, both explicit in the
    output rather than silently applied:

    1. Sample size: a bucket below the locked min_cases_per_bucket_for_check
       is marked skipped=True with a reason and excluded entirely from
       both the gap and direction scoring.

    2. Effect size: even with enough cases, a bucket whose |analytic_effect|
       is smaller than the already-locked max_absolute_probability_gap
       cannot have its SIGN meaningfully tested -- the gap tolerance
       already committed to permits an empirical value on either side of
       zero for a true effect that small, by construction of the two
       tests together (not a new free parameter: derived mechanically
       from the existing locked gap tolerance, not chosen to fit any
       specific result). Such buckets are still gap-tested (the magnitude
       claim IS meaningful even when the sign isn't) but marked
       direction_scored=False and excluded from
       fraction_buckets_matching_direction's denominator, with that
       exclusion counted and reported explicitly, not silently dropped.
    """
    cfg = thresholds["ground_truth_treatment_effect"]
    candidate_key = ["case_id", "candidate_action", "candidate_timing", "candidate_method", "candidate_channel"]
    merged = joint_df[candidate_key + ["event_type", "root_cause", "candidate_method_changed",
                                        "amount", "recovered_amount"]].merge(
        truth_df[candidate_key + ["analytic_p"]],
        on=candidate_key, how="inner"
    )
    # This must be a strict 1:1 join -- each joint row is exactly one candidate for exactly
    # one case, and truth_df has exactly one row per (case, candidate). Joining on case_id +
    # candidate_action ALONE (the original bug) silently many-to-many joins every case that has
    # more than one candidate sharing an action_type (e.g. multiple retry timing/method
    # combinations for the same case) -- which is every retry-eligible case. Asserting the row
    # count here turns that class of bug into an immediate, loud failure instead of a silently
    # wrong analytic_effect number.
    assert len(merged) == len(joint_df), (
        f"ground_truth_treatment_effect_check: merge produced {len(merged)} rows from "
        f"{len(joint_df)} input rows -- candidate_key {candidate_key} does not uniquely "
        f"identify rows in joint_df/truth_df. This must be a 1:1 join."
    )
    merged["recovered_fraction"] = merged["recovered_amount"] / merged["amount"].replace(0, np.nan)
    merged["recovered_fraction"] = merged["recovered_fraction"].fillna(0.0)
    merged["root_cause_label"] = merged["root_cause"].fillna("n/a")
    merged["method_changed"] = merged["candidate_method_changed"].fillna(False)
    merged["effect_class"] = merged.apply(
        lambda row: _root_cause_effect_class(row["candidate_action"], row["event_type"], row["root_cause"]),
        axis=1
    )

    baseline = merged[merged["candidate_action"] == "do_nothing"].groupby("case_id").agg(
        baseline_frac=("recovered_fraction", "mean"),
        baseline_p=("analytic_p", "mean"),
    )

    non_baseline = merged[merged["candidate_action"] != "do_nothing"]
    per_bucket = []
    matching_direction = 0
    total_evaluated = 0
    total_direction_scored = 0
    total_skipped = 0

    group_cols = ["candidate_action", "event_type", "effect_class", "method_changed"]
    for (action, event_type, effect_class, method_changed), sub in non_baseline.groupby(group_cols):
        by_case = sub.groupby("case_id").agg(frac=("recovered_fraction", "mean"), p=("analytic_p", "mean"))
        joined = by_case.join(baseline, how="inner")
        n = len(joined)
        raw_root_causes = sorted(sub["root_cause_label"].unique().tolist())
        bucket_key = f"{action}|{event_type}|{effect_class}|" \
                     f"{'method_changed' if method_changed else 'same_method'}"

        if n < cfg["min_cases_per_bucket_for_check"]:
            total_skipped += 1
            per_bucket.append({
                "bucket_key": bucket_key, "action_type": action, "event_type": event_type,
                "effect_class": effect_class, "raw_root_causes": raw_root_causes,
                "method_changed": bool(method_changed),
                "n_cases": n, "skipped": True,
                "reason": f"n_cases ({n}) below locked min_cases_per_bucket_for_check "
                          f"({cfg['min_cases_per_bucket_for_check']}) -- NOT ENOUGH DATA, "
                          f"not evaluated, not counted toward pass/fail.",
            })
            continue

        empirical_effect = float((joined["frac"] - joined["baseline_frac"]).mean())
        analytic_effect = float((joined["p"] - joined["baseline_p"]).mean())
        gap = abs(empirical_effect - analytic_effect)
        gap_within_tolerance = gap <= cfg["max_absolute_probability_gap"]

        # Effect-size gate: a true effect smaller than the gap tolerance we've
        # already committed to cannot have its sign meaningfully tested -- see
        # docstring point 2. Derived from the existing locked
        # max_absolute_probability_gap, not a new free parameter.
        direction_scored = abs(analytic_effect) >= cfg["max_absolute_probability_gap"]
        direction_match = (empirical_effect >= 0) == (analytic_effect >= 0)

        total_evaluated += 1
        if direction_scored:
            total_direction_scored += 1
            if direction_match:
                matching_direction += 1

        per_bucket.append({
            "bucket_key": bucket_key, "action_type": action, "event_type": event_type,
            "effect_class": effect_class, "raw_root_causes": raw_root_causes,
            "method_changed": bool(method_changed),
            "n_cases": n, "skipped": False,
            "empirical_effect": empirical_effect, "analytic_effect": analytic_effect, "gap": gap,
            "gap_within_tolerance": gap_within_tolerance,
            "direction_scored": direction_scored,
            "direction_match": direction_match if direction_scored else None,
            "direction_not_scored_reason": None if direction_scored else
                f"|analytic_effect|={abs(analytic_effect):.4f} is smaller than the locked gap "
                f"tolerance ({cfg['max_absolute_probability_gap']}) -- EFFECT TOO SMALL TO TEST "
                f"DIRECTION MEANINGFULLY, gap-tested only, not counted toward "
                f"fraction_buckets_matching_direction.",
        })

    frac_matching = (matching_direction / total_direction_scored) if total_direction_scored else 0.0
    all_gaps_ok = all(b["gap_within_tolerance"] for b in per_bucket if not b["skipped"])
    passed = bool(
        total_direction_scored > 0
        and all_gaps_ok
        and frac_matching >= cfg["min_fraction_of_buckets_matching_direction"]
    )

    # Descriptive-only rollup by action_type, for backward-compatible summaries.
    # Weighted by n_cases across that action's EVALUATED sub-buckets only --
    # never used to decide passed/failed, and explicitly labeled as such.
    per_action = {}
    for action in sorted(set(b["action_type"] for b in per_bucket)):
        sub_buckets = [b for b in per_bucket if b["action_type"] == action and not b["skipped"]]
        skipped_sub_buckets = [b for b in per_bucket if b["action_type"] == action and b["skipped"]]
        if not sub_buckets:
            per_action[action] = {
                "skipped": True, "descriptive_only": True,
                "reason": "every sub-bucket for this action_type had insufficient data",
                "n_sub_buckets_skipped": len(skipped_sub_buckets),
            }
            continue
        total_n = sum(b["n_cases"] for b in sub_buckets)
        weighted_empirical = sum(b["empirical_effect"] * b["n_cases"] for b in sub_buckets) / total_n
        weighted_analytic = sum(b["analytic_effect"] * b["n_cases"] for b in sub_buckets) / total_n
        per_action[action] = {
            "descriptive_only": True,
            "n_cases": total_n,
            "n_sub_buckets_evaluated": len(sub_buckets),
            "n_sub_buckets_skipped_insufficient_data": len(skipped_sub_buckets),
            "empirical_effect": weighted_empirical,
            "analytic_effect": weighted_analytic,
            "gap": abs(weighted_empirical - weighted_analytic),
            "direction_match": (weighted_empirical >= 0) == (weighted_analytic >= 0),
        }

    return {
        "passed": passed,
        "fraction_buckets_matching_direction": frac_matching,
        "all_gaps_within_tolerance": all_gaps_ok,
        "buckets_evaluated": total_evaluated,
        "buckets_skipped_insufficient_data": total_skipped,
        "buckets_direction_scored": total_direction_scored,
        "buckets_effect_too_small_for_direction_test": total_evaluated - total_direction_scored,
        "per_bucket": per_bucket,
        "per_action": per_action,
    }


# ---------------------------------------------------------------------
# Validator robustness -- deliberately corrupt a dataset and confirm the
# hidden-state-once-per-case validator actually catches it.
# ---------------------------------------------------------------------

def hidden_state_once_per_case_check(truth_df):
    """The one check that can only run against truth_df (hidden state is
    deliberately never written to the training-facing joint dataset --
    preserved from legacy's discipline). For every case_id, every
    candidate row's hidden_* columns must be identical -- this is the
    single property the execution plan calls the one that makes
    cross-candidate comparison causally meaningful at all."""
    hidden_cols = [c for c in truth_df.columns if c.startswith("hidden_")]
    n_unique_per_case = truth_df.groupby("case_id")[hidden_cols].nunique()
    violating_cases = n_unique_per_case[(n_unique_per_case > 1).any(axis=1)]
    passed = len(violating_cases) == 0
    return {"passed": passed, "violating_case_count": int(len(violating_cases)),
            "hidden_columns_checked": hidden_cols}


def validator_robustness_self_test(truth_df, rng_seed=999):
    """Deliberately corrupts a COPY of truth_df by resampling hidden_*
    values independently per row (breaking the once-per-case rule on
    purpose), then confirms hidden_state_once_per_case_check correctly
    fails on the corrupted copy while still passing on the original.
    This is the check required by the task instructions: 'deliberately
    corrupt a dataset ... and confirm your own validator actually
    catches it -- don't just claim the validator works.'"""
    rng = np.random.default_rng(rng_seed)
    hidden_cols = [c for c in truth_df.columns if c.startswith("hidden_")]

    corrupted = truth_df.copy()
    for col in hidden_cols:
        corrupted[col] = rng.uniform(0, 1, size=len(corrupted))

    result_on_clean = hidden_state_once_per_case_check(truth_df)
    result_on_corrupted = hidden_state_once_per_case_check(corrupted)

    # The self-test itself passes only if: clean data passes AND
    # corrupted data is correctly flagged as failing.
    self_test_passed = result_on_clean["passed"] and (not result_on_corrupted["passed"])
    return {
        "passed": self_test_passed,
        "clean_result": result_on_clean,
        "corrupted_result": result_on_corrupted,
    }


# ---------------------------------------------------------------------
# Distributional sanity + directional relationship checks.
# ---------------------------------------------------------------------

def distributional_sanity_check(joint_df, thresholds):
    cfg = thresholds["distributional_sanity"]
    amount_ok = joint_df["amount"].between(cfg["amount_min"], cfg["amount_max"]).all()
    health = joint_df["network_health_score"].dropna()
    health_ok = health.between(cfg["network_health_score_min"], cfg["network_health_score_max"]).all()
    methods_ok = set(joint_df["candidate_method"].unique()) <= (set(["n/a"]) | set(
        ["card", "netbanking", "upi", "wallet"]))
    passed = bool(amount_ok and health_ok and methods_ok)
    return {"passed": passed, "amount_in_range": bool(amount_ok),
            "health_in_range": bool(health_ok), "methods_valid": bool(methods_ok)}


def directional_relationship_check(joint_df, thresholds):
    """Lower simulated bank health should measurably correlate with lower
    recovery for retry candidates on payment_failed technical-failure
    root causes -- checked directly, not assumed."""
    cfg = thresholds["distributional_sanity"]
    sub = joint_df[
        (joint_df["candidate_action"] == "retry")
        & (joint_df["root_cause"].isin(["gateway_timeout", "network_error"]))
        & (joint_df["network_health_score"].notna())
    ]
    if len(sub) < 30:
        return {"passed": False, "reason": "insufficient rows", "n": len(sub)}
    corr = float(np.corrcoef(sub["network_health_score"], sub["recovered"])[0, 1])
    passed = corr >= cfg["min_directional_correlation_health_vs_technical_recovery"]
    return {"passed": passed, "correlation": corr, "n": len(sub)}


def calibration_profile_divergence_check(baseline_health_obs_df, stress_health_obs_df,
                                          baseline_joint_df, stress_joint_df, thresholds):
    cfg = thresholds["calibration_profile_divergence"]
    b_health = baseline_health_obs_df["health_score"].mean()
    s_health = stress_health_obs_df["health_score"].mean()
    rel_diff_health = abs(b_health - s_health) / b_health if b_health else 0.0

    b_rate = baseline_joint_df["recovered"].mean()
    s_rate = stress_joint_df["recovered"].mean()
    rel_diff_rate = abs(b_rate - s_rate) / b_rate if b_rate else 0.0

    passed = bool(rel_diff_health >= cfg["min_relative_difference_bank_health"]
                  and rel_diff_rate >= cfg["min_relative_difference_recovery_rate"])
    return {"passed": passed, "baseline_mean_health": float(b_health),
            "stress_mean_health": float(s_health), "rel_diff_health": float(rel_diff_health),
            "baseline_recovery_rate": float(b_rate), "stress_recovery_rate": float(s_rate),
            "rel_diff_recovery_rate": float(rel_diff_rate)}


# ---------------------------------------------------------------------
# Fatigue significance check -- [TIGHTENED] gate: a named statistic
# (Pearson correlation), a predeclared expected sign, a predeclared
# significance threshold, and a predeclared minimum N -- not a
# descriptive bucket-mean table.
# ---------------------------------------------------------------------

def fatigue_significance_check(joint_df, thresholds):
    cfg = thresholds["fatigue_significance"]
    sub = joint_df[joint_df["candidate_action"] != "do_nothing"]
    n = len(sub)
    if n < cfg["min_n"]:
        return {"passed": False, "reason": "below predeclared min_n", "n": n, "min_n": cfg["min_n"]}

    r, p_value = scipy_stats.pearsonr(sub["prior_contacts_in_window"], sub["recovered"])
    sign_ok = (r < 0) if cfg["expected_sign"] == "negative" else (r > 0)
    significant = p_value < cfg["alpha"]
    passed = bool(sign_ok and significant)
    return {"passed": passed, "correlation": float(r), "p_value": float(p_value),
            "n": n, "expected_sign": cfg["expected_sign"], "alpha": cfg["alpha"],
            "sign_matches_expected": bool(sign_ok), "statistically_significant": bool(significant)}


# ---------------------------------------------------------------------
# Candidate timing validity -- structural check that every row's
# (candidate_timing, candidate_timing_hours) pair is one of the locked
# allowed buckets, catching e.g. an out-of-range timing bucket.
# ---------------------------------------------------------------------

def candidate_timing_validity_check(joint_df, thresholds):
    cfg = thresholds["candidate_timing_validity"]
    allowed_timings = set(cfg["allowed_timings"])
    allowed_hours_map = cfg["allowed_timing_hours"]

    bad_timing_mask = ~joint_df["candidate_timing"].isin(allowed_timings)
    bad_hours_mask = ~joint_df.apply(
        lambda row: allowed_hours_map.get(row["candidate_timing"]) == row["candidate_timing_hours"],
        axis=1
    )
    violations = int((bad_timing_mask | bad_hours_mask).sum())
    passed = violations == 0
    return {"passed": passed, "violations": violations, "checked_rows": int(len(joint_df))}


# ---------------------------------------------------------------------
# Per-validator corruption self-tests -- closes the Phase Acceptance
# Test Gates requirement: "at least one corruption test per validator,
# not a general claim." Each function deliberately corrupts a COPY of
# real data in exactly the way that validator exists to catch, and
# confirms: (a) the clean data still passes, (b) the corrupted data is
# correctly flagged as failing. The self-test itself only passes if
# both (a) and (b) hold.
# ---------------------------------------------------------------------

def leakage_case_level_robustness_self_test(joint_df, thresholds, rng_seed=101):
    """Corruption: force the same case_id to appear in both partitions
    (a duplicated case ID across the train/test split) and confirm the
    shared overlap-counting logic catches it."""
    rng = np.random.default_rng(rng_seed)
    case_ids = joint_df["case_id"].unique()
    split_point = int(len(case_ids) * 0.8)
    shuffled = rng.permutation(case_ids)
    train_cases = set(shuffled[:split_point])
    test_cases = set(shuffled[split_point:])
    clean_result = _group_overlap_result(train_cases, test_cases,
                                          thresholds["leakage"]["case_level_max_overlap"], "cases")

    corrupted_test_cases = set(test_cases) | {shuffled[0]}  # inject one case from train into test
    corrupted_result = _group_overlap_result(train_cases, corrupted_test_cases,
                                              thresholds["leakage"]["case_level_max_overlap"], "cases")

    self_test_passed = clean_result["passed"] and (not corrupted_result["passed"])
    return {"passed": self_test_passed, "clean_result": clean_result, "corrupted_result": corrupted_result}


def leakage_customer_level_robustness_self_test(joint_df, thresholds, rng_seed=102):
    """Same pattern as above, for customer_id."""
    rng = np.random.default_rng(rng_seed)
    customer_ids = joint_df["customer_id"].unique()
    split_point = int(len(customer_ids) * 0.8)
    shuffled = rng.permutation(customer_ids)
    train_customers = set(shuffled[:split_point])
    test_customers = set(shuffled[split_point:])
    clean_result = _group_overlap_result(train_customers, test_customers,
                                          thresholds["leakage"]["customer_level_max_overlap"], "customers")

    corrupted_test_customers = set(test_customers) | {shuffled[0]}
    corrupted_result = _group_overlap_result(train_customers, corrupted_test_customers,
                                              thresholds["leakage"]["customer_level_max_overlap"], "customers")

    self_test_passed = clean_result["passed"] and (not corrupted_result["passed"])
    return {"passed": self_test_passed, "clean_result": clean_result, "corrupted_result": corrupted_result}


def leakage_temporal_order_robustness_self_test(joint_df, thresholds):
    """Corruption: inject a negative hours_since_last_action (a future
    contact used to compute a decision-time feature) and confirm the
    check flags it."""
    clean_result = leakage_temporal_order(joint_df, thresholds)

    corrupted = joint_df.copy()
    non_null_idx = corrupted[corrupted["hours_since_last_action"].notna()].index
    if len(non_null_idx) == 0:
        return {"passed": False, "reason": "no non-null hours_since_last_action rows to corrupt"}
    corrupted.loc[non_null_idx[0], "hours_since_last_action"] = -5.0
    corrupted_result = leakage_temporal_order(corrupted, thresholds)

    self_test_passed = clean_result["passed"] and (not corrupted_result["passed"])
    return {"passed": self_test_passed, "clean_result": clean_result, "corrupted_result": corrupted_result}


def distributional_sanity_robustness_self_test(joint_df, thresholds):
    """Corruption: inject an out-of-range amount and confirm the check
    flags it."""
    clean_result = distributional_sanity_check(joint_df, thresholds)

    corrupted = joint_df.copy()
    corrupted.loc[corrupted.index[0], "amount"] = thresholds["distributional_sanity"]["amount_max"] * 100
    corrupted_result = distributional_sanity_check(corrupted, thresholds)

    self_test_passed = clean_result["passed"] and (not corrupted_result["passed"])
    return {"passed": self_test_passed, "clean_result": clean_result, "corrupted_result": corrupted_result}


def candidate_timing_validity_robustness_self_test(joint_df, thresholds):
    """Corruption: inject an out-of-range timing bucket (the exact
    example the gate document names) and confirm the check flags it."""
    clean_result = candidate_timing_validity_check(joint_df, thresholds)

    corrupted = joint_df.copy()
    corrupted.loc[corrupted.index[0], "candidate_timing"] = "9d"
    corrupted.loc[corrupted.index[0], "candidate_timing_hours"] = 216.0
    corrupted_result = candidate_timing_validity_check(corrupted, thresholds)

    self_test_passed = clean_result["passed"] and (not corrupted_result["passed"])
    return {"passed": self_test_passed, "clean_result": clean_result, "corrupted_result": corrupted_result}


def ground_truth_check_robustness_self_test(joint_df, truth_df, thresholds, rng_seed=103):
    """Corruption: randomly permute recovered_amount across rows,
    destroying its relationship to the generator's own analytic_p, and
    confirm the ground-truth check's fraction-matching-direction drops
    (fewer buckets agree in sign once the relationship is destroyed) or
    at least one bucket's gap blows past tolerance. This does not assert
    the CLEAN result passes (that gate is separately, honestly reported
    as unstable in PHASE2_NOTES.md) -- it only asserts the corrupted
    result is measurably worse than the clean one, which is the actual
    property this self-test can honestly claim."""
    rng = np.random.default_rng(rng_seed)
    clean_result = ground_truth_treatment_effect_check(joint_df, truth_df, thresholds)

    corrupted = joint_df.copy()
    corrupted["recovered_amount"] = rng.permutation(corrupted["recovered_amount"].values)
    corrupted_result = ground_truth_treatment_effect_check(corrupted, truth_df, thresholds)

    clean_frac = clean_result["fraction_buckets_matching_direction"]
    corrupted_frac = corrupted_result["fraction_buckets_matching_direction"]
    self_test_passed = corrupted_frac <= clean_frac and not corrupted_result["passed"]
    return {"passed": bool(self_test_passed), "clean_result": clean_result, "corrupted_result": corrupted_result,
            "clean_fraction_matching": clean_frac, "corrupted_fraction_matching": corrupted_frac}


# ---------------------------------------------------------------------
# Static authority-boundary check -- no Data Factory module may import or
# call anything with execution authority.
# ---------------------------------------------------------------------

FORBIDDEN_MODULES = {
    "backend.engine.execute_action", "engine.execute_action",
    "backend.engine.decide_action", "engine.decide_action",
    "backend.api.actions", "api.actions",
}
FORBIDDEN_NAMES = {"execute_action", "decide_action", "mark_opportunity_recovered"}


def static_no_execution_authority_check():
    violations = []
    for path in sorted(THIS_DIR.glob("*.py")):
        if path.name == "validators.py":
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as e:
            violations.append({"file": str(path), "error": f"SyntaxError: {e}"})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_MODULES:
                        violations.append({"file": str(path), "import": alias.name})
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in FORBIDDEN_MODULES:
                    violations.append({"file": str(path), "import_from": mod})
                for alias in node.names:
                    if alias.name in FORBIDDEN_NAMES:
                        violations.append({"file": str(path), "imported_name": alias.name})
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                violations.append({"file": str(path), "referenced_name": node.id})
    passed = len(violations) == 0
    return {"passed": passed, "violations": violations,
            "files_scanned": [str(p) for p in sorted(THIS_DIR.glob("*.py")) if p.name != "validators.py"]}