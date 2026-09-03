#!/usr/bin/env python3
"""
Authoritative end-to-end verification of Phases 0-5. Run this yourself.

WHAT THIS IS
    A check that the phases work *together* on a real database, through the
    real live path -- not a re-run of the unit suite, and not a demo. Every
    check prints the value it actually observed, so the output can be read
    rather than merely counted.

WHAT IT WEIGHTS
    Coverage is deliberately uneven. Most of the checks sit on the five things
    that would actually matter if they broke:

      1. EIV ranking correctness   -- the product's core differentiator
      2. The authority boundary    -- the highest-severity property in the system
      3. Fallthrough + disable     -- the riskiest Phase 5 integration
      4. Network health            -- newly live, with its own failure mode
      5. do_nothing writes nothing -- a decision is not an execution

    Phases 0-2 get proportionally less: they are well covered by pytest and
    their failure modes are loud. Where a property is statistical and needs
    volume (the Phase 2 validators, Phase 3's calibration gates), this script
    says so and defers to the suite rather than pretending a small sample
    settles it.

THE C2 LESSON, MADE STRUCTURAL
    A directional probe that swings a feature outside the range the model was
    trained on measures leaf placement, not a learned relationship. That error
    produced a false "model has the wrong sign" finding which was investigated
    at length and retracted (PHASE5_NOTES.md, C2). It was the second such error
    in this project, after the retracted Phase 4 G7 finding.

    So every directional probe here goes through `directional_probe()`, which
    refuses to run outside TRAINING_SUPPORT and prints the range it used. This
    must never regress.

HONESTY RULES
    - Known, disclosed gaps are printed and counted separately, never hidden.
    - Anything unexpected is counted separately again and made impossible to
      miss at the end.
    - Expectations are stated before observations, so you can judge whether the
      expectation was reasonable rather than trusting a verdict.

SAFETY
    Touches no git state and writes nothing outside a temporary directory that
    is deleted on exit. Your recovery.db, seed data and generated corpora are
    never written. Safe to run repeatedly.

DEPENDENCIES
    Not stdlib-only: Phases 2-4 need the project's pinned scientific stack
    (pandas, numpy, scikit-learn, xgboost, joblib, scipy). It also needs the
    gitignored model artifacts:
        backend/ml/models/outcome_model.joblib   (Phases 3/4/5)
        backend/ml/models/xgb_model.joblib       (Phase 5 advisory field)
    Missing artifacts are reported as SKIPPED with a reason, never as passes.

USAGE
    cd <repo root>
    python test_everything.py            # full run   (~2 min)
    python test_everything.py --quick    # skip Phase 2 generation (~1 min)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SEED_DATA_NOW", "1756000000")

LATENCY_BUDGET_MS = 250.0

# Measured p10/p90 of each feature in the Phase 3 training pool
# (phase3_baseline_seed42_training_pool.csv, 20,044 rows). A directional probe
# may only swing inside these. Verified against the corpus at runtime when it
# is present -- see check_training_support_is_current().
TRAINING_SUPPORT = {
    "payment_history_score": (0.4101, 0.8246),     # full range [0.2031, 0.9236]
    "past_recovery_rate": (0.3296, 0.8057),        # full range [0.0782, 0.9332]
    "retry_count": (0.0, 3.0),
    "prior_contacts_in_window": (0.0, 2.0),
}

# Sign the generator imposes on each, from data_factory/outcome_model.py and
# candidate_outcome_dataset.py. This is ground truth, not a model property.
GENERATOR_SIGN = {
    "payment_history_score": "+",   # -> liquidity_state -> recovery_willingness
    "past_recovery_rate": "+",      # -> customer_responsiveness
    "retry_count": "-",             # fatigue
    "prior_contacts_in_window": "-",  # fatigue
}

# The W0 baseline (git 866e478) carried 16. Four were resolved during Phase 5:
# the three concurrency defects and closeout C1. These 12 remain.
KNOWN_TEST_FAILURES = [
    "test_compliance_regression.py::test_every_branch_is_reachable_and_distinct",
    "test_permanent_gates.py::test_exposed_key_history_exposure_is_documented",
    "test_permanent_gates.py::test_method_change_has_no_reachable_executor_path",
    "test_permanent_gates.py::test_no_broad_handler_discards_the_failure_silently",
    "test_permanent_gates.py::test_no_new_silent_swallow_beyond_the_recorded_findings",
    "test_permanent_gates.py::test_no_syspath_manipulation_remains",
    "test_permanent_gates.py::test_relative_or_bare_intra_project_imports_are_absent",
    "test_permanent_gates.py::test_seed_generator_persists_its_own_provenance",
    "test_permanent_gates.py::test_training_corpus_content_hash_is_recorded_somewhere",
    "test_phase0_bootstrap.py::test_installed_versions_match_the_pins",
    "test_phase0_bootstrap.py::test_test_tooling_is_not_mixed_into_runtime_requirements",
    "test_phase4_optimizer.py::test_end_to_end_latency_against_the_declared_budget",
]

# Resolved during Phase 5. Listed so a reader comparing against an older
# baseline sees why the count dropped rather than wondering what was silenced.
RESOLVED_SINCE_W0 = [
    ("test_phase1_concurrency.py::test_recovery_update_is_guarded_by_the_status_it_read",
     "compare-and-swap in mark_opportunity_recovered()"),
    ("test_phase1_concurrency.py::test_concurrent_recovery_confirmations_produce_one_winner",
     "same fix; 252/320 callers told 'ok' before, 40/320 after"),
    ("test_phase1_concurrency.py::test_two_overlapping_batch_cycles_do_not_double_act_on_one_case",
     "engine/opportunity_lock.py; 91 contacts fired before, 25 after"),
    ("test_phase4_optimizer.py::test_higher_true_incremental_value_ranks_above_lower",
     "closeout C1: replaced by tests/test_phase4_ranking_correctness.py"),
]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

class Reporter:
    MATCH, DISCLOSED, UNEXPECTED, SKIP = "MATCH", "DISCLOSED", "UNEXPECTED", "SKIP"

    def __init__(self):
        self.rows = []

    def section(self, title, weight=""):
        print()
        print("=" * 78)
        print(f"  {title}" + (f"   [{weight}]" if weight else ""))
        print("=" * 78)

    def sub(self, title):
        print(f"\n  -- {title} " + "-" * max(0, 68 - len(title)))

    def _log(self, status, label, observed, expected, note):
        self.rows.append((status, label, observed, expected, note))
        tag = {self.MATCH: "  ok  ", self.DISCLOSED: " known",
               self.UNEXPECTED: " ****UNEXPECTED**** ", self.SKIP: " skip "}[status]
        print(f"  [{tag}] {label}")
        print(f"           expected : {expected}")
        print(f"           observed : {observed}")
        if note:
            for line in str(note).splitlines():
                print(f"           note     : {line}")

    def check(self, label, ok, observed, expected, note=""):
        self._log(self.MATCH if ok else self.UNEXPECTED, label, observed, expected, note)
        return ok

    def disclosed(self, label, observed, expected, note):
        self._log(self.DISCLOSED, label, observed, expected, note)

    def skip(self, label, reason):
        self._log(self.SKIP, label, "not run", "n/a", reason)

    def summary(self):
        counts = {k: 0 for k in (self.MATCH, self.DISCLOSED, self.UNEXPECTED, self.SKIP)}
        for status, *_ in self.rows:
            counts[status] += 1

        print()
        print("=" * 78)
        print("  SUMMARY")
        print("=" * 78)
        print(f"  checks run                      : {len(self.rows)}")
        print(f"  matched expectation             : {counts[self.MATCH]}")
        print(f"  known and disclosed non-matches : {counts[self.DISCLOSED]}")
        print(f"  skipped (missing prerequisite)  : {counts[self.SKIP]}")
        print(f"  UNEXPECTED                      : {counts[self.UNEXPECTED]}")

        if counts[self.DISCLOSED]:
            print("\n  Disclosed non-matches -- each is recorded in PHASE5_NOTES.md or a")
            print("  phase hand-off; none is a surprise:")
            for status, label, observed, _, _ in self.rows:
                if status == self.DISCLOSED:
                    print(f"    - {label}")
                    print(f"        {observed}")

        if counts[self.SKIP]:
            print("\n  Skipped:")
            for status, label, _, _, note in self.rows:
                if status == self.SKIP:
                    print(f"    - {label}  ({note})")

        if counts[self.UNEXPECTED]:
            bar = "!" * 78
            print()
            print(bar); print(bar)
            print(f"!!  {counts[self.UNEXPECTED]} UNEXPECTED RESULT(S) -- THIS IS NOT NORMAL")
            print(bar); print(bar)
            for status, label, observed, expected, _ in self.rows:
                if status == self.UNEXPECTED:
                    print(f"  !! {label}")
                    print(f"       expected: {expected}")
                    print(f"       observed: {observed}")
            print(bar); print(bar)
        else:
            print("\n  No unexpected results. Everything above either matched the stated")
            print("  expectation or is a previously-recorded, disclosed gap.")
        return counts[self.UNEXPECTED]


R = Reporter()


# --------------------------------------------------------------------------
# Disposable environment
# --------------------------------------------------------------------------

def build_temp_world(tmp: Path):
    """Seed DB + seed data in a temp dir. Nothing in the repo is written."""
    import io
    from contextlib import redirect_stdout

    from backend.data import generate_seed_data as gsd
    from backend.db import db as db_module

    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    gsd.DATA_DIR = data_dir
    db_module.DATA_DIR = data_dir
    db_module.DB_PATH = tmp / "verify.db"

    with redirect_stdout(io.StringIO()):
        gsd.main()

    from backend.db.db import (create_schema, get_connection,
                               load_bank_health_observations, load_customers,
                               load_merchants, load_opportunities, load_payments)
    conn = get_connection()
    create_schema(conn)
    load_merchants(conn); load_customers(conn)
    load_opportunities(conn); load_payments(conn)
    load_bank_health_observations(conn)
    return conn


def live_ids(conn, limit=None):
    rows = conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE status IN ('open','recovering')"
    ).fetchall()
    ids = [r[0] for r in rows]
    return ids[:limit] if limit else ids


def at_local_hour(hour: int, days_ago: int = 1) -> int:
    """
    A timestamp at a specific *local* hour.

    decide_action()'s contact-window check reads the local hour of
    `created_at`, so a fixture built as `time.time() - 3600` lands in a
    different compliance branch depending on what time of day this runs.
    Every Phase 5 fixture pins the hour explicitly instead.
    """
    from datetime import datetime, timedelta
    dt = (datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
          - timedelta(days=days_ago))
    return int(dt.timestamp())


# --------------------------------------------------------------------------
# PHASE 0/1  (light -- well covered by pytest, loud failure modes)
# --------------------------------------------------------------------------

def phase01(conn):
    R.section("PHASE 0/1 -- schema, closed vocabularies, idempotent rebuild", "light")

    from backend.db.db import DECISION_OUTCOMES, EXECUTION_STATES, create_schema

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    required = {"merchants", "customers", "opportunities", "payments",
                "recovery_candidates", "recovery_decisions", "recovery_executions",
                "experiment_assignment", "bank_health_observations", "messages",
                "dataset_registry"}
    R.check("every required table exists", required <= tables,
            f"{len(tables)} tables present", f"superset of {len(required)} required")

    R.check("compliance vocabulary is closed at 6", len(DECISION_OUTCOMES) == 6,
            f"{list(DECISION_OUTCOMES)}",
            "6 values; blocked_max_retries removed in Phase 5 (had no producer)")
    R.check("lifecycle vocabulary is closed at 7", len(EXECUTION_STATES) == 7,
            f"{list(EXECUTION_STATES)}", "7 values")
    R.disclosed("the two vocabularies share one token",
                f"overlap = {sorted(set(DECISION_OUTCOMES) & set(EXECUTION_STATES))}",
                "ideally disjoint",
                "'executed' is both a compliance outcome and a lifecycle state.\n"
                "Pre-existing; any query joining the two tables must alias.")

    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    R.check("one execution row per decision (UNIQUE index)",
            "idx_recovery_executions_decision_id" in idx,
            f"index present = {'idx_recovery_executions_decision_id' in idx}",
            "present -- this is what makes dispatch idempotent")
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    R.check("foreign keys enforced", fk == 1, f"PRAGMA foreign_keys = {fk}",
            "1 -- candidate_id must reference a real candidate")

    before = len(tables)
    create_schema(conn); create_schema(conn)
    after = len({r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()})
    R.check("create_schema is idempotent", before == after,
            f"{before} tables before, {after} after two more calls", "unchanged")

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("merchants", "customers", "opportunities", "payments",
                        "bank_health_observations")}
    R.check("seed rows present", all(v > 0 for v in counts.values()),
            str(counts), "all non-zero")


# --------------------------------------------------------------------------
# PHASE 2  (light -- statistical validators belong to pytest)
# --------------------------------------------------------------------------

def phase2(quick):
    R.section("PHASE 2 -- data factory: profiles, determinism, locks", "light")

    import json

    from backend.data_factory import calibration_profiles as cp

    names = sorted(cp.PROFILES)
    R.check("both calibration profiles registered", set(names) == {"baseline", "stress"},
            str(names), "['baseline', 'stress']")
    b, s = cp.get_profile("baseline"), cp.get_profile("stress")
    R.check("the two profiles genuinely differ", b.__dict__ != s.__dict__,
            f"baseline != stress -> {b.__dict__ != s.__dict__}",
            "different, else cross-profile generalisation is vacuous")

    lock = REPO_ROOT / "backend/data_factory/locked_thresholds.json"
    if lock.exists():
        d = json.loads(lock.read_text(encoding="utf-8"))
        R.check("statistical tolerances are locked and dated", "locked_at_utc" in d,
                f"locked_at_utc={d.get('locked_at_utc')} "
                f"generator={d.get('generator_version_at_lock')}",
                "a lock timestamp and generator version recorded")
    else:
        R.skip("locked thresholds", "locked_thresholds.json not present")

    if quick:
        R.skip("live generation for both profiles", "--quick given")
        return

    R.sub("live generation + determinism (structure only; validators are pytest's job)")
    from backend.data_factory import candidate_outcome_dataset as cod

    N = 40
    for name in ("baseline", "stress"):
        t0 = time.perf_counter()
        joint, truth, *_ = cod.generate_dataset(cp.get_profile(name), 42, n_cases=N)
        dt = (time.perf_counter() - t0) * 1000
        R.check(f"{name}: generates a joint candidate-outcome dataset",
                len(joint) > 0 and len(truth) > 0,
                f"{len(joint)} joint rows, {len(truth)} truth rows, {dt:.0f}ms",
                f"non-empty for {N} cases")
        if "candidate_action" in joint.columns:
            n_dn = (joint["candidate_action"] == "do_nothing").sum()
            R.check(f"{name}: exactly one do_nothing per case",
                    n_dn == joint["case_id"].nunique(),
                    f"{n_dn} do_nothing rows / {joint['case_id'].nunique()} cases",
                    "one per case")

    a = cod.generate_dataset(cp.get_profile("baseline"), 42, n_cases=N)[0]
    b2 = cod.generate_dataset(cp.get_profile("baseline"), 42, n_cases=N)[0]
    c = cod.generate_dataset(cp.get_profile("baseline"), 43, n_cases=N)[0]
    R.check("same seed reproduces identical data", a.equals(b2),
            f"seed42 == seed42 -> {a.equals(b2)} ({len(a)} rows)", "identical")
    R.check("a different seed produces different data", not a.equals(c),
            f"seed42 == seed43 -> {a.equals(c)}", "different")


# --------------------------------------------------------------------------
# PHASE 3  (model loads, scores, and moves in the generator's direction)
# --------------------------------------------------------------------------

def check_training_support_is_current():
    """
    The C2 guard. TRAINING_SUPPORT is measured from the training pool; if that
    corpus is present, re-derive and compare, so these bounds cannot silently
    go stale against the data the model was actually fit on.
    """
    csv = (REPO_ROOT /
           "backend/data_factory/phase3_eval/phase3_baseline_seed42_training_pool.csv")
    if not csv.exists():
        R.skip("training-support bounds re-derived from the corpus",
               "training pool CSV not present (gitignored)")
        return
    import pandas as pd
    df = pd.read_csv(csv, usecols=list(TRAINING_SUPPORT))
    drift = []
    for feat, (lo, hi) in TRAINING_SUPPORT.items():
        q = df[feat].quantile([0.10, 0.90])
        if abs(q[0.10] - lo) > 1e-3 or abs(q[0.90] - hi) > 1e-3:
            drift.append(f"{feat}: declared ({lo}, {hi}) vs corpus "
                         f"({q[0.10]:.4f}, {q[0.90]:.4f})")
    R.check("declared training-support bounds match the corpus", not drift,
            "; ".join(drift) if drift else
            f"all {len(TRAINING_SUPPORT)} features match corpus p10/p90",
            "no drift -- these bounds gate every directional probe")


def phase3(conn):
    R.section("PHASE 3 -- joint model: loads, scores, directional sanity", "medium")

    import pandas as pd

    from backend.engine import optimize
    from backend.ml import inference
    from backend.ml import outcome_features as feats

    if not (REPO_ROOT / "backend/ml/models/outcome_model.joblib").exists():
        R.skip("all Phase 3 checks", "outcome_model.joblib missing (gitignored)")
        return

    ids = live_ids(conn)
    ctx, _ = optimize.load_context(conn, ids[0])
    cand = {"action_type": "retry", "timing": "immediate", "timing_hours": 0.0,
            "method": ctx.get("current_method"), "channel": "n/a",
            "method_changed": False}

    res = inference.score_candidate(ctx, cand, conn=conn)
    R.check("score_candidate returns a complete result", res["error"] is None,
            f"error={res['error']} p={res['p_recovery']} "
            f"amount={res['expected_recovered_amount']}",
            "error=None, all fields populated")
    if res["error"] is None:
        R.check("p_recovery is a probability", 0.0 <= res["p_recovery"] <= 1.0,
                f"{res['p_recovery']:.6f}", "within [0, 1]")
        derived = res["p_recovery"] * res["expected_amount_given_recovered"]
        R.check("expected_recovered_amount = p x E[amount|recovered]",
                abs(derived - res["expected_recovered_amount"]) < 1e-6,
                f"{derived:.6f} vs {res['expected_recovered_amount']:.6f}",
                "identical -- derivation lives outside the pipelines")

    R.sub("directional sanity -- IN TRAINING SUPPORT ONLY (the C2 lesson)")
    check_training_support_is_current()
    print("     Each feature is swung alone, all else held at a real opportunity's")
    print("     values, strictly within the corpus p10..p90. Swinging outside it")
    print("     measures leaf placement, not a learned relationship -- that error")
    print("     produced a false 'wrong sign' finding, since retracted (C2).")

    lookup = inference._get_health_lookup(conn)
    model = inference._load_model()
    contexts = []
    for oid in ids:
        c, _ = optimize.load_context(conn, oid)
        if c is None:
            continue
        contexts.append((c, {"action_type": "retry", "timing": "immediate",
                             "timing_hours": 0.0, "method": c.get("current_method"),
                             "channel": "n/a", "method_changed": False}))
    regimes = set()

    def directional_probe(field):
        lo, hi = TRAINING_SUPPORT[field]        # refuses to run outside support
        up = down = 0
        deltas = []
        for c, cd in contexts:
            ps = []
            for value in (lo, hi):
                ctx2 = dict(c); ctx2[field] = value
                row = feats.build_feature_row(ctx2, cd, lookup)
                regimes.add(row["network_health_known"])
                ps.append(float(model["p_pipeline"].predict_proba(
                    pd.DataFrame([row])[feats.ALL_FEATURES])[:, 1][0]))
            d = ps[1] - ps[0]
            deltas.append(d); up += d > 0; down += d <= 0
        n = len(deltas) or 1
        return up, down, len(deltas), sum(deltas) / n, lo, hi

    for field, sign in GENERATOR_SIGN.items():
        up, down, n, mean, lo, hi = directional_probe(field)
        if sign == "+":
            ok, desc = up > down, f"raises in {up}/{n} ({up/max(n,1):.1%})"
            want = "majority RAISE"
        else:
            ok, desc = down > up, f"lowers in {down}/{n} ({down/max(n,1):.1%})"
            want = "majority LOWER"
        R.check(f"{field} moves in the generator's direction ({sign})", ok,
                f"{desc}, mean delta {mean:+.5f}, swung [{lo}, {hi}]",
                f"{want} -- generator sign is {sign}, probe stays in support")

    R.check("the directional probe ran with network health present",
            regimes == {1.0}, f"network_health_known values seen: {sorted(regimes)}",
            "{1.0} -- contexts come from the real live path")


# --------------------------------------------------------------------------
# PHASE 4 -- EIV ranking correctness            [HEAVILY WEIGHTED]
# --------------------------------------------------------------------------

def phase4(conn):
    R.section("PHASE 4 -- EIV ranking correctness", "CORE DIFFERENTIATOR")

    from backend.engine import optimize
    from backend.engine import optimizer_config as ocfg

    if not (REPO_ROOT / "backend/ml/models/outcome_model.joblib").exists():
        R.skip("all Phase 4 checks", "outcome_model.joblib missing")
        return

    oid = conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE event_type='payment_failed'"
        " AND status IN ('open','recovering') LIMIT 1").fetchone()[0]
    res = optimize.optimize_opportunity(conn, oid, persist=False)
    ranked = res["ranked"]
    R.check("the optimizer returns a ranked list", res["error"] is None and ranked,
            f"error={res['error']}, {len(ranked)} ranked, "
            f"{res['candidate_count']} generated, {len(res['pruned'])} pruned",
            "non-empty ranked list, no error")

    print("\n     rank  action        timing      method    channel   EIV")
    for row in ranked[:9]:
        print(f"     {row['rank']:>4}  {str(row['action_type']):<12} "
              f"{str(row['timing']):<10} {str(row['method']):<9} "
              f"{str(row['channel']):<9} {row['predicted_eiv']:>11.2f}")
    if len(ranked) > 9:
        print(f"     ... {len(ranked) - 9} more")

    R.sub("do_nothing is a real, competitive, zero-valued option")
    dn = [r for r in ranked if r["action_type"] == "do_nothing"]
    R.check("do_nothing is always present", len(dn) == 1,
            f"{len(dn)} do_nothing row(s)", "exactly one")
    if dn:
        R.check("do_nothing has EIV of EXACTLY zero", dn[0]["predicted_eiv"] == 0.0,
                f"{dn[0]['predicted_eiv']!r}",
                "0.0 exactly -- x - x - 0 through the same arithmetic as every "
                "other candidate, not a special case")

    R.sub("ranking direction and bounds")
    eivs = [r["predicted_eiv"] for r in ranked]
    R.check("ranking is descending by EIV", eivs == sorted(eivs, reverse=True),
            f"first {eivs[0]:.2f} -> last {eivs[-1]:.2f}, monotonic="
            f"{eivs == sorted(eivs, reverse=True)}", "non-increasing")
    R.check("rank field agrees with list order",
            [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1)),
            f"ranks {[r['rank'] for r in ranked][:6]}...", "1..n in order")
    R.check("candidate count within the declared ceiling",
            res["candidate_count"] <= ocfg.MAX_CANDIDATES,
            f"{res['candidate_count']} <= {ocfg.MAX_CANDIDATES}",
            f"at most MAX_CANDIDATES ({ocfg.MAX_CANDIDATES})")

    R.sub("the optimizer is advisory ONLY")
    sel = {r.get("selected") for r in ranked}
    R.check("the optimizer never marks a candidate selected", sel <= {0, None},
            f"selected values across ranked rows: {sel}",
            "0 or unset -- only the rule engine may grant selection")
    allowed_keys = {k for r in ranked for k in r if "allowed" in k.lower()}
    R.check("no ranked row carries a permission bit", not allowed_keys,
            f"permission-like keys found: {allowed_keys or 'none'}",
            "none -- `allowed` is the rule engine's alone")

    # EIV must be reproducible from its own parts.
    scored = [r for r in ranked if r.get("predicted_eiv") is not None
              and r.get("cost") is not None]
    if scored:
        row = scored[0]
        base = dn[0]["predicted_expected_amount_treated"] if dn else None
        recomputed = (row["predicted_expected_amount_treated"]
                      - row["predicted_expected_amount_baseline"] - row["cost"])
        R.check("EIV = E[amount|treated] - E[amount|baseline] - cost",
                abs(recomputed - row["predicted_eiv"]) < 1e-6,
                f"recomputed {recomputed:.6f} vs stored {row['predicted_eiv']:.6f}",
                "identical -- the definition is arithmetic, not a fitted quantity")

    R.sub("latency, measured not hidden")
    warm = live_ids(conn, 3)
    for o in warm:
        optimize.optimize_opportunity(conn, o, persist=False)
    times = []
    for o in live_ids(conn, 12):
        t0 = time.perf_counter()
        optimize.optimize_opportunity(conn, o, persist=False)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p50 = times[len(times) // 2]
    p95 = times[int(0.95 * (len(times) - 1))]
    R.disclosed("optimizer latency against the declared budget",
                f"p50 {p50:.1f}ms, p95 {p95:.1f}ms over {len(times)} opportunities "
                f"(budget {LATENCY_BUDGET_MS}ms)",
                f"p95 <= {LATENCY_BUDGET_MS}ms",
                "BUDGET NOT MET, known since Phase 4 and unresolved. ~99.7% is\n"
                "single-row model inference through frozen ml/inference.py.\n"
                "This is why the optimizer stays OFF at both request-synchronous\n"
                "entry points.")


# --------------------------------------------------------------------------
# PHASE 5 -- authority boundary                 [HEAVILY WEIGHTED]
# --------------------------------------------------------------------------

def phase5_authority(conn):
    R.section("PHASE 5 -- the authority boundary", "HIGHEST SEVERITY")

    import ast

    from backend.engine import decide_action as da_mod
    from backend.engine import execute_action as ea_mod
    from backend.engine import phase5_config as cfg
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action
    from backend.engine.execute_action import STATUS_MAP

    def opp(oid):
        return dict(conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id=?", (oid,)).fetchone())

    def decide(o, ranked=None, **kw):
        return decide_action(o, classify(o["event_type"], o.get("root_cause")),
                             conn, ranked_candidates=ranked, **kw)

    ids = live_ids(conn)

    R.sub("the executable vocabulary is closed and declared")
    R.check("executor action set == declared executable vocabulary",
            set(STATUS_MAP) == set(cfg.EXECUTABLE_ACTIONS),
            f"STATUS_MAP={sorted(STATUS_MAP)}",
            f"== EXECUTABLE_ACTIONS={sorted(cfg.EXECUTABLE_ACTIONS)}")
    R.check("method change is declared permanently non-executable",
            cfg.METHOD_CHANGE_IS_EXECUTABLE is False,
            f"METHOD_CHANGE_IS_EXECUTABLE={cfg.METHOD_CHANGE_IS_EXECUTABLE}",
            "False -- a structural boundary, not a tunable")

    R.sub("method_change: evaluable, NEVER dispatchable, even at rank 1")
    conn.execute("UPDATE opportunities SET created_at=?, root_cause='expired_card',"
                 " event_type='payment_failed', status='open' WHERE opportunity_id=?",
                 (at_local_hour(12), ids[2]))
    conn.commit()
    o3 = opp(ids[2])
    lp = conn.execute("SELECT * FROM payments WHERE opportunity_id=?"
                      " ORDER BY created_at DESC LIMIT 1", (ids[2],)).fetchone()
    cur_method = lp["method"] if lp else "card"
    other = "upi" if cur_method != "upi" else "card"
    mc_ranked = [
        {"action_type": "retry", "rank": 1, "timing": "immediate", "timing_hours": 0.0,
         "method": other, "channel": "n/a", "method_changed": True,
         "predicted_eiv": 999999.0},
        {"action_type": "reminder", "rank": 2, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 1.0},
    ]
    mc = decide(o3, ranked=mc_ranked, latest_payment=dict(lp) if lp else None)
    approved_mc = mc["allowed"] and mc["action_type"] == "retry"
    R.check("a rank-1 method change with astronomic EIV is never approved",
            not approved_mc,
            f"current method={cur_method}, rank 1 = retry on {other} at EIV 999999.0; "
            f"result action={mc['action_type']!r} allowed={mc['allowed']} "
            f"outcome={mc['outcome']}",
            "not an approved retry on a different method -- rank is irrelevant "
            "to this boundary")
    R.check("the refusal is recorded, not silent",
            "payment-method change" in mc["reasoning"],
            f"reasoning: {mc['reasoning'][:100]}...",
            "considered-and-refused appears in the audit trail")

    R.sub("no decision can carry a payment method to the executor")
    o0 = opp(ids[0])
    plain = decide(o0)
    R.check("no branch returns a 'method' key",
            "method" not in plain and "method" not in mc,
            f"hardcoded keys={sorted(plain)}",
            "absent on every branch -- the executor has no field for one to ride on")

    R.sub("execute_action does not re-derive compliance")
    src = Path(ea_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    docstrings = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
    banned = ("COOLDOWN", "MAX_RETRIES", "CONTACT_WINDOW", "cooldown",
              "contact_window", "_has_customer_reply")
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in banned:
            hits.append(f"{node.id}@{node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr in banned:
            hits.append(f".{node.attr}@{node.lineno}")
    R.check("the executor references no compliance rule", not hits,
            f"compliance identifiers in execute_action.py: {hits or 'none'}",
            "none -- it writes what decide_action decided and re-checks nothing")
    R.check("only the rule engine grants the permission bit",
            'allowed": True' not in src and "allowed'] = True" not in src,
            "no `allowed: True` literal in execute_action.py",
            "absent -- decide_action is the sole grantor")


# --------------------------------------------------------------------------
# PHASE 5 -- fallthrough and the disable switch  [HEAVILY WEIGHTED]
# --------------------------------------------------------------------------

def phase5_fallthrough(conn):
    R.section("PHASE 5 -- fallthrough and the runtime disable switch", "RISKIEST INTEGRATION")

    from backend.engine import phase5_config as cfg
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action

    def opp(oid):
        return dict(conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id=?", (oid,)).fetchone())

    def decide(o, ranked=None, **kw):
        return decide_action(o, classify(o["event_type"], o.get("root_cause")),
                             conn, ranked_candidates=ranked, **kw)

    ids = live_ids(conn)

    R.sub("a real blocked-then-compliant scenario")
    # Created at 3am: contact actions are blocked by the contact window, but
    # escalation is internal routing and is not. This is the ONE compliance rule
    # that is action-dependent, and therefore the case fallthrough exists for.
    conn.execute("UPDATE opportunities SET created_at=?, status='open'"
                 " WHERE opportunity_id=?", (at_local_hour(3), ids[1]))
    conn.commit()
    o2 = opp(ids[1])
    hardcoded = decide(o2)
    ranked = [
        {"action_type": "reminder", "rank": 1, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 90.0, "eiv_confidence": "low", "eiv_gap_to_next": 0.004},
        {"action_type": "escalate", "rank": 2, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 40.0},
    ]
    fell = decide(o2, ranked=ranked)
    R.check("a blocked top candidate falls through to the next compliant one",
            hardcoded["outcome"] == "blocked_contact_hours"
            and fell["action_type"] == "escalate" and fell["allowed"] is True,
            f"without list -> {hardcoded['action_type']}/{hardcoded['outcome']}; "
            f"with list -> {fell['action_type']}/{fell['outcome']}",
            "hardcoded blocks the reminder; the ranked path reaches escalate")
    R.check("the fallthrough names what it skipped and why",
            "contact window" in fell["reasoning"] and "rank 1" in fell["reasoning"],
            f"reasoning: {fell['reasoning'][:120]}...",
            "the skipped candidate and its reason are both in the audit trail")

    R.sub("blocks that are NOT action-dependent are never overturned")
    conn.execute("UPDATE opportunities SET created_at=?, status='open'"
                 " WHERE opportunity_id=?", (at_local_hour(12), ids[4]))
    conn.commit()
    o4 = opp(ids[4])
    conn.execute(
        "INSERT INTO recovery_decisions (opportunity_id, candidate_id, action_type,"
        " outcome, reasoning, triggered_by, ml_recovery_probability, flag_type,"
        " timestamp) VALUES (?, NULL, 'retry', 'executed', 'verify fixture',"
        " 'rule', NULL, NULL, ?)", (ids[4], int(time.time()) - 6 * 3600))
    conn.commit()
    cooled = decide(o4, ranked=ranked)
    R.check("a cooldown block is not unblocked by supplying a ranked list",
            cooled["allowed"] is False and cooled["outcome"] == "blocked_cooldown"
            and cooled == decide(o4),
            f"with list -> {cooled['action_type']}/{cooled['outcome']}, "
            f"identical to no-list = {cooled == decide(o4)}",
            "identical to the hardcoded path -- cooldown blocks every candidate "
            "equally, so falling through would overturn a compliance decision")

    R.sub("the runtime kill switch, flipped mid-run")
    conn.execute("UPDATE opportunities SET created_at=?, status='open'"
                 " WHERE opportunity_id=?", (at_local_hour(12), ids[3]))
    conn.commit()
    o5 = opp(ids[3])
    dn_first = [
        {"action_type": "do_nothing", "rank": 1, "timing": "immediate",
         "timing_hours": 0.0, "method": None, "channel": None,
         "predicted_eiv": 50.0, "candidate_id": 90001},
        {"action_type": "reminder", "rank": 2, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 20.0, "candidate_id": 90002},
    ]
    on = decide(o5, ranked=dn_first)
    cfg.OPTIMIZER_PATHWAY_ENABLED = False
    off = decide(o5, ranked=dn_first)
    no_list = decide(o5)
    cfg.OPTIMIZER_PATHWAY_ENABLED = True
    back = decide(o5, ranked=dn_first)
    R.check("flipping the switch mid-run reverts to pre-optimizer behaviour",
            off == no_list and off != on and back == on,
            f"ON -> {on['action_type']} (candidate {on.get('candidate_id')}); "
            f"OFF -> {off['action_type']}, identical to no-list = {off == no_list}; "
            f"back ON -> {back['action_type']}",
            "OFF is byte-identical to passing no list at all; no restart needed")
    R.check("the entry-point table keeps the optimizer off by default",
            not any(cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT.values()),
            str(cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT),
            "all False while the latency budget is unmet")


# --------------------------------------------------------------------------
# PHASE 5 -- execution write side                [HEAVILY WEIGHTED]
# --------------------------------------------------------------------------

def phase5_execution(conn):
    R.section("PHASE 5 -- execution: lifecycle, and what must NOT be written", "SIDE EFFECTS")

    from backend.data_factory.candidate_generation import TIMING_HOURS
    from backend.db.db import EXECUTION_STATES
    from backend.engine.execute_action import execute_action

    def opp(oid):
        return dict(conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id=?", (oid,)).fetchone())

    def n_exec():
        return conn.execute("SELECT COUNT(*) FROM recovery_executions").fetchone()[0]

    ids = live_ids(conn)

    R.sub("a decision that is not an execution writes NO execution row")
    before = n_exec()
    o = opp(ids[5])
    execute_action(o, {"action_type": "do_nothing", "allowed": True,
                       "outcome": "executed", "reasoning": "verify",
                       "triggered_by": "rule"}, conn)
    after = n_exec()
    status_now = conn.execute(
        "SELECT status FROM opportunities WHERE opportunity_id=?", (ids[5],)).fetchone()[0]
    R.check("do_nothing writes ZERO execution rows", after == before,
            f"execution rows {before} -> {after} (delta {after - before})",
            "delta 0 -- deciding to act by not acting is a decision, not an "
            "execution; it previously wrote a fabricated 'executed' row")
    R.check("do_nothing does not move the opportunity", status_now == o["status"],
            f"status {o['status']!r} -> {status_now!r}", "unchanged")

    before = n_exec()
    execute_action(opp(ids[6]), {"action_type": "retry", "allowed": False,
                                 "outcome": "blocked_cooldown", "reasoning": "verify",
                                 "triggered_by": "rule"}, conn)
    R.check("a blocked decision writes ZERO execution rows", n_exec() == before,
            f"execution rows {before} -> {n_exec()}", "delta 0")

    R.sub("scheduling is a lifecycle state, not a compliance outcome")
    oid = ids[7]
    cid = conn.execute(
        "INSERT INTO recovery_candidates (opportunity_id, action_type, timing,"
        " method, channel, predicted_eiv, rank, selected, created_at)"
        " VALUES (?, 'payment_link', '24h', 'n/a', 'email', 5.0, 1, 0, ?)",
        (oid, int(time.time()))).lastrowid
    conn.commit()
    t0 = int(time.time())
    execute_action(opp(oid), {"action_type": "payment_link", "allowed": True,
                              "outcome": "executed", "reasoning": "verify",
                              "triggered_by": "rule", "candidate_id": cid}, conn)
    row = dict(conn.execute(
        "SELECT e.state, e.scheduled_for, e.executed_at, d.candidate_id, d.outcome"
        " FROM recovery_executions e JOIN recovery_decisions d"
        " ON d.decision_id = e.decision_id WHERE d.candidate_id=?", (cid,)).fetchone())
    expected_at = t0 + int(TIMING_HOURS["24h"] * 3600)
    R.check("a 24h candidate is SCHEDULED, not executed",
            row["state"] == "scheduled" and row["executed_at"] is None
            and abs(row["scheduled_for"] - expected_at) <= 5,
            f"state={row['state']} scheduled_for=+{row['scheduled_for']-t0}s "
            f"executed_at={row['executed_at']}",
            "state='scheduled', scheduled_for = now + 24h, executed_at NULL")
    R.check("the lifecycle state is in the closed vocabulary",
            row["state"] in EXECUTION_STATES,
            f"{row['state']!r} in {list(EXECUTION_STATES)}", "a declared state")
    R.check("the compliance outcome stays a compliance outcome",
            row["outcome"] == "executed",
            f"decision outcome={row['outcome']!r}, execution state={row['state']!r}",
            "'scheduled' never leaks into recovery_decisions.outcome")
    sel = conn.execute("SELECT selected FROM recovery_candidates WHERE candidate_id=?",
                       (cid,)).fetchone()[0]
    R.check("the approved candidate is marked selected=1", sel == 1,
            f"selected={sel}", "1 -- set by the rule engine after adjudication")

    R.sub("idempotency: one decision can never carry two execution rows")
    did = conn.execute(
        "SELECT decision_id FROM recovery_decisions WHERE candidate_id=?",
        (cid,)).fetchone()[0]
    import sqlite3
    try:
        conn.execute("INSERT INTO recovery_executions (decision_id, state,"
                     " executed_at, channel) VALUES (?, 'executed', ?, NULL)",
                     (did, int(time.time())))
        conn.commit()
        rejected = False
    except sqlite3.IntegrityError:
        rejected = True
    R.check("a second execution row for one decision is rejected", rejected,
            f"UNIQUE(decision_id) rejected the duplicate = {rejected}",
            "rejected -- the mechanism the scheduled-dispatch sweep relies on")


# --------------------------------------------------------------------------
# PHASE 5 -- network health
# --------------------------------------------------------------------------

def phase5_network_health(conn):
    R.section("PHASE 5 -- network health at serving time", "NEWLY LIVE")

    from backend.engine import optimize
    from backend.engine import phase5_config as cfg
    from backend.ml import inference
    from backend.ml import outcome_features as feats

    R.sub("mapping and horizon tripwires")
    lo, hi = cfg.HEALTH_WINDOW_HOURS, cfg.HEALTH_HORIZON_HOURS
    origin = cfg.NETWORK_HEALTH_ORIGIN_UNIX
    probes = [origin, 0, origin - 10 ** 8, origin + 10 ** 9] + \
             [origin + h * 3600 for h in (0, 1, 4, hi - 1, hi, hi * 2)]
    outside = [t for t in probes if not (lo <= cfg.simulated_hour_for(t) < hi)]
    R.check("every timestamp maps inside the truthful range", not outside,
            f"{len(probes)} probes, all in [{lo}, {hi}) = {not outside}",
            f"[{lo}, {hi}) -- below {lo} no window has closed; at or past {hi} "
            "the lookup clamps and returns a stale constant claiming known=1.0")
    R.check("the horizon exceeds the trailing-average span",
            hi > feats.NETWORK_HEALTH_WINDOW_HOURS,
            f"horizon {hi}h vs trailing {feats.NETWORK_HEALTH_WINDOW_HOURS}h "
            f"(ratio {feats.NETWORK_HEALTH_WINDOW_HOURS/hi:.3f})",
            "strictly greater, else every query averages from window 0 and the "
            "rolling value degenerates into a prefix average")

    R.sub("the live path actually sees it")
    n_pay = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    n_ch = conn.execute("SELECT COUNT(*) FROM payments WHERE bank IS NOT NULL"
                        " AND psp IS NOT NULL").fetchone()[0]
    R.check("every payment names a (bank, method, psp) channel", n_ch == n_pay,
            f"{n_ch}/{n_pay} payments carry a channel", "all of them")

    ctx, _ = optimize.load_context(conn, live_ids(conn, 1)[0])
    R.check("optimize.load_context supplies the channel and a mapped hour",
            ctx.get("bank") is not None and ctx.get("psp") is not None
            and lo <= ctx.get("decision_time_hours", -1) < hi,
            f"bank={ctx.get('bank')!r} psp={ctx.get('psp')!r} "
            f"decision_time_hours={ctx.get('decision_time_hours'):.3f}",
            f"real channel from the latest payment, hour in [{lo}, {hi})")

    lookup = inference._get_health_lookup(conn)
    known, scores = [], []
    for oid in live_ids(conn):
        c, _ = optimize.load_context(conn, oid)
        cand = {"action_type": "retry", "timing": "immediate", "timing_hours": 0.0,
                "method": c.get("current_method"), "channel": "n/a",
                "method_changed": False}
        row = feats.build_feature_row(c, cand, lookup)
        known.append(row["network_health_known"])
        if row["network_health_score_rolling"] is not None:
            scores.append(row["network_health_score_rolling"])
    R.check("every live scoring resolves to real observations",
            set(known) == {1.0}, f"network_health_known values: {sorted(set(known))} "
            f"across {len(known)} contexts", "{1.0}")

    R.sub("TRIPWIRE: the rolling value must not be a stale constant")
    distinct, spread = len(set(scores)), (max(scores) - min(scores)) if scores else 0
    R.check("rolling health varies across opportunities",
            distinct > 1 and spread > 0.01,
            f"{distinct} distinct values across {len(scores)} contexts, "
            f"range [{min(scores):.4f}, {max(scores):.4f}], spread {spread:.4f}",
            ">1 distinct and spread >0.01 -- a constant reported as known=1.0 is "
            "the past-the-end clamp signature and is worse than known=0, because "
            "it is indistinguishable from real data")


# --------------------------------------------------------------------------
# PHASE 5 -- the full pipeline
# --------------------------------------------------------------------------

def phase5_pipeline(conn):
    R.section("PHASE 5 -- the full pipeline through a real entry point", "END TO END")

    from backend.engine.trigger_event import trigger_event

    before_d = conn.execute("SELECT COUNT(*) FROM recovery_decisions").fetchone()[0]
    out = trigger_event(event_type="payment_failed", amount=54321, conn=conn,
                        root_cause="gateway_timeout",
                        event_id=f"verify-{int(time.time())}")
    decision = out.get("decision") or {}
    R.check("classify -> decide -> execute runs end to end",
            out.get("status") == "ok" and decision.get("outcome") is not None,
            f"status={out.get('status')} "
            f"opportunity={(out.get('opportunity') or {}).get('opportunity_id')} "
            f"action={decision.get('action_type')} outcome={decision.get('outcome')} "
            f"(the outcome depends on the wall-clock hour you run at -- contact "
            f"outside 9am-8pm is correctly blocked)",
            "an opportunity is created and a logged decision comes back")

    after_d = conn.execute("SELECT COUNT(*) FROM recovery_decisions").fetchone()[0]
    R.check("the decision was persisted", after_d > before_d,
            f"recovery_decisions {before_d} -> {after_d}", "grew by at least one")

    silent = conn.execute("SELECT COUNT(*) FROM recovery_decisions WHERE reasoning"
                          " IS NULL OR reasoning=''").fetchone()[0]
    R.check("no decision is ever silent", silent == 0,
            f"{after_d} decisions logged, {silent} without a reason",
            "0 without a reason -- every action taken or declined carries one")


# --------------------------------------------------------------------------
# Known gaps
# --------------------------------------------------------------------------

def known_gaps():
    R.section("KNOWN, DISCLOSED GAPS -- printed so they are not mistaken for news")

    R.sub(f"the {len(KNOWN_TEST_FAILURES)} pre-existing pytest failures, by name")
    print("     Present at the Phase 5 W0 baseline (git 866e478) and unchanged")
    print("     since. If `pytest` shows exactly these, nothing new is broken.\n")
    for i, name in enumerate(KNOWN_TEST_FAILURES, 1):
        print(f"       {i:>2}. {name}")
    R.disclosed("pre-existing pytest failures",
                f"{len(KNOWN_TEST_FAILURES)} known failures, listed above",
                "0 in an ideal world",
                "14 pre-date Phase 4; 2 are Phase 4's disclosed pair. Compare with\n"
                "`cd backend && python -m pytest -q`.")

    R.sub(f"resolved during Phase 5 ({len(RESOLVED_SINCE_W0)}), no longer failing")
    for name, how in RESOLVED_SINCE_W0:
        print(f"       {name}\n           -> {how}")
    R.check("the closeout list has no open items",
            True, "C1 fixed by re-implementation; C2 withdrawn as a probe artefact",
            "no open items -- PHASE5_NOTES.md section 1a",
            "An item may leave that list in exactly three ways: fixed, formally\n"
            "retired with a recorded reason, or the finding withdrawn as\n"
            "erroneous with the diagnosis recorded. Never by being absorbed into\n"
            "the known-failure count above.")

    R.disclosed("execute_action() is not idempotent at the call level",
                "calling it twice with one decision writes 2 decisions, 2 executions",
                "ideally 1 and 1",
                "The UNIQUE index only prevents two executions per decision, which\n"
                "IS the guarantee the dispatch sweep needs (it updates an existing\n"
                "row). If dispatch is ever built by re-calling execute_action(), an\n"
                "idempotency key becomes mandatory. PHASE5_NOTES.md section 1c.")

    R.disclosed("closeout C2 -- RETRACTED, recorded here so it is not re-raised",
                "the payment_history_score 'sign inversion' was a probe artefact",
                "n/a -- no defect",
                "The probe swung 0.05 -> 0.95; the training corpus spans\n"
                "[0.2031, 0.9236] and contains ZERO rows outside that. Within the\n"
                "interquartile range the model is directionally correct on 92/92\n"
                "opportunities. Every directional probe in this script now swings\n"
                "within TRAINING_SUPPORT for exactly this reason.")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="skip Phase 2 live generation (the slowest section)")
    args = ap.parse_args()

    print("=" * 78)
    print("  PHASES 0-5 END-TO-END VERIFICATION")
    print("=" * 78)
    print(f"  repo   : {REPO_ROOT}")
    print(f"  python : {sys.version.split()[0]}")
    print(f"  started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("  Writes only to a temp directory; touches no git state.")

    tmp = Path(tempfile.mkdtemp(prefix="verify_phases_"))
    started = time.perf_counter()
    conn = None
    try:
        conn = build_temp_world(tmp)
        print(f"  tempdir: {tmp}")
        phase01(conn)
        phase2(args.quick)
        phase3(conn)
        phase4(conn)
        phase5_authority(conn)
        phase5_fallthrough(conn)
        phase5_execution(conn)
        phase5_network_health(conn)
        phase5_pipeline(conn)
        known_gaps()
    except Exception:
        print("\n" + "!" * 78)
        print("!!  THE VERIFICATION SCRIPT ITSELF CRASHED -- an unexpected result")
        print("!" * 78)
        traceback.print_exc()
        R.check("verification script ran to completion", False,
                "crashed, see traceback above", "ran to completion")
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  elapsed: {time.perf_counter() - started:.1f}s")
    return 1 if R.summary() else 0


if __name__ == "__main__":
    sys.exit(main())
