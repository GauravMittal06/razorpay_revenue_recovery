"""
Phase 3 -- evaluation harness. Runs every gate in locked_thresholds.json's
phase3_* blocks against the trained outcome_model.joblib artifact, on the
frozen, hash-verified Phase 3 eval artifacts. Reports raw pass/fail per gate
with the actual computed numbers -- never a bare "passed".

Gates (see backend/PHASE3_NOTES.md for the reasoning behind each threshold):
  1. phase3_calibration      -- calibration_holdout (UNSEEN)
  2. phase3_treatment_effect -- training_pool UNION calibration_holdout
                                 (per the dated evaluation_population amendment)
  3. phase3_cross_profile    -- stress dataset (UNSEEN, different profile)
  4. phase3_temporal         -- temporal_holdout (UNSEEN, later time window)
  5. phase3_multiseed        -- retrain fresh on seeds 43/44's own carved
                                 training pools, re-run gates 1/2/4 on each
                                 (cross_profile is NOT repeated per seed --
                                 no per-seed stress dataset was commissioned;
                                 documented scope decision, not an oversight)
  6. phase3_parity           -- batch harness path vs ml.inference.py path,
                                 same representative cases
  7. failure behavior        -- malformed cases -> flagged null, never a
                                 crash or a plausible-looking silent score

Run from the directory containing backend/:
    python -m backend.ml.evaluate_outcome_model
"""

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import roc_auc_score

from backend.data_factory import eval_set_lock as evl
from backend.data_factory.validators import _root_cause_effect_class, load_thresholds
from backend.db import db as db_module
from backend.ml import bank_health_setup
from backend.ml import outcome_features as feats
from backend.ml import inference as inf
from backend.ml.train_outcome_model import fit_outcome_model, MODEL_PATH

PHASE3_DIR = _ROOT / "backend" / "data_factory" / "phase3_eval"

_results = []


def check(label, ok, detail=None):
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {label}"
    if detail is not None:
        line += f"  -- {detail}"
    print(line)
    _results.append((label, bool(ok)))
    return bool(ok)


def section(title):
    print("\n" + "=" * 90 + f"\n{title}\n" + "=" * 90)


def load_artifact(name):
    r = evl.verify_phase3_artifact(name)
    if not r["passed"]:
        raise RuntimeError(f"artifact '{name}' failed hash verification: {r}")
    df = feats.read_joint_csv(PHASE3_DIR / f"{name}.csv")
    return df, r


# --------------------------------------------------------------------- prediction

def predict_batch(artifact, df, health_lookup):
    X = feats.build_feature_frame_from_joint_df(df, health_lookup)
    p = artifact["p_pipeline"].predict_proba(X)[:, 1]
    amt = np.clip(artifact["amount_pipeline"].predict(X), 0.0, None)
    return p, amt, p * amt


# ------------------------------------------------------------------- 1. calibration

def calibration_metrics(y_true, p_pred, n_bins=10):
    frac_pos, mean_pred = calibration_curve(y_true, p_pred, n_bins=n_bins, strategy="quantile")
    gaps = np.abs(frac_pos - mean_pred)
    # weight ECE by (roughly) equal-frequency bin size -- quantile bins are
    # already ~equal count, so an unweighted mean is a fair approximation
    # without re-deriving bin membership counts here.
    return float(gaps.max()), float(gaps.mean()), list(zip(mean_pred.tolist(), frac_pos.tolist()))


def gate_calibration(artifact, health_lookup, df, cfg, label):
    min_cases = cfg.get("min_holdout_cases", cfg.get("min_stress_cases", 0))
    if df["case_id"].nunique() < min_cases:
        return check(f"{label}: calibration (insufficient data)", False,
                      f"{df['case_id'].nunique()} cases < min {min_cases}")
    p_pred, _, _ = predict_batch(artifact, df, health_lookup)
    max_gap, ece, bins = calibration_metrics(df["recovered"].values, p_pred, cfg.get("n_bins", 10))
    ok = (max_gap <= cfg["max_abs_bin_gap"]) and (ece <= cfg["max_expected_calibration_error"])
    return check(f"{label}: calibration (max_bin_gap<={cfg['max_abs_bin_gap']}, "
                 f"ece<={cfg['max_expected_calibration_error']})", ok,
                 f"max_bin_gap={max_gap:.4f} ece={ece:.4f} n_cases={df['case_id'].nunique()}")


# ------------------------------------------------------------- 2. treatment effect

def compute_effects(artifact, health_lookup, joint_df, truth_df):
    """Per-candidate model_p / model_expected_amount, merged 1:1 with the
    ground-truth companion's analytic_p (same strict-merge discipline as
    data_factory.validators.ground_truth_treatment_effect_check), then
    per-case effect = candidate - do_nothing, both empirically (model) and
    analytically (generator)."""
    key = ["case_id", "candidate_action", "candidate_timing", "candidate_method", "candidate_channel"]
    p_pred, amt_pred, expected_pred = predict_batch(artifact, joint_df, health_lookup)
    scored = joint_df[key + ["event_type", "root_cause", "candidate_method_changed"]].copy()
    scored["model_p"] = p_pred
    scored["model_expected_amount"] = expected_pred

    merged = scored.merge(truth_df[key + ["analytic_p"]], on=key, how="inner")
    assert len(merged) == len(scored), (
        f"compute_effects: merge produced {len(merged)} rows from {len(scored)} input rows -- "
        f"candidate key does not uniquely identify rows (expected a strict 1:1 join)."
    )
    merged["effect_class"] = merged.apply(
        lambda r: _root_cause_effect_class(r["candidate_action"], r["event_type"], r["root_cause"]), axis=1)
    merged["method_changed"] = merged["candidate_method_changed"].fillna(False)

    baseline = merged[merged["candidate_action"] == "do_nothing"].groupby("case_id").agg(
        base_model_p=("model_p", "mean"), base_analytic_p=("analytic_p", "mean"),
        base_model_amt=("model_expected_amount", "mean"))
    non_baseline = merged[merged["candidate_action"] != "do_nothing"].join(baseline, on="case_id", how="inner")
    non_baseline["model_effect"] = non_baseline["model_p"] - non_baseline["base_model_p"]
    non_baseline["analytic_effect"] = non_baseline["analytic_p"] - non_baseline["base_analytic_p"]
    return non_baseline


def gate_treatment_effect(artifact, health_lookup, joint_df, truth_df, cfg, label="treatment_effect"):
    eff = compute_effects(artifact, health_lookup, joint_df, truth_df)
    group_cols = ["candidate_action", "event_type", "effect_class", "method_changed"]

    per_bucket, matching, total_evaluated, total_dir_scored = [], 0, 0, 0
    for keys, sub in eff.groupby(group_cols):
        by_case = sub.groupby("case_id").agg(model_effect=("model_effect", "mean"),
                                              analytic_effect=("analytic_effect", "mean"))
        n = len(by_case)
        if n < cfg["min_cases_per_bucket_for_check"]:
            continue
        model_effect = float(by_case["model_effect"].mean())
        analytic_effect = float(by_case["analytic_effect"].mean())
        gap = abs(model_effect - analytic_effect)
        gap_ok = gap <= cfg["max_abs_effect_gap"]
        direction_scored = abs(analytic_effect) >= cfg["effect_size_floor_for_direction_test"]
        direction_match = (model_effect >= 0) == (analytic_effect >= 0)
        total_evaluated += 1
        if direction_scored:
            total_dir_scored += 1
            if direction_match:
                matching += 1
        per_bucket.append({"bucket_key": "|".join(map(str, keys)), "n_cases": n,
                            "model_effect": model_effect, "analytic_effect": analytic_effect,
                            "gap": gap, "gap_ok": gap_ok, "direction_scored": direction_scored,
                            "direction_match": direction_match if direction_scored else None})

    frac_matching = (matching / total_dir_scored) if total_dir_scored else 0.0
    all_gaps_ok = all(b["gap_ok"] for b in per_bucket)
    enough_buckets = total_dir_scored >= cfg["min_direction_scored_buckets"]
    passed = bool(total_dir_scored > 0 and all_gaps_ok
                  and frac_matching >= cfg["min_fraction_buckets_matching_direction"]
                  and enough_buckets)

    for b in per_bucket:
        tag = "skip" if not b["direction_scored"] else ("OK " if b["direction_match"] else "MISS")
        print(f"    [{tag}] {b['bucket_key']:55s} n={b['n_cases']:4d} "
              f"model={b['model_effect']:+.4f} analytic={b['analytic_effect']:+.4f} "
              f"gap={b['gap']:.4f} gap_ok={b['gap_ok']}")

    check(f"{label}: gaps within tolerance ({cfg['max_abs_effect_gap']})", all_gaps_ok)
    check(f"{label}: >= {cfg['min_direction_scored_buckets']} direction-scored buckets", enough_buckets,
          f"{total_dir_scored} scored")
    ok = check(f"{label}: fraction matching direction >= {cfg['min_fraction_buckets_matching_direction']}",
               passed, f"{frac_matching:.3f} ({matching}/{total_dir_scored}), buckets_evaluated={total_evaluated}")
    return ok, per_bucket


# ----------------------------------------------------------- 3. cross-profile (amended)

def gate_cross_profile(artifact, stress_lookup, in_profile_lookup, stress_df, in_profile_df, cfg):
    """
    AMENDED 2026-09-01T18:10:24Z by independent review: ranking-transfer
    criterion, NOT a raw-ECE bound. See locked_thresholds.json
    phase3_cross_profile._amendment_reason for the full justification (AUC
    invariance under monotone transforms; the null-result experiment; the
    absence of any valid disjoint stress calibration sample).

    GATING:     stress ranking AUC must not degrade from in-profile AUC by
                more than max_abs_auc_degradation_vs_in_profile, and must
                clear min_stress_auc_absolute.
    REPORTED,   raw calibration level (ECE / maxgap) under the stress
    NOT GATING: profile -- a disclosed known limitation.
    """
    if stress_df["case_id"].nunique() < cfg["min_stress_cases"]:
        return check("cross_profile: ranking transfer (insufficient data)", False,
                      f"{stress_df['case_id'].nunique()} < {cfg['min_stress_cases']}")

    p_in, _, _ = predict_batch(artifact, in_profile_df, in_profile_lookup)
    y_in = in_profile_df["recovered"].astype(int).to_numpy()
    p_st, _, _ = predict_batch(artifact, stress_df, stress_lookup)
    y_st = stress_df["recovered"].astype(int).to_numpy()

    auc_in, auc_st = roc_auc_score(y_in, p_in), roc_auc_score(y_st, p_st)
    degradation = auc_in - auc_st

    ok = check(f"cross_profile: ranking transfer (AUC degradation <= "
               f"{cfg['max_abs_auc_degradation_vs_in_profile']}, stress AUC >= "
               f"{cfg['min_stress_auc_absolute']})",
               (degradation <= cfg["max_abs_auc_degradation_vs_in_profile"]
                and auc_st >= cfg["min_stress_auc_absolute"]),
               f"in_profile_AUC={auc_in:.4f} stress_AUC={auc_st:.4f} "
               f"degradation={degradation:+.4f} n_cases={stress_df['case_id'].nunique()}")

    max_gap, ece, _ = calibration_metrics(y_st, p_st, cfg.get("n_bins", 10))
    print(f"    [KNOWN LIMITATION, reported not gated] stress calibration LEVEL: "
          f"ECE={ece:.4f} maxgap={max_gap:.4f} "
          f"(mean_pred={p_st.mean():.4f} vs mean_actual={y_st.mean():.4f}) -- "
          f"unobservable global_intercept_shift=-0.35; corrected in production by "
          f"Phase 6+ recalibration, not by the model.")
    return ok


# --------------------------------------------------------------- 4. temporal ranking

def gate_temporal(artifact, health_lookup, temporal_df, temporal_truth_df, cfg):
    ok_cal = gate_calibration(artifact, health_lookup, temporal_df, {
        "max_abs_bin_gap": cfg["calibration_max_abs_bin_gap"],
        "max_expected_calibration_error": cfg["calibration_max_expected_calibration_error"],
        "min_holdout_cases": 100, "n_bins": cfg.get("n_bins", 10),
    }, "temporal")

    eff = compute_effects(artifact, health_lookup, temporal_df, temporal_truth_df)
    floor = cfg["ranking_effect_size_floor"]
    n_correct, n_pairs = 0, 0
    for case_id, sub in eff.groupby("case_id"):
        recs = sub[["analytic_effect", "model_effect"]].to_dict("records")
        for i in range(len(recs)):
            for j in range(len(recs)):
                if i == j:
                    continue
                a, b = recs[i], recs[j]
                if a["analytic_effect"] - b["analytic_effect"] >= floor:
                    n_pairs += 1
                    # like-for-like: model's probability-space treatment
                    # effect vs the generator's probability-space effect (see
                    # the phase3_temporal ranking_pair_definition amendment)
                    if a["model_effect"] >= b["model_effect"]:
                        n_correct += 1

    if n_pairs < cfg["min_ranking_pairs"]:
        ok_rank = check("temporal: ranking-direction (insufficient pairs)", False,
                         f"{n_pairs} pairs < min {cfg['min_ranking_pairs']}")
    else:
        agreement = n_correct / n_pairs
        ok_rank = check(f"temporal: ranking-direction agreement >= {cfg['min_ranking_direction_agreement']}",
                         agreement >= cfg["min_ranking_direction_agreement"],
                         f"{agreement:.3f} ({n_correct}/{n_pairs} pairs)")
    return bool(ok_cal and ok_rank)


# ------------------------------------------------------------------------ 6. parity

def gate_parity(artifact, health_lookup, health_df, sample_df, cfg):
    """Two independent invocations must produce identical scores for the same
    (context, candidate):
      (a) the BATCH harness path  -- predict_batch() -> build_feature_frame_
          from_joint_df() -> pipeline.predict, exactly as every gate above
          scores a whole dataset;
      (b) the LIVE path           -- ml.inference.score_candidate() one case
          at a time, exactly as Phase 4's optimizer will call it.
    Plus a check that NetworkHealthLookup (the bulk lookup both paths share)
    reproduces network_health_rolling() (the semantic reference)."""
    # (0) bulk lookup vs reference. The prefix-sum lookup and the reference's
    # pandas .mean() sum the same values in a different order, so they agree
    # only to floating-point epsilon (~1e-14) -- checked against the locked
    # phase3_parity.fallback_atol (1e-9), NOT bitwise, since FP associativity
    # is not a real train/serve divergence. The batch-vs-live score check
    # below IS bitwise-exact (same pipeline, same feature row).
    lookup_atol = cfg["fallback_atol_if_platform_float_nondeterminism"]
    ref_samples = [(r["bank"], r["current_method"], r["psp"], r["sim_hour"])
                   for r in sample_df.to_dict("records")]
    lookup_ref_diff = health_lookup.verify_against_reference(health_df, ref_samples)
    check(f"parity: NetworkHealthLookup reproduces network_health_rolling() "
          f"(atol {lookup_atol})", lookup_ref_diff <= lookup_atol,
          f"max_diff={lookup_ref_diff:.2e}")

    # point the live module at exactly this artifact + this lookup
    inf.reset_cache()
    inf._MODEL, inf._MODEL_LOAD_ATTEMPTED = artifact, True
    inf._HEALTH_LOOKUP = health_lookup

    # (a) batch path over the whole sample
    p_batch, amt_batch, expected_batch = predict_batch(artifact, sample_df, health_lookup)

    # (b) live path, one case at a time
    max_diff = 0.0
    records = sample_df.to_dict("records")
    for i, r in enumerate(records):
        context, candidate = feats.context_and_candidate_from_joint_row(r)
        live = inf.score_candidate(context, candidate, conn=None)
        assert live["error"] is None, f"live scoring errored on a well-formed case: {live}"
        max_diff = max(max_diff, abs(live["p_recovery"] - p_batch[i]),
                       abs(live["expected_recovered_amount"] - expected_batch[i]))
    n_checked = len(records)

    # constructed edge cases: do_nothing, a method_change candidate, a
    # null-health case (unknown channel) -- live path only (no batch-frame
    # equivalent needed; build_feature_frame_from_joint_df IS a loop over
    # build_feature_row, so batch==single by construction for these).
    base_context, _ = feats.context_and_candidate_from_joint_row(records[0])
    edge_cases = [
        (base_context, feats.do_nothing_candidate(), "do_nothing"),
        (base_context, {"action_type": "retry", "timing": "immediate", "timing_hours": 0.0,
                         "method": "upi" if base_context["current_method"] != "upi" else "wallet",
                         "method_changed": True, "channel": "n/a"}, "method_change"),
        ({**base_context, "bank": "unknown_bank_xyz", "psp": "unknown_psp_xyz"},
         {"action_type": "reminder", "timing": "4h", "timing_hours": 4.0, "method": "n/a",
          "channel": "email", "method_changed": False}, "null-health"),
    ]
    for context, candidate, lbl in edge_cases:
        br = feats.build_feature_row(context, candidate, health_lookup)
        X = pd.DataFrame([br])[feats.ALL_FEATURES]
        p_b = float(artifact["p_pipeline"].predict_proba(X)[:, 1][0])
        amt_b = max(0.0, float(artifact["amount_pipeline"].predict(X)[0]))
        live = inf.score_candidate(context, candidate, conn=None)
        assert live["error"] is None, f"live scoring errored on edge case {lbl}: {live}"
        max_diff = max(max_diff, abs(live["p_recovery"] - p_b),
                       abs(live["expected_recovered_amount"] - p_b * amt_b))
        n_checked += 1

    ok = check(f"parity: batch path vs ml.inference.py, max score diff <= "
               f"{cfg['max_abs_score_difference']} (exact)",
               max_diff <= cfg["max_abs_score_difference"],
               f"max_diff={max_diff:.2e} over {n_checked} cases (incl. do_nothing, "
               f"method_change, null-health)")
    inf.reset_cache()
    return bool(ok and lookup_ref_diff <= lookup_atol)


# ---------------------------------------------------------------- 7. failure behavior

def gate_failure_behavior():
    malformed = [
        ({}, feats.do_nothing_candidate(), "empty context"),
        ({"event_type": "not_a_real_event_type", "amount": 100, "current_method": "card",
          "retry_count": 0, "days_since_event": 1, "payment_history_score": 0.5,
          "past_recovery_rate": 0.5, "preferred_channel": "email", "merchant_cohort": "smb",
          "bank": "hdfc", "psp": "razorpay_gw_a", "decision_time_hours": 10},
         feats.do_nothing_candidate(), "unknown event_type"),
        ({"event_type": "payment_failed", "amount": -500, "current_method": "card",
          "retry_count": 0, "days_since_event": 1, "payment_history_score": 0.5,
          "past_recovery_rate": 0.5, "preferred_channel": "email", "merchant_cohort": "smb",
          "bank": "hdfc", "psp": "razorpay_gw_a", "decision_time_hours": 10},
         feats.do_nothing_candidate(), "negative amount"),
        ({"event_type": "payment_failed", "amount": 100, "current_method": "card",
          "retry_count": 0, "days_since_event": 1, "payment_history_score": float("nan"),
          "past_recovery_rate": 0.5, "preferred_channel": "email", "merchant_cohort": "smb",
          "bank": "hdfc", "psp": "razorpay_gw_a", "decision_time_hours": 10},
         feats.do_nothing_candidate(), "NaN confidence-like numeric field"),
        ({"event_type": "payment_failed", "amount": 100, "current_method": "card",
          "retry_count": 0, "days_since_event": 1, "payment_history_score": 0.5,
          "past_recovery_rate": 0.5, "preferred_channel": "email", "merchant_cohort": "smb",
          "bank": "hdfc", "psp": "razorpay_gw_a", "decision_time_hours": 10},
         {"action_type": "not_a_real_action", "timing": "immediate", "timing_hours": 0.0,
          "method": "n/a", "channel": "n/a"}, "unknown candidate action_type"),
    ]
    all_ok = True
    for context, candidate, label in malformed:
        try:
            result = inf.score_candidate(context, candidate)
        except Exception as e:
            all_ok = False
            check(f"failure-behavior: {label} -> flagged null (raised instead: {e!r})", False)
            continue
        flagged = (result.get("error") is not None and result.get("p_recovery") is None
                   and result.get("expected_recovered_amount") is None)
        all_ok = all_ok and flagged
        check(f"failure-behavior: {label} -> flagged null, never a plausible score", flagged,
              result.get("error"))
    return all_ok


# ------------------------------------------------------------- 8. authority boundary

_FORBIDDEN_IN_INFERENCE = {"execute_action", "decide_action", "mark_opportunity_recovered",
                           "recovery_decisions", "recovery_executions"}


def gate_authority_boundary():
    """Mechanical (not review-convention) check that ml/inference.py -- the
    module Phase 4's optimizer will import -- has NO path to execution
    authority: no import of an engine control function, no reference to a
    compliance/execution write target. Mirrors
    data_factory.validators.static_no_execution_authority_check()'s intent
    for the new ml/ modules."""
    import ast as _ast
    ok = True
    for name in ("inference.py", "outcome_features.py"):
        path = _ROOT / "backend" / "ml" / name
        tree = _ast.parse(path.read_text(), filename=str(path))
        hits = []
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                names = [a.name for a in node.names]
                if "engine" in mod or any("engine" in n for n in names):
                    hits.append(f"imports engine: {mod or names}")
                for f in _FORBIDDEN_IN_INFERENCE:
                    if f in names or f == mod:
                        hits.append(f"imports {f}")
            elif isinstance(node, _ast.Name) and node.id in _FORBIDDEN_IN_INFERENCE:
                hits.append(f"references {node.id}")
        ok = check(f"authority: backend/ml/{name} has no execution-authority path",
                   len(hits) == 0, hits or None) and ok
    return ok


# --------------------------------------------------------------------- 5. multiseed

# Independent-review waiver, recorded 2026-09-01. NOT a threshold change --
# phase3_multiseed's locked criterion is untouched. This is one explicitly
# disclosed exception applied at the REPORT layer, and the gate suite prints it
# loudly every run: a waived exception must never look like a silent pass.
#
# Waived: seed 43, phase3_treatment_effect, bucket
#   retry|payment_failed|insufficient_funds|same_method  (n=160)
# Grounds (reviewer's, verbatim in PHASE3_NOTES.md): the bucket was already
# flagged in PHASE3_NOTES section 3 as unstable BEFORE the feature change --
# seed 43's own realized empirical effect (+0.020) already disagreed with its
# analytic ground truth (-0.055) at the Phase 2 dataset level. Magnitude still
# passes (gap 0.065 < 0.10 tolerance); only the SIGN flips, on an effect sitting
# at the noise floor (~0.022 probability-sd per draw). The sharper model is more
# faithful to noisy training data here, not less accurate.
MULTISEED_WAIVERS = [{
    "seed": 43,
    "gate": "treatment_effect",
    # NOTE the key format: gate_treatment_effect() above renders method_changed
    # as the raw bool ("False"), whereas data_factory/validators.py's Phase 2
    # check renders the same field as "same_method"/"method_changed". Same
    # bucket, two spellings. The waiver must carry THIS module's spelling or it
    # silently never fires (it did not, on the first run -- the exact-match
    # guard refused to apply it, which is the intended failure direction).
    "bucket_key": "retry|payment_failed|insufficient_funds|False",
    "reason": "pre-existing dataset-level instability (PHASE3_NOTES section 3); "
              "magnitude within tolerance (gap 0.065 < 0.10), sign only, effect at "
              "the ~0.022 noise floor",
}]

def run_seed_gates(seed, thresholds, label_prefix):
    """Fits a fresh model on seed's own carved training_pool and runs
    calibration + treatment_effect + temporal against that seed's own
    holdouts. Deliberately does NOT include cross_profile -- no per-seed
    stress dataset was generated (Decision F is about baseline-profile
    multi-seed robustness; commissioning 43/44 stress datasets was judged
    out of the minimal scope this task asked for)."""
    joint_df, _ = load_artifact(f"phase3_baseline_seed{seed}_joint")
    truth_df, _ = load_artifact(f"phase3_baseline_seed{seed}_truth")
    conn = db_module.get_connection()
    health_lookup = feats.NetworkHealthLookup(feats.load_health_observations(conn))
    conn.close()

    splits, meta = evl.carve_phase3_splits(joint_df, thresholds)
    print(f"  seed {seed} split: train_pool={meta['n_cases_training_pool']} "
          f"calib={meta['n_cases_calibration_holdout']} temporal={meta['n_cases_temporal_holdout']}")

    print(f"  seed {seed}: fitting fresh model on its own training_pool...")
    artifact = fit_outcome_model(splits["training_pool"], health_lookup, verbose=False)

    calib_cases = set(splits["calibration_holdout"]["case_id"])
    temporal_cases = set(splits["temporal_holdout"]["case_id"])
    trt_pop_cases = set(splits["training_pool"]["case_id"]) | calib_cases

    ok_cal = gate_calibration(artifact, health_lookup, splits["calibration_holdout"],
                               thresholds["phase3_calibration"], f"{label_prefix}calibration")
    ok_trt, per_bucket = gate_treatment_effect(
        artifact, health_lookup, joint_df[joint_df["case_id"].isin(trt_pop_cases)],
        truth_df[truth_df["case_id"].isin(trt_pop_cases)],
        thresholds["phase3_treatment_effect"], f"{label_prefix}treatment_effect")
    ok_temp = gate_temporal(artifact, health_lookup, splits["temporal_holdout"],
                             truth_df[truth_df["case_id"].isin(temporal_cases)],
                             thresholds["phase3_temporal"])

    signs = {b["bucket_key"]: (b["model_effect"] >= 0) for b in per_bucket if b["direction_scored"]}
    # which direction-scored buckets actually missed -- needed so the documented
    # waiver can be applied to a SPECIFIC bucket and nothing else
    missed = [b["bucket_key"] for b in per_bucket
              if b["direction_scored"] and not b["direction_match"]]
    sub = {"calibration": bool(ok_cal), "treatment_effect": bool(ok_trt),
           "temporal": bool(ok_temp)}
    return sub, signs, missed


def apply_multiseed_waivers(seed, sub, missed):
    """Applies the independent-review waivers above to ONE seed's sub-gate
    results. Returns (effective_sub, applied, blocked) where `applied` lists
    waivers that fired and `blocked` lists sub-gates still failing on their own
    merits. A waiver fires only if the named bucket is the ONLY direction miss
    for that seed -- it can never mask a second, undisclosed failure."""
    eff, applied, blocked = dict(sub), [], []
    for w in MULTISEED_WAIVERS:
        if w["seed"] != seed or sub.get(w["gate"], True):
            continue
        if w["gate"] == "treatment_effect" and missed == [w["bucket_key"]]:
            eff[w["gate"]] = True
            applied.append(w)
        else:
            blocked.append((w["gate"], f"waiver NOT applied -- misses were {missed}, "
                                        f"expected exactly ['{w['bucket_key']}']"))
    for g, ok in eff.items():
        if not ok:
            blocked.append((g, "no waiver covers this failure"))
    return eff, applied, blocked


def gate_multiseed(thresholds):
    cfg = thresholds["phase3_multiseed"]
    seeds = cfg["seeds"]
    per_seed_pass = {}
    per_seed_signs = {}
    waived_exceptions = []
    for seed in seeds:
        section(f"Multi-seed: seed {seed}")
        if seed == 42:
            # Reuse the already-trained, already-gated primary model rather
            # than refitting -- its gate results are computed elsewhere in
            # this run (sections 1/2/4); here we only need its per-bucket
            # signs for the cross-seed sign-stability comparison.
            joint_df, _ = load_artifact("phase3_baseline_seed42_joint")
            truth_df, _ = load_artifact("phase3_baseline_seed42_truth")
            training_pool, _ = load_artifact("phase3_baseline_seed42_training_pool")
            calib_holdout, _ = load_artifact("phase3_baseline_seed42_calibration_holdout")
            trt_pop_cases = set(training_pool["case_id"]) | set(calib_holdout["case_id"])
            conn = db_module.get_connection()
            health_lookup = feats.NetworkHealthLookup(feats.load_health_observations(conn))
            conn.close()
            artifact = joblib.load(MODEL_PATH)
            _, per_bucket = gate_treatment_effect(
                artifact, health_lookup, joint_df[joint_df["case_id"].isin(trt_pop_cases)],
                truth_df[truth_df["case_id"].isin(trt_pop_cases)],
                thresholds["phase3_treatment_effect"], "seed42(primary):treatment_effect")
            per_seed_signs[seed] = {b["bucket_key"]: (b["model_effect"] >= 0)
                                     for b in per_bucket if b["direction_scored"]}
            per_seed_pass[seed] = True  # primary model's own gates are checked in sections 1/2/4
            continue
        sub, signs, missed = run_seed_gates(seed, thresholds, f"seed{seed}:")
        eff, applied, blocked = apply_multiseed_waivers(seed, sub, missed)
        for w in applied:
            waived_exceptions.append((seed, w))
            print(f"    [WAIVED -- DISCLOSED EXCEPTION] seed {w['seed']} {w['gate']}: "
                  f"bucket {w['bucket_key']} -- {w['reason']}")
            print(f"    (independent-review decision 2026-09-01; phase3_multiseed's locked "
                  f"criterion is UNCHANGED -- this is an exception, not a passing result)")
        for g, why in blocked:
            print(f"    [NOT WAIVED] seed {seed} {g}: {why}")
        per_seed_pass[seed] = all(eff.values())
        per_seed_signs[seed] = signs

    # Cross-seed sign stability: for any bucket key that is direction-scored
    # on more than one seed, its model-effect sign must agree across those
    # seeds.
    all_keys = set()
    for s in per_seed_signs.values():
        all_keys |= set(s.keys())
    conflicts = []
    for k in all_keys:
        seen = {seed: per_seed_signs[seed][k] for seed in seeds if k in per_seed_signs[seed]}
        if len(set(seen.values())) > 1:
            conflicts.append((k, seen))

    all_gates_pass = all(per_seed_pass.values())
    label = ("multiseed: all per-seed hard gates pass (CONDITIONAL -- "
             f"{len(waived_exceptions)} disclosed waived exception(s))"
             if waived_exceptions else "multiseed: all per-seed hard gates pass")
    detail = dict(per_seed_pass)
    if waived_exceptions:
        detail["waived"] = [f"seed{s}:{w['gate']}:{w['bucket_key']}" for s, w in waived_exceptions]
    check(label, all_gates_pass, detail)
    ok_signs = check("multiseed: cross-seed bucket-sign stability", len(conflicts) == 0,
                      f"{len(conflicts)} conflicting bucket(s): {conflicts}" if conflicts
                      else "no conflicts")
    return bool(all_gates_pass and ok_signs)


# --------------------------------------------------------------------------- main

def main():
    t0 = time.time()
    thresholds = load_thresholds()

    section("Load model artifact")
    artifact = joblib.load(MODEL_PATH)
    check("outcome_model.joblib loads", artifact is not None)

    conn = db_module.get_connection()
    health_df = feats.load_health_observations(conn)
    conn.close()
    check("bank_health_observations populated", len(health_df) > 0, f"{len(health_df)} rows")
    health_lookup = feats.NetworkHealthLookup(health_df)

    section("Load frozen Phase 3 artifacts (hash-verified)")
    training_pool, _ = load_artifact("phase3_baseline_seed42_training_pool")
    calib_holdout, _ = load_artifact("phase3_baseline_seed42_calibration_holdout")
    temporal_holdout, _ = load_artifact("phase3_baseline_seed42_temporal_holdout")
    joint42, _ = load_artifact("phase3_baseline_seed42_joint")
    truth42, _ = load_artifact("phase3_baseline_seed42_truth")
    stress_joint, _ = load_artifact("phase3_stress_seed42_joint")
    check("all primary artifacts hash-verified", True)

    section("Gate 1: phase3_calibration (calibration_holdout, UNSEEN)")
    gate_calibration(artifact, health_lookup, calib_holdout, thresholds["phase3_calibration"], "primary")

    section("Gate 2: phase3_treatment_effect (training_pool UNION calibration_holdout)")
    trt_pop_cases = set(training_pool["case_id"]) | set(calib_holdout["case_id"])
    gate_treatment_effect(artifact, health_lookup,
                           joint42[joint42["case_id"].isin(trt_pop_cases)],
                           truth42[truth42["case_id"].isin(trt_pop_cases)],
                           thresholds["phase3_treatment_effect"], "primary")

    section("Gate 3: phase3_cross_profile -- AMENDED 2026-09-01: ranking-transfer criterion "
            "(raw calibration level reported as a known limitation, not gated)")
    # The stress dataset's outcomes were generated under the STRESS profile's
    # own network-health series -- so the model's network-health feature for
    # these rows must be computed from stress health, not the baseline health
    # in bank_health_observations (which is what a live serve against a
    # stress-shifted world would also see). Deterministic, same generator.
    stress_health_df = pd.DataFrame([
        o.__dict__ for o in bank_health_setup.regenerate_health_observations(
            seed=42, profile_name="stress")])
    stress_lookup = feats.NetworkHealthLookup(stress_health_df)
    gate_cross_profile(artifact, stress_lookup, health_lookup, stress_joint, calib_holdout,
                       thresholds["phase3_cross_profile"])

    section("Gate 4: phase3_temporal (temporal_holdout, UNSEEN time window)")
    temporal_truth = truth42[truth42["case_id"].isin(set(temporal_holdout["case_id"]))]
    gate_temporal(artifact, health_lookup, temporal_holdout, temporal_truth, thresholds["phase3_temporal"])

    section("Gate 5: phase3_multiseed (supporting)")
    gate_multiseed(thresholds)

    section("Gate 6: phase3_parity (batch harness vs ml.inference.py)")
    sample = calib_holdout.sample(n=min(30, len(calib_holdout)), random_state=7)
    gate_parity(artifact, health_lookup, health_df, sample, thresholds["phase3_parity"])
    inf.reset_cache()  # restore normal lazy-load behavior for any later caller

    section("Gate 7: failure behavior (malformed cases -> flagged null)")
    gate_failure_behavior()

    section("Gate 8: authority boundary (ml/inference.py has no execution path)")
    gate_authority_boundary()

    section("VERDICT")
    npass = sum(1 for _, ok in _results if ok)
    print(f"{npass}/{len(_results)} checks passed   ({time.time() - t0:.1f}s)")
    failed = [lbl for lbl, ok in _results if not ok]
    if failed:
        print("\nFAILED:")
        for lbl in failed:
            print(f"  - {lbl}")
        return 1
    print("\nRESULT: all Phase 3 gates pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
