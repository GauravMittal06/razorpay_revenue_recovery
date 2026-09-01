"""
Data Factory -- eval-set lock. Added after Phase 2's first review round,
closing the "[NEW] Eval-set lock" gap in the Phase Acceptance Test Gates
document: "The dataset(s) reserved for final unseen evaluation (stress
profile, later temporal window) are generated and hashed/committed before
any model tuning in Phase 3 begins; that hash is checked at Phase 3 and
Phase 9."

This module does the "generated and hashed/committed" half. The temporal
boundary itself was already declared in locked_thresholds.json's
`unseen_eval_split` block (fraction=0.15, applies to the baseline
profile) -- unchanged here. What was missing was an actual materialized
file and a committed hash for it; this module produces both.

Because Phase 3 does not exist yet in this codebase, "before any model
tuning in Phase 3 begins" is trivially and honestly satisfied by running
this once, now, and never touching the resulting file or manifest again.
Phase 3 (and Phase 9's audit) are expected to call `verify_eval_set()`
against the committed manifest before doing anything else with the
holdout file.
"""

import hashlib
import json
import time
from pathlib import Path

import numpy as np

THIS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = THIS_DIR / "output"

# --- Phase 3 eval-artifact lock -------------------------------------------------
# The Phase 2 lock_eval_set/verify_eval_set above stay exactly as they were
# (baseline temporal holdout only, manifest keyed by profile name). Phase 3
# (Decisions A/F/G) needs to additionally freeze: the stress-profile dataset,
# a second "calibration holdout" slice carved from the baseline visible cases,
# and multi-seed (43/44) baseline datasets. Those go in a SEPARATE, Phase-3-
# owned directory + manifest so the Phase 2 artifact is never touched, keyed by
# an explicit artifact name (not just a profile) since there are now several per
# profile.
#
# Deliberately NOT under output/: phase2_verify.py wipes output/ on every run
# (it rebuilds everything from scratch), which would silently destroy the frozen
# Phase 3 eval set. phase3_eval/ is never touched by any Phase 2 script.
PHASE3_DIR = THIS_DIR / "phase3_eval"
PHASE3_MANIFEST = PHASE3_DIR / "phase3_eval_lock.json"
PHASE3_HASH_ALGO = "sha256"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_holdout(joint_df, thresholds):
    """
    Splits joint_df into (visible, holdout) by sim_hour, grouped by
    case_id (a case's rows never straddle the boundary). The boundary is
    the (1 - temporal_holdout_fraction) quantile of per-case sim_hour --
    computed from thresholds locked in locked_thresholds.json's
    `unseen_eval_split` block, not chosen after looking at this data.
    """
    frac = thresholds["unseen_eval_split"]["temporal_holdout_fraction"]
    case_hours = joint_df.groupby("case_id")["sim_hour"].first()
    boundary = float(case_hours.quantile(1 - frac))
    holdout_cases = set(case_hours[case_hours >= boundary].index)
    holdout_df = joint_df[joint_df["case_id"].isin(holdout_cases)].copy()
    visible_df = joint_df[~joint_df["case_id"].isin(holdout_cases)].copy()
    return visible_df, holdout_df, boundary


def lock_eval_set(joint_df, profile_name, seed, generator_version, thresholds):
    """
    Generates the holdout CSV, hashes it, and writes a committed manifest
    (eval_set_manifest.json, one entry per profile). Returns the manifest
    entry written. Idempotent in the sense that re-running with identical
    input reproduces an identical hash (this rides on the same generator
    reproducibility already proven in validators.reproducibility_check).
    """
    cfg = thresholds["eval_set_lock"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    visible_df, holdout_df, boundary = split_holdout(joint_df, thresholds)

    holdout_path = OUTPUT_DIR / cfg["holdout_filename_template"].format(profile=profile_name, seed=seed)
    holdout_df.to_csv(holdout_path, index=False)
    file_hash = sha256_file(holdout_path)

    entry = {
        "profile": profile_name,
        "seed": seed,
        "generator_version": generator_version,
        "sim_hour_boundary": boundary,
        "temporal_holdout_fraction": thresholds["unseen_eval_split"]["temporal_holdout_fraction"],
        "holdout_row_count": int(len(holdout_df)),
        "holdout_case_count": int(holdout_df["case_id"].nunique()),
        "visible_row_count": int(len(visible_df)),
        "visible_case_count": int(visible_df["case_id"].nunique()),
        "holdout_file": str(holdout_path.name),
        "sha256": file_hash,
        "hash_algorithm": cfg["hash_algorithm"],
        "locked_at": int(time.time()),
    }

    manifest_path = OUTPUT_DIR / cfg["manifest_filename"]
    manifest = {}
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    manifest[profile_name] = entry
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    return entry


def verify_eval_set(profile_name, thresholds):
    """
    Integrity check: recompute the holdout file's hash and compare
    against the committed manifest entry. This is what Phase 3 and
    Phase 9 are expected to call before using the holdout file for
    anything -- a mismatch means the file was regenerated or edited
    after locking, which invalidates any "unseen" claim about it.
    """
    cfg = thresholds["eval_set_lock"]
    manifest_path = OUTPUT_DIR / cfg["manifest_filename"]
    if not manifest_path.exists():
        return {"passed": False, "reason": "no manifest found -- eval set was never locked"}

    with open(manifest_path) as f:
        manifest = json.load(f)
    if profile_name not in manifest:
        return {"passed": False, "reason": f"no locked entry for profile '{profile_name}'"}

    entry = manifest[profile_name]
    holdout_path = OUTPUT_DIR / entry["holdout_file"]
    if not holdout_path.exists():
        return {"passed": False, "reason": f"holdout file {holdout_path} missing"}

    current_hash = sha256_file(holdout_path)
    passed = current_hash == entry["sha256"]
    return {"passed": passed, "locked_hash": entry["sha256"], "current_hash": current_hash,
            "locked_at": entry["locked_at"], "holdout_case_count": entry["holdout_case_count"]}


# =============================================================================
# Phase 3 eval-artifact lock (Decisions A / F / G)
# =============================================================================

def _read_phase3_manifest() -> dict:
    if PHASE3_MANIFEST.exists():
        with open(PHASE3_MANIFEST) as f:
            return json.load(f)
    return {"_manifest_notice": (
        "Phase 3 frozen eval artifacts. Every entry's sha256 is committed BEFORE "
        "any Phase 3 model is trained/evaluated. Phase 3 (train_outcome_model.py) "
        "and Phase 9 must call verify_phase3_artifact()/verify_all_phase3() and "
        "abort on any mismatch before using a file."), "artifacts": {}}


def _write_phase3_manifest(manifest: dict) -> None:
    PHASE3_DIR.mkdir(parents=True, exist_ok=True)
    with open(PHASE3_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def carve_phase3_splits(joint_df, thresholds, carve_seed=None):
    """
    The 4-way split of the baseline seed-42 joint dataset, per
    locked_thresholds.json['phase3_data_split'] (Decision A):

        temporal_holdout   -- latest `temporal_holdout_fraction` of cases by
                              per-case sim_hour. Customer-disjointness NOT
                              enforced against the rest (Decision K).
        calibration_holdout -- carved by CUSTOMER from the pool of cases earlier
                              than the temporal boundary: the earlier-pool
                              customers are shuffled with the carve seed and
                              added one at a time until the accumulated
                              earlier-pool case count first reaches
                              `calibration_holdout_fraction_of_all_cases` * (all
                              cases). Every one of that customer's earlier-pool
                              cases is in the calibration holdout; none of any
                              other customer's are -> training_pool and
                              calibration_holdout are exactly customer-disjoint
                              by construction.
        training_pool      -- every earlier-pool case whose customer was not
                              taken into the calibration holdout. Phase 3 splits
                              train / model-selection-val from THIS at fit time;
                              those two are not hash-locked.

    Returns (splits: dict[str, DataFrame], meta: dict). Deterministic given
    (joint_df, thresholds, carve_seed).
    """
    cfg = thresholds["phase3_data_split"]
    if carve_seed is None:
        carve_seed = cfg["calibration_holdout_carve_seed"]

    case_hours = joint_df.groupby("case_id")["sim_hour"].first().sort_values()
    all_cases = list(case_hours.index)
    n_all = len(all_cases)
    cust_by_case = joint_df.groupby("case_id")["customer_id"].first().to_dict()

    # 1. temporal holdout: latest fraction by sim_hour (case-level; Decision K)
    t_frac = cfg["temporal_holdout_fraction"]
    boundary = float(case_hours.quantile(1.0 - t_frac))
    temporal_cases = set(case_hours[case_hours >= boundary].index)
    earlier_cases = [c for c in all_cases if c not in temporal_cases]

    # 2. calibration holdout: carve by CUSTOMER from the earlier pool until the
    #    accumulated earlier-pool case count reaches calib_frac * n_all_cases.
    calib_frac = cfg["calibration_holdout_fraction_of_all_cases"]
    n_calib_target = int(round(calib_frac * n_all))

    earlier_cases_by_cust = {}
    for c in sorted(earlier_cases):
        earlier_cases_by_cust.setdefault(cust_by_case[c], []).append(c)

    rng = np.random.default_rng(carve_seed)
    earlier_custs = sorted(earlier_cases_by_cust)          # stable order in
    rng.shuffle(earlier_custs)                             # -> deterministic shuffle

    calib_cases, calib_custs = set(), set()
    for cust in earlier_custs:
        if len(calib_cases) >= n_calib_target:
            break
        calib_custs.add(cust)
        calib_cases.update(earlier_cases_by_cust[cust])

    training_cases = {c for c in earlier_cases if cust_by_case[c] not in calib_custs}

    def _sub(df, cases):
        return df[df["case_id"].isin(cases)].copy()

    splits = {
        "training_pool": _sub(joint_df, training_cases),
        "calibration_holdout": _sub(joint_df, calib_cases),
        "temporal_holdout": _sub(joint_df, temporal_cases),
    }

    train_custs = set(cust_by_case[c] for c in training_cases)
    calib_custs = set(cust_by_case[c] for c in calib_cases)
    meta = {
        "carve_seed": carve_seed,
        "sim_hour_boundary": boundary,
        "n_cases_total": n_all,
        "n_cases_training_pool": len(training_cases),
        "n_cases_calibration_holdout": len(calib_cases),
        "n_cases_temporal_holdout": len(temporal_cases),
        "case_disjoint": bool(
            not (training_cases & calib_cases)
            and not (training_cases & temporal_cases)
            and not (calib_cases & temporal_cases)
        ),
        "training_vs_calibration_customer_overlap": len(train_custs & calib_custs),
        "training_vs_temporal_customer_overlap_ACCEPTED": len(
            train_custs & set(cust_by_case[c] for c in temporal_cases)
        ),
    }
    return splits, meta


def lock_phase3_artifact(name, df, extra: dict = None):
    """
    Materialize `df` to phase3_eval/<name>.csv, hash it, and commit the hash to
    phase3_eval/phase3_eval_lock.json under key `name`. Returns the manifest
    entry. Idempotent given identical `df` (rides on generator reproducibility).
    """
    PHASE3_DIR.mkdir(parents=True, exist_ok=True)
    path = PHASE3_DIR / f"{name}.csv"
    df.to_csv(path, index=False)
    file_hash = sha256_file(path)

    entry = {
        "artifact": name,
        "file": path.name,
        "row_count": int(len(df)),
        "case_count": int(df["case_id"].nunique()) if "case_id" in df.columns else None,
        "sha256": file_hash,
        "hash_algorithm": PHASE3_HASH_ALGO,
        "locked_at": int(time.time()),
    }
    if extra:
        entry.update(extra)

    manifest = _read_phase3_manifest()
    manifest["artifacts"][name] = entry
    manifest["locked_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_phase3_manifest(manifest)
    return entry


def verify_phase3_artifact(name):
    """Recompute one artifact's hash and compare to the committed manifest."""
    if not PHASE3_MANIFEST.exists():
        return {"passed": False, "reason": "phase3_eval_lock.json not found -- artifacts never locked"}
    manifest = _read_phase3_manifest()
    entry = manifest.get("artifacts", {}).get(name)
    if entry is None:
        return {"passed": False, "reason": f"no locked entry for artifact '{name}'"}
    path = PHASE3_DIR / entry["file"]
    if not path.exists():
        return {"passed": False, "reason": f"artifact file {path} missing"}
    current = sha256_file(path)
    return {"passed": bool(current == entry["sha256"]),
            "artifact": name, "locked_hash": entry["sha256"], "current_hash": current,
            "locked_at": entry["locked_at"], "row_count": entry["row_count"],
            "case_count": entry["case_count"]}


def verify_all_phase3():
    """Verify every artifact in the Phase 3 manifest. Phase 3 Step 2 and Phase 9
    call this and abort on any failure before touching a frozen file."""
    if not PHASE3_MANIFEST.exists():
        return {"passed": False, "reason": "phase3_eval_lock.json not found", "results": {}}
    manifest = _read_phase3_manifest()
    results = {name: verify_phase3_artifact(name) for name in sorted(manifest.get("artifacts", {}))}
    return {"passed": all(r["passed"] for r in results.values()) and len(results) > 0,
            "n_artifacts": len(results), "results": results}