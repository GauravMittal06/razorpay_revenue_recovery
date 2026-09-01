"""
Phase 2 -- single standalone, complete verification script.

Run from the folder that CONTAINS backend/:

    python phase2_verify.py

This is the ONLY verification script needed for Phase 2. It supersedes
test_data_factory.py and phase2_gate_report.py (delete those if they're
still around -- this file replaces both and depends on neither).

It does NOT import or subprocess-invoke any other test/verification
script. It DOES call backend/'s own actual modules directly
(backend.db.db, backend.data.generate_seed_data,
backend.ml.simulate_training_data, backend.data_factory.*) -- those are
the SYSTEM being verified, the same way a web app's test suite calls the
app's own code; that is not "another script" in the sense of a second
test harness. Exactly ONE external subprocess is used anywhere in this
file (Section 2, invoking `python -m backend.data_factory.run_generation`
twice) -- specifically because in-process reproducibility can't prove
CROSS-PROCESS reproducibility; every other check runs in-process, by
direct function call, inside this one file.

Env vars:
  DF_N_CASES=3000        primary generation size (default 3000, matches
                          the formal gate's own default)
  SKIP_VENV_INSTALL=1    skip the fresh-venv pip-install step (slow,
                          needs network) -- set this if already verified

Exit code 0 = every check in every section passed (GREEN LIGHT).
Exit code 1 = at least one check failed -- see the FINAL VERDICT section
for the complete, itemized list of exactly which ones.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import venv
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
DF_DIR = BACKEND / "data_factory"
DB_PATH = BACKEND / "db" / "recovery.db"
sys.path.insert(0, str(ROOT))

N_CASES = int(os.environ.get("DF_N_CASES", "3000"))
SEED = 42

MASTER = []  # (section, label, passed: bool, detail: str) -- the ONE list
             # the final verdict is built from. Every check anywhere in
             # this file, in every section, appends here via record().


def record(section, label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    line = f"  [{status}] {label}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    MASTER.append((section, label, bool(passed), str(detail)))
    return passed


def H1(title):
    print("\n" + "#" * 92)
    print(f"# {title}")
    print("#" * 92)


def H2(title):
    print("\n" + "-" * 92)
    print(f"-- {title}")
    print("-" * 92)


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else "N/A"


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_subprocess(cmd, cwd=ROOT, timeout=None, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                        env={**os.environ, **(env or {})})
    return r, time.time() - t0


# ===========================================================================
# SECTION 1 -- FRESH REBUILD
# ===========================================================================

def section_1_fresh_rebuild():
    H1("SECTION 1 -- Fresh rebuild")

    H2("1a. Fresh venv, pip install -r requirements.txt")
    if os.environ.get("SKIP_VENV_INSTALL") == "1":
        print("  SKIPPED (SKIP_VENV_INSTALL=1 set).")
    else:
        venv_dir = ROOT / "_phase2_verify_venv"
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        venv.create(venv_dir, with_pip=True)
        pip_bin = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
        try:
            r, elapsed = run_subprocess([str(pip_bin), "install", "-r", str(BACKEND / "requirements.txt")],
                                         timeout=600)
            record("1a", "pip install -r requirements.txt exits 0 in a fresh venv", r.returncode == 0,
                   f"exit_code={r.returncode} elapsed={elapsed:.1f}s")
            warn_lines = [ln for ln in (r.stdout + r.stderr).splitlines() if "warn" in ln.lower()]
            print(f"  Lines containing 'warn': {len(warn_lines)}")
            for ln in warn_lines[:10]:
                print(f"    {ln}")
        except subprocess.TimeoutExpired:
            record("1a", "pip install -r requirements.txt exits 0 in a fresh venv", False,
                   "TIMED OUT after 600s")
        finally:
            shutil.rmtree(venv_dir, ignore_errors=True)

    H2("1b. Wipe all generated files, rebuild everything from scratch")
    targets = [
        BACKEND / "data" / "merchants.json", BACKEND / "data" / "customers.json",
        BACKEND / "data" / "opportunities.json", BACKEND / "data" / "payments.json",
        DB_PATH, BACKEND / "ml" / "data" / "training_corpus.csv",
        DF_DIR / "output", DF_DIR / "registry",
    ]
    for t in targets:
        if t.exists():
            shutil.rmtree(t) if t.is_dir() else t.unlink()
            print(f"  Wiped: {t}")
        else:
            print(f"  (already absent): {t}")

    from backend.data import generate_seed_data
    from backend.db import db as db_module
    from backend.ml import simulate_training_data as sim_mod

    try:
        generate_seed_data.main()
        record("1b", "backend.data.generate_seed_data.main() completes without raising", True)
    except Exception as e:
        record("1b", "backend.data.generate_seed_data.main() completes without raising", False, repr(e))

    try:
        db_module.main()
        record("1b", "backend.db.db.main() completes without raising, DB file created",
               DB_PATH.exists())
    except Exception as e:
        record("1b", "backend.db.db.main() completes without raising, DB file created", False, repr(e))

    try:
        corpus_df = sim_mod.generate_corpus()
        os.makedirs(sim_mod.DATA_DIR, exist_ok=True)
        corpus_out = sim_mod.DATA_DIR / "training_corpus.csv"
        corpus_df.to_csv(corpus_out, index=False)
        record("1b", "backend.ml.simulate_training_data regenerates the ML training corpus",
               corpus_out.exists(), f"{len(corpus_df)} rows -> {corpus_out}")
    except Exception as e:
        record("1b", "backend.ml.simulate_training_data regenerates the ML training corpus", False, repr(e))

    H2(f"1c. Run Data Factory generation IN-PROCESS (backend.data_factory.run_generation.main(), "
       f"DF_N_CASES={N_CASES})")
    from backend.data_factory import run_generation as rg
    rg.results.clear()  # module-level list -- clear before this run so it reflects only this call
    os.environ["DF_N_CASES"] = str(N_CASES)
    rg_exit_code = rg.main()
    record("1c", "backend.data_factory.run_generation.main() returns exit code 0",
           rg_exit_code == 0, f"returned {rg_exit_code}, {len(rg.results)} internal checks ran")

    # This IS the formal internal gate rollup -- captured directly from the module's own
    # results list, not re-implemented or paraphrased. Folded into the master verdict as-is,
    # under its own section tag so it's traceable back to run_generation.py specifically.
    for label, ok in rg.results:
        record("1c-internal", label, ok)

    return rg_exit_code == 0


# ===========================================================================
# SECTION 2 -- REPRODUCIBILITY (in-process AND cross-process)
# ===========================================================================

def section_2_reproducibility():
    H1("SECTION 2 -- Reproducibility (in-process AND cross-process)")
    from backend.data_factory import candidate_outcome_dataset as cod
    from backend.data_factory import calibration_profiles as cp

    profile = cp.get_profile("baseline")

    H2("2a. In-process: generate twice in the SAME Python session, hash-compare")
    df1, truth1, *_ = cod.generate_dataset(profile, SEED, n_cases=min(N_CASES, 1000))
    df2, truth2, *_ = cod.generate_dataset(profile, SEED, n_cases=min(N_CASES, 1000))
    tmp1, tmp2 = ROOT / "_pv_inprocess_1.csv", ROOT / "_pv_inprocess_2.csv"
    df1.to_csv(tmp1, index=False)
    df2.to_csv(tmp2, index=False)
    md5_1, sha_1 = md5_of_file(tmp1), sha256_of_file(tmp1)
    md5_2, sha_2 = md5_of_file(tmp2), sha256_of_file(tmp2)
    print(f"  Run 1 -- md5={md5_1}  sha256={sha_1}")
    print(f"  Run 2 -- md5={md5_2}  sha256={sha_2}")
    record("2a", "In-process double-generation is byte-identical (md5+sha256 match)",
           md5_1 == md5_2 and sha_1 == sha_2)
    record("2a", "In-process double-generation is pandas-equals identical", df1.equals(df2))
    tmp1.unlink()
    tmp2.unlink()

    H2("2b. Cross-process: TWO SEPARATE subprocess invocations of run_generation.py "
       "(the one external-process check in this file)")
    baseline_csv = DF_DIR / "output" / f"joint_baseline_seed{SEED}.csv"
    r1, _ = run_subprocess([sys.executable, "-m", "backend.data_factory.run_generation"], timeout=300,
                            env={"DF_N_CASES": str(min(N_CASES, 1000))})
    hash1, md5a = (sha256_of_file(baseline_csv), md5_of_file(baseline_csv)) if baseline_csv.exists() else (None, None)
    r2, _ = run_subprocess([sys.executable, "-m", "backend.data_factory.run_generation"], timeout=300,
                            env={"DF_N_CASES": str(min(N_CASES, 1000))})
    hash2, md5b = (sha256_of_file(baseline_csv), md5_of_file(baseline_csv)) if baseline_csv.exists() else (None, None)
    print(f"  Subprocess run 1 exit={r1.returncode} -- md5={md5a}  sha256={hash1}")
    print(f"  Subprocess run 2 exit={r2.returncode} -- md5={md5b}  sha256={hash2}")
    record("2b", "Cross-process double-generation is byte-identical (md5+sha256 match)",
           hash1 == hash2 and md5a == md5b and hash1 is not None)


# ===========================================================================
# SECTION 3 -- LOCKED THRESHOLDS + EVAL-SET LOCK
# ===========================================================================

def section_3_locked_thresholds():
    H1("SECTION 3 -- Locked-tolerance artifacts")
    thresholds_path = DF_DIR / "locked_thresholds.json"
    manifest_path = DF_DIR / "output" / "eval_set_manifest.json"

    H2("3a. Full contents of locked_thresholds.json")
    with open(thresholds_path) as f:
        thresholds_text = f.read()
        thresholds = json.loads(thresholds_text)
    print(thresholds_text)
    record("3a", "locked_thresholds.json parses as valid JSON", True)

    H2("3b. Filesystem mtime + declared internal lock timestamps")
    mtime = os.path.getmtime(thresholds_path)
    print(f"  Filesystem mtime: {iso(mtime)}")
    print("  No git repo in this build environment -- mtime is the substitute record; use "
          "`git log -1 --format=%cI -- backend/data_factory/locked_thresholds.json` once this is "
          "under version control.")
    print(f"  top-level locked_at_utc: {thresholds.get('locked_at_utc')}")
    for key in ("fatigue_significance", "candidate_timing_validity", "eval_set_lock",
                "ground_truth_treatment_effect"):
        block = thresholds.get(key, {})
        for tskey in ("_locked_at_utc", "_amendment_locked_at_utc"):
            if tskey in block:
                print(f"  {key}.{tskey}: {block[tskey]}")

    H2("3c. eval_set_manifest.json -- mtime + contents")
    if not manifest_path.exists():
        record("3c", "eval_set_manifest.json exists", False, "not found")
        manifest = None
    else:
        record("3c", "eval_set_manifest.json exists", True)
        manifest_mtime = os.path.getmtime(manifest_path)
        print(f"  Filesystem mtime: {iso(manifest_mtime)}")
        with open(manifest_path) as f:
            manifest = json.load(f)
        print(json.dumps(manifest, indent=2, sort_keys=True, default=str))

    H2("3d. Timestamp ordering -- thresholds lock vs. eval-set generation vs. Phase 3 start")
    thresholds_locked_at = thresholds.get("eval_set_lock", {}).get("_locked_at_utc") or thresholds.get("locked_at_utc")
    eval_locked_at = manifest.get("baseline", {}).get("locked_at") if manifest else None
    print(f"  1. locked_thresholds.json eval_set_lock locked at: {thresholds_locked_at}")
    print(f"  2. eval_set_manifest.json 'baseline' locked at:    {iso(eval_locked_at) if eval_locked_at else 'N/A'}")
    print(f"  3. Phase 3 work started at:                         N/A -- does not exist yet in this codebase.")
    if thresholds_locked_at and eval_locked_at:
        t_dt = datetime.strptime(thresholds_locked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        e_dt = datetime.fromtimestamp(eval_locked_at, tz=timezone.utc)
        record("3d", "Thresholds lock timestamp predates eval-set generation timestamp", t_dt < e_dt,
               f"{t_dt.isoformat()} < {e_dt.isoformat()}")

    H2("3e. Eval-set manifest hash vs. freshly recomputed hash of the actual holdout file")
    if manifest is None:
        record("3e", "Eval-set hash re-verification", False, "no manifest")
    else:
        entry = manifest.get("baseline", {})
        holdout_path = DF_DIR / "output" / entry.get("holdout_file", "")
        stored_hash = entry.get("sha256")
        print(f"  Manifest's stored sha256:     {stored_hash}")
        if holdout_path.exists():
            recomputed = sha256_of_file(holdout_path)
            print(f"  Recomputed sha256, right now: {recomputed}")
            record("3e", "Eval-set manifest hash matches freshly recomputed hash", stored_hash == recomputed)
        else:
            record("3e", "Eval-set manifest hash matches freshly recomputed hash", False,
                   f"holdout file {holdout_path} missing")


# ===========================================================================
# SECTION 4 -- FATIGUE STATISTIC
# ===========================================================================

def section_4_fatigue(baseline_joint):
    H1("SECTION 4 -- Fatigue statistic")
    from backend.data_factory import validators as val
    thresholds = val.load_thresholds()
    cfg = thresholds["fatigue_significance"]

    H2("4a. Locked config (as locked BEFORE this run)")
    print(f"  Statistic: Pearson correlation, prior_contacts_in_window vs. recovered")
    print(f"  Population: every candidate row where candidate_action != 'do_nothing'")
    print(f"  Locked expected sign: {cfg['expected_sign']}   alpha: {cfg['alpha']}   min_n: {cfg['min_n']}")
    print(f"  Locked at: {cfg['_locked_at_utc']}")

    H2("4b. Actual computed r, p, n -- fresh, against the freshly rebuilt dataset")
    result = val.fatigue_significance_check(baseline_joint, thresholds)
    print(json.dumps(result, indent=2, default=str))
    record("4b", "Fatigue: sign matches locked expectation AND statistically significant at locked alpha",
           result["passed"], f"r={result.get('correlation')} p={result.get('p_value')} n={result.get('n')}")


# ===========================================================================
# SECTION 5 -- VALIDATOR ROBUSTNESS (7 corruption self-tests)
# ===========================================================================

def section_5_validator_robustness(baseline_joint, baseline_truth):
    H1("SECTION 5 -- Validator robustness (7 corruption self-tests)")
    from backend.data_factory import validators as val
    thresholds = val.load_thresholds()

    tests = [
        ("Case-level leakage", "Force one case's rows into BOTH train/test partitions",
         lambda: val.leakage_case_level_robustness_self_test(baseline_joint, thresholds)),
        ("Customer-level leakage", "Force one customer's rows into BOTH train/test partitions",
         lambda: val.leakage_customer_level_robustness_self_test(baseline_joint, thresholds)),
        ("Temporal-order leakage", "Set one row's hours_since_last_action to -5.0",
         lambda: val.leakage_temporal_order_robustness_self_test(baseline_joint, thresholds)),
        ("Distributional sanity", "Set one row's amount to 100x the locked maximum",
         lambda: val.distributional_sanity_robustness_self_test(baseline_joint, thresholds)),
        ("Candidate timing validity", "Set one row's candidate_timing to '9d' (not a valid bucket)",
         lambda: val.candidate_timing_validity_robustness_self_test(baseline_joint, thresholds)),
        ("Hidden-state-once-per-case", "Resample hidden_* columns independently per row",
         lambda: val.validator_robustness_self_test(baseline_truth)),
    ]
    for name, corruption, fn in tests:
        H2(name)
        print(f"  Corruption: {corruption}")
        result = fn()
        print(json.dumps(result, indent=2, default=str))
        record("5", f"{name}: clean data passes AND corrupted data correctly flagged as failing",
               result["passed"])

    H2("Ground-truth check corruption self-test (7th, narrower claim)")
    result = val.ground_truth_check_robustness_self_test(baseline_joint, baseline_truth, thresholds)
    print(f"  Corruption: recovered_amount randomly permuted across all rows")
    print(json.dumps(result, indent=2, default=str))
    record("5", "Ground-truth check: corrupted data measurably worse than clean data", result["passed"])


# ===========================================================================
# SECTION 6 -- GROUND-TRUTH TREATMENT-EFFECT CHECK
# ===========================================================================

def section_6_ground_truth(baseline_joint, baseline_truth):
    H1("SECTION 6 -- Ground-truth treatment-effect check")
    from backend.data_factory import validators as val
    from backend.data_factory import candidate_outcome_dataset as cod
    from backend.data_factory import calibration_profiles as cp
    thresholds = val.load_thresholds()

    H2("6a. Full per-bucket breakdown, freshly rebuilt dataset")
    result = val.ground_truth_treatment_effect_check(baseline_joint, baseline_truth, thresholds)
    print(f"  passed={result['passed']}  fraction_buckets_matching_direction="
          f"{result['fraction_buckets_matching_direction']:.4f}  "
          f"all_gaps_within_tolerance={result['all_gaps_within_tolerance']}  "
          f"buckets_evaluated={result['buckets_evaluated']}  "
          f"buckets_skipped_insufficient_data={result['buckets_skipped_insufficient_data']}  "
          f"buckets_direction_scored={result['buckets_direction_scored']}")
    for b in result["per_bucket"]:
        if b["skipped"]:
            print(f"    [SKIPPED -- NOT ENOUGH DATA] {b['bucket_key']:55s} n={b['n_cases']:5d}")
        elif not b["direction_scored"]:
            print(f"    [GAP-TESTED, DIRECTION NOT SCORED] {b['bucket_key']:55s} n={b['n_cases']:5d} "
                  f"empirical={b['empirical_effect']:+.4f} analytic={b['analytic_effect']:+.4f} "
                  f"gap={b['gap']:.4f}")
        else:
            print(f"    [EVALUATED] {b['bucket_key']:55s} n={b['n_cases']:5d} "
                  f"empirical={b['empirical_effect']:+.4f} analytic={b['analytic_effect']:+.4f} "
                  f"gap={b['gap']:.4f} direction_match={b['direction_match']}")
    record("6a", "Ground-truth check passes on the primary freshly-rebuilt dataset (formal gate, "
                 "n=" + str(N_CASES) + ")", result["passed"])

    H2("6b. Robustness sweep across sample sizes -- EXPLORATORY, NOT part of the formal gate "
       "(informational only, does not affect the final verdict below)")
    profile = cp.get_profile("baseline")
    for n in (800, 3000, 6000):
        jdf, tdf, *_ = cod.generate_dataset(profile, SEED, n_cases=n)
        r = val.ground_truth_treatment_effect_check(jdf, tdf, thresholds)
        retry_buckets = [b for b in r["per_bucket"] if b["action_type"] == "retry"]
        print(f"  n={n}: passed={r['passed']} frac_matching={r['fraction_buckets_matching_direction']:.4f} "
              f"all_gaps_ok={r['all_gaps_within_tolerance']}")
        for b in retry_buckets:
            tag = "SKIPPED" if b["skipped"] else ("NOT SCORED" if not b["direction_scored"] else
                                                    f"direction_match={b['direction_match']}")
            print(f"      {b['bucket_key']}: n={b['n_cases']} {tag}")


# ===========================================================================
# SECTION 7 -- CUMULATIVE REGRESSION
# ===========================================================================

def section_7_cumulative_regression():
    H1("SECTION 7 -- Cumulative regression")

    H2("Re-checked THIS run")
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    record("7", "recovery_actions fully retired", "recovery_actions" not in tables)
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    record("7", "Foreign key check clean on fresh DB", len(fk) == 0, f"{len(fk)} violations")
    n_opps = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    record("7", "Opportunities loaded (150 expected)", n_opps == 150, f"n={n_opps}")
    dr_count = conn.execute("SELECT COUNT(*) FROM dataset_registry").fetchone()[0]
    record("7", "dataset_registry populated by this run", dr_count >= 2, f"n={dr_count}")
    idx_names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    record("7", "UNIQUE index on opportunities.ingestion_event_id present",
           "idx_opportunities_ingestion_event_id" in idx_names)
    record("7", "UNIQUE index on recovery_decisions.candidate_id present",
           "idx_recovery_decisions_candidate_id" in idx_names)
    record("7", "UNIQUE index on recovery_executions.decision_id present",
           "idx_recovery_executions_decision_id" in idx_names)
    conn.close()
    corpus_path = BACKEND / "ml" / "data" / "training_corpus.csv"
    record("7", "ML training corpus regenerates from the existing generator", corpus_path.exists())

    H2("COULD NOT be re-checked this run (engine/, api/, llm/, .joblib model artifacts not provided)")
    not_checked = [
        "Import hygiene across engine/*.py and api/*.py",
        "API server starts; /api/metrics and one read endpoint return 200",
        "Shipped lr_model.joblib / xgb_model.joblib load with zero compatibility warnings",
        "Batch pipeline (core_loop.run_cycle) runs end-to-end",
        "Duplicate-event idempotency at the application layer (trigger_event.py)",
        "experiment_assignment concurrent-write safety at the application layer",
        "Full test_everything.py 19-check suite",
    ]
    for label in not_checked:
        print(f"  [NOT CHECKED] {label}  -- required files not part of this session's inputs")
    print(f"\n  These {len(not_checked)} items are NOT counted as failures in the final verdict below "
          f"(there is nothing to run), and are NOT counted as passes either -- they are absent from "
          f"the master tally entirely, listed here so the gap is visible rather than silently assumed clear.")


# ===========================================================================
# SECTION 8 -- DISTRIBUTIONAL / RELATIONSHIP SANITY
# ===========================================================================

def section_8_distributional(baseline_joint):
    H1("SECTION 8 -- Distributional / relationship sanity")
    from backend.data_factory import validators as val
    thresholds = val.load_thresholds()

    H2("8a. Basic distributional stats")
    print("  amount.describe():")
    print(baseline_joint["amount"].describe().to_string())
    print("\n  current_method value_counts (per case):")
    print(baseline_joint.drop_duplicates("case_id")["current_method"].value_counts().to_string())
    print("\n  event_type value_counts (per case):")
    print(baseline_joint.drop_duplicates("case_id")["event_type"].value_counts().to_string())
    print("\n  root_cause value_counts (per case, payment_failed only):")
    print(baseline_joint.drop_duplicates("case_id")["root_cause"].value_counts(dropna=True).to_string())

    r = val.distributional_sanity_check(baseline_joint, thresholds)
    record("8a", "Amounts/health/methods within valid ranges", r["passed"], json.dumps(r, default=str))

    H2("8b. Directional check -- lower bank health vs. higher simulated technical-failure rate")
    r = val.directional_relationship_check(baseline_joint, thresholds)
    print(json.dumps(r, indent=2, default=str))
    record("8b", "Network health positively correlates with recovery for technical retries", r["passed"])


# ===========================================================================
# SECTION 9 -- CALIBRATION PROFILE DIFFERENCE
# ===========================================================================

def section_9_calibration_profiles():
    H1("SECTION 9 -- Calibration profile difference")
    from backend.data_factory import candidate_outcome_dataset as cod
    from backend.data_factory import calibration_profiles as cp
    from backend.data_factory import validators as val
    thresholds = val.load_thresholds()

    baseline_profile = cp.get_profile("baseline")
    stress_profile = cp.get_profile("stress")
    b_joint, b_truth, b_world, b_health_idx, b_health_obs = cod.generate_dataset(
        baseline_profile, SEED, n_cases=min(N_CASES, 1500))
    s_joint, s_truth, s_world, s_health_idx, s_health_obs = cod.generate_dataset(
        stress_profile, SEED, n_cases=min(N_CASES, 1500))
    b_health_df = pd.DataFrame([o.__dict__ for o in b_health_obs])
    s_health_df = pd.DataFrame([o.__dict__ for o in s_health_obs])

    print(f"  {'Statistic':40s} {'baseline':>14s} {'stress':>14s} {'rel. diff':>12s}")
    stats = [
        ("mean bank health_score", b_health_df["health_score"].mean(), s_health_df["health_score"].mean()),
        ("overall recovery rate", b_joint["recovered"].mean(), s_joint["recovered"].mean()),
        ("do_nothing recovery rate", b_joint[b_joint["candidate_action"] == "do_nothing"]["recovered"].mean(),
         s_joint[s_joint["candidate_action"] == "do_nothing"]["recovered"].mean()),
    ]
    for label, bval, sval in stats:
        rel_diff = abs(bval - sval) / bval if bval else float("nan")
        print(f"  {label:40s} {bval:14.4f} {sval:14.4f} {rel_diff:12.4f}")

    r = val.calibration_profile_divergence_check(b_health_df, s_health_df, b_joint, s_joint, thresholds)
    record("9", "Baseline and stress profiles differ materially (not just by seed)", r["passed"], json.dumps(r, default=str))


# ===========================================================================

def main():
    t0 = time.time()

    ok1 = section_1_fresh_rebuild()

    from backend.data_factory import candidate_outcome_dataset as cod
    from backend.data_factory import calibration_profiles as cp
    profile = cp.get_profile("baseline")
    baseline_joint, baseline_truth, *_ = cod.generate_dataset(profile, SEED, n_cases=N_CASES)

    section_2_reproducibility()
    section_3_locked_thresholds()
    section_4_fatigue(baseline_joint)
    section_5_validator_robustness(baseline_joint, baseline_truth)
    section_6_ground_truth(baseline_joint, baseline_truth)
    section_7_cumulative_regression()
    section_8_distributional(baseline_joint)
    section_9_calibration_profiles()

    H1("FINAL VERDICT")
    by_section = {}
    for section, label, passed, detail in MASTER:
        by_section.setdefault(section, []).append((label, passed, detail))

    total = len(MASTER)
    total_passed = sum(1 for _, _, p, _ in MASTER if p)
    failed = [(s, l, d) for s, l, p, d in MASTER if not p]

    print(f"\n{total_passed}/{total} checks passed across all sections.\n")
    for section in sorted(by_section):
        section_total = len(by_section[section])
        section_passed = sum(1 for _, p, _ in by_section[section] if p)
        print(f"  Section {section:15s}: {section_passed}/{section_total}")

    if failed:
        print("\nFAILED CHECKS (exact list, nothing summarized away):")
        for section, label, detail in failed:
            print(f"  [{section}] {label}" + (f"  -- {detail}" if detail else ""))
        print(f"\nTotal wall-clock time: {time.time() - t0:.1f}s")
        print("\n" + "=" * 92)
        print("RESULT: NOT CLEAR -- see FAILED CHECKS above")
        print("=" * 92)
        return 1

    print(f"\nTotal wall-clock time: {time.time() - t0:.1f}s")
    print("\n" + "=" * 92)
    print("RESULT: GREEN LIGHT -- every check in every section passed")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())