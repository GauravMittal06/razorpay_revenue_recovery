#!/usr/bin/env python3
"""
End-to-end verification of Phases 0-5. Run this yourself.

WHAT THIS IS
    An independent gut-check that the phases work *together* on a real
    database, not a re-run of the unit suite. Every check prints the value it
    actually observed, so the output can be read rather than merely counted.

WHAT THIS IS NOT
    A pass/fail gate, and not a replacement for `pytest`. Where a property is
    statistical and needs volume (the Phase 2 validators, Phase 3's
    calibration and generalisation gates), this script says so and defers to
    the test suite rather than pretending a 40-case sample settles it.

HONESTY RULES THIS SCRIPT FOLLOWS
    - Known, disclosed gaps are printed, not hidden, and counted separately.
    - Anything unexpected is counted separately again and made impossible to
      miss at the end.
    - "Expected" values are stated before the observation, so you can judge
      whether the expectation was reasonable rather than trusting a verdict.

SAFETY
    Touches no git state and writes nothing outside a temporary directory that
    is deleted on exit. Your recovery.db, seed data and generated corpora are
    never read for mutation and never written. Safe to run repeatedly.

DEPENDENCIES
    Not stdlib-only, and cannot be: Phases 2-4 require the project's pinned
    scientific stack (pandas, numpy, scikit-learn, xgboost, joblib, scipy).
    FILE_INVENTORY.md describes this file as "stdlib-only"; that description
    predates Phases 2-4 and is inaccurate for anything past Phase 1. The
    script's own machinery is stdlib; the code under test is not.

    It also needs the gitignored model artifacts:
        backend/ml/models/outcome_model.joblib   (Phase 3/4)
        backend/ml/models/xgb_model.joblib       (Phase 5 advisory field)
    If either is missing the affected checks are reported as SKIPPED with the
    reason, not silently passed.

USAGE
    cd <repo root>
    python test_everything.py            # full run
    python test_everything.py --quick    # skip Phase 2 generation (slowest)
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

# Pinned so every run of this script sees the same seed dataset.
os.environ.setdefault("SEED_DATA_NOW", "1756000000")

LATENCY_BUDGET_MS = 250.0


def at_local_hour(hour: int, days_ago: int = 1) -> int:
    """
    A timestamp at a specific *local* hour.

    decide_action()'s contact-window check reads the local hour of
    `created_at`, so a fixture built as `time.time() - 3600` lands in a
    different compliance branch depending on what time of day this script is
    run. Every Phase 5 fixture below pins the hour explicitly instead, so the
    output is the same at 3am as at 3pm.
    """
    from datetime import datetime, timedelta
    dt = (datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
          - timedelta(days=days_ago))
    return int(dt.timestamp())

# The 16 test failures present at the Phase 5 W0 baseline (git 866e478) and
# unchanged since. Listed by name so a NEW failure is visibly different from
# the known set rather than vanishing into a count.
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
    "test_phase1_concurrency.py::test_concurrent_recovery_confirmations_produce_one_winner",
    "test_phase1_concurrency.py::test_recovery_update_is_guarded_by_the_status_it_read",
    "test_phase1_concurrency.py::test_two_overlapping_batch_cycles_do_not_double_act_on_one_case",
    "test_phase4_optimizer.py::test_higher_true_incremental_value_ranks_above_lower",
    "test_phase4_optimizer.py::test_end_to_end_latency_against_the_declared_budget",
]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

class Reporter:
    MATCH, DISCLOSED, UNEXPECTED, SKIP = "MATCH", "DISCLOSED", "UNEXPECTED", "SKIP"

    def __init__(self):
        self.rows = []

    def section(self, title):
        print()
        print("=" * 78)
        print(f"  {title}")
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
        """A known, recorded non-match. Printed, never hidden."""
        self._log(self.DISCLOSED, label, observed, expected, note)

    def skip(self, label, reason):
        self._log(self.SKIP, label, "not run", "n/a", reason)

    def summary(self):
        counts = {k: 0 for k in (self.MATCH, self.DISCLOSED, self.UNEXPECTED, self.SKIP)}
        for status, *_ in self.rows:
            counts[status] += 1
        total = len(self.rows)

        print()
        print("=" * 78)
        print("  SUMMARY")
        print("=" * 78)
        print(f"  checks run                      : {total}")
        print(f"  matched expectation             : {counts[self.MATCH]}")
        print(f"  known and disclosed non-matches : {counts[self.DISCLOSED]}")
        print(f"  skipped (missing prerequisite)  : {counts[self.SKIP]}")
        print(f"  UNEXPECTED                      : {counts[self.UNEXPECTED]}")

        if counts[self.DISCLOSED]:
            print("\n  Disclosed non-matches (each one is recorded in PHASE5_NOTES.md")
            print("  or a phase hand-off; none is a surprise):")
            for status, label, observed, _, _ in self.rows:
                if status == self.DISCLOSED:
                    print(f"    - {label}  ->  {observed}")

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
    from backend.data import generate_seed_data as gsd
    from backend.db import db as db_module

    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    gsd.DATA_DIR = data_dir
    db_module.DATA_DIR = data_dir
    db_module.DB_PATH = tmp / "verify.db"

    import io
    from contextlib import redirect_stdout
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
    return conn, db_module


# --------------------------------------------------------------------------
# PHASE 0 / 1
# --------------------------------------------------------------------------

def phase01(conn, db_module, tmp):
    R.section("PHASE 0/1 -- bootstrap, schema, idempotent rebuild")

    from backend.db.db import DECISION_OUTCOMES, EXECUTION_STATES, create_schema

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    required = {"merchants", "customers", "opportunities", "payments",
                "recovery_candidates", "recovery_decisions", "recovery_executions",
                "experiment_assignment", "bank_health_observations", "messages",
                "dataset_registry"}
    R.check("every required table exists", required <= tables,
            f"{len(tables)} tables: {sorted(tables)}",
            f"superset of {len(required)} required tables")

    R.sub("the three-way separation (decision / execution / business outcome)")
    R.check("recovery_decisions carries a closed compliance vocabulary",
            len(DECISION_OUTCOMES) == 6,
            f"{len(DECISION_OUTCOMES)}: {list(DECISION_OUTCOMES)}",
            "6 values, blocked_max_retries removed in Phase 5")
    R.check("recovery_executions carries a closed lifecycle vocabulary",
            len(EXECUTION_STATES) == 7,
            f"{len(EXECUTION_STATES)}: {list(EXECUTION_STATES)}",
            "7 values")
    overlap = set(DECISION_OUTCOMES) & set(EXECUTION_STATES)
    R.disclosed("the two vocabularies share one token",
                f"overlap = {sorted(overlap)}",
                "ideally disjoint",
                "'executed' is both a compliance outcome and a lifecycle state.\n"
                "Pre-existing; queries joining the two tables must alias.\n"
                "Recorded in PHASE5_NOTES.md section 2.")

    R.sub("uniqueness guarantees the later phases depend on")
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    R.check("one execution row per decision (UNIQUE index present)",
            "idx_recovery_executions_decision_id" in idx,
            f"present={'idx_recovery_executions_decision_id' in idx}",
            "index exists -- this is what makes W6 dispatch idempotent")

    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    R.check("foreign keys are enforced", fk == 1, f"PRAGMA foreign_keys = {fk}",
            "1 (on) -- candidate_id must reference a real candidate")

    R.sub("idempotent rebuild")
    before = len(tables)
    create_schema(conn)
    create_schema(conn)
    after = len({r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()})
    R.check("create_schema is idempotent", before == after,
            f"{before} tables before, {after} after two further calls",
            "unchanged, no error")

    R.sub("seed data loaded")
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("merchants", "customers", "opportunities", "payments")}
    R.check("seed rows present", all(v > 0 for v in counts.values()),
            str(counts), "all non-zero")
    open_n = conn.execute(
        "SELECT COUNT(*) FROM opportunities WHERE status IN ('open','recovering')"
    ).fetchone()[0]
    R.check("actionable opportunities exist", open_n > 0,
            f"{open_n} open/recovering", "> 0")

    R.sub("network channel data (added Phase 5)")
    n_pay = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    n_chan = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE bank IS NOT NULL AND psp IS NOT NULL"
    ).fetchone()[0]
    R.check("every payment names a (bank, method, psp) channel", n_chan == n_pay,
            f"{n_chan}/{n_pay} payments carry bank and psp", "all of them")
    n_health = conn.execute(
        "SELECT COUNT(*) FROM bank_health_observations").fetchone()[0]
    R.check("the network-health series is seeded", n_health > 0,
            f"{n_health} observation rows", "> 0")


# --------------------------------------------------------------------------
# PHASE 2
# --------------------------------------------------------------------------

def phase2(quick):
    R.section("PHASE 2 -- data factory: profiles, generation, reproducibility, locks")

    from backend.data_factory import calibration_profiles as cp

    R.sub("both calibration profiles")
    names = sorted(cp.PROFILES)
    R.check("both profiles are registered", set(names) == {"baseline", "stress"},
            str(names), "['baseline', 'stress']")
    b, s = cp.get_profile("baseline"), cp.get_profile("stress")
    differs = b.__dict__ != s.__dict__
    R.check("the two profiles genuinely differ", differs,
            f"baseline != stress -> {differs}",
            "different parameters, else cross-profile generalisation is vacuous")

    R.sub("locked thresholds and dataset registry")
    import json
    lock = REPO_ROOT / "backend/data_factory/locked_thresholds.json"
    if lock.exists():
        d = json.loads(lock.read_text(encoding="utf-8"))
        R.check("statistical tolerances are locked and dated",
                "locked_at_utc" in d,
                f"locked_at_utc={d.get('locked_at_utc')} "
                f"generator={d.get('generator_version_at_lock')}",
                "a lock timestamp and generator version are recorded")
    else:
        R.skip("locked thresholds", "locked_thresholds.json not present")

    reg = REPO_ROOT / "backend/data_factory/registry"
    manifests = sorted(reg.glob("*.json")) if reg.exists() else []
    if manifests:
        for m in manifests:
            d = json.loads(m.read_text(encoding="utf-8"))
            R.check(f"registry manifest: {d.get('calibration_profile')}",
                    d.get("row_count", 0) > 0 and d.get("seed") is not None,
                    f"seed={d.get('seed')} cases={d.get('case_count')} "
                    f"rows={d.get('row_count')} gen={d.get('generator_version')}",
                    "seed, case/row counts and generator version all recorded")
    else:
        R.skip("dataset registry manifests",
               "backend/data_factory/registry/ is gitignored and not present")

    if quick:
        R.skip("live generation for both profiles", "--quick given")
        return

    R.sub("live generation, both profiles (small sample -- structure only)")
    print("     (statistical validators need volume and are the pytest suite's")
    print("      job; this checks the generator runs and is deterministic)")
    from backend.data_factory import candidate_outcome_dataset as cod

    N = 40
    for name in ("baseline", "stress"):
        profile = cp.get_profile(name)
        t0 = time.perf_counter()
        joint, truth, _world, _hi, _obs = cod.generate_dataset(profile, 42, n_cases=N)
        dt = (time.perf_counter() - t0) * 1000
        R.check(f"{name}: generated a joint candidate-outcome dataset",
                len(joint) > 0 and len(truth) > 0,
                f"{len(joint)} joint rows, {len(truth)} truth rows, {dt:.0f}ms",
                f"non-empty for {N} cases")
        if "candidate_action" in joint.columns:
            has_dn = (joint["candidate_action"] == "do_nothing").sum()
            R.check(f"{name}: do_nothing present for every case",
                    has_dn == joint["case_id"].nunique(),
                    f"{has_dn} do_nothing rows / {joint['case_id'].nunique()} cases",
                    "exactly one per case")

    R.sub("reproducibility -- same seed, byte-identical output")
    a = cod.generate_dataset(cp.get_profile("baseline"), 42, n_cases=N)[0]
    b2 = cod.generate_dataset(cp.get_profile("baseline"), 42, n_cases=N)[0]
    same = a.equals(b2)
    R.check("two runs at seed 42 produce identical data", same,
            f"identical={same} ({len(a)} rows compared)",
            "identical -- the generator's only randomness is the seed")

    diff_seed = cod.generate_dataset(cp.get_profile("baseline"), 43, n_cases=N)[0]
    R.check("a different seed produces different data", not a.equals(diff_seed),
            f"seed42 == seed43 -> {a.equals(diff_seed)}",
            "different, else the seed is not wired through")


# --------------------------------------------------------------------------
# PHASE 3
# --------------------------------------------------------------------------

def phase3(conn):
    R.section("PHASE 3 -- joint outcome model: loads, scores, directional sanity")

    from backend.engine import optimize
    from backend.ml import inference

    model_path = REPO_ROOT / "backend/ml/models/outcome_model.joblib"
    if not model_path.exists():
        R.skip("Phase 3 model checks",
               f"{model_path.relative_to(REPO_ROOT)} missing (gitignored artifact)")
        return None

    ids = [r[0] for r in conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE status IN ('open','recovering')"
    ).fetchall()]
    oid = ids[0]
    ctx, _ = optimize.load_context(conn, oid)
    cand = {"action_type": "retry", "timing": "immediate", "timing_hours": 0.0,
            "method": ctx.get("current_method"), "channel": "n/a",
            "method_changed": False}

    R.sub("the model loads and produces a well-formed score")
    res = inference.score_candidate(ctx, cand, conn=conn)
    R.check("score_candidate returns a complete result", res["error"] is None,
            f"error={res['error']} p={res['p_recovery']} "
            f"amt={res['expected_recovered_amount']}",
            "error=None and all three fields populated")
    if res["error"] is None:
        R.check("p_recovery is a probability", 0.0 <= res["p_recovery"] <= 1.0,
                f"{res['p_recovery']:.6f}", "within [0, 1]")
        derived = res["p_recovery"] * res["expected_amount_given_recovered"]
        R.check("expected_recovered_amount = p x E[amount|recovered]",
                abs(derived - res["expected_recovered_amount"]) < 1e-6,
                f"{derived:.6f} vs {res['expected_recovered_amount']:.6f}",
                "identical -- the derivation is outside the pipelines")

    R.sub("directional sanity across every seeded opportunity")
    print("     Each feature is varied alone, all else held fixed, and the sign")
    print("     of the change in p_recovery is counted. Every swing stays INSIDE")
    print("     the feature's observed range in the training corpus -- outside")
    print("     it a tree ensemble has no defined behaviour and the result")
    print("     measures leaf placement, not a learned relationship.")
    print("     The generator's own ground truth fixes the expected sign:")
    print("       payment_history_score -> liquidity_state -> recovery_willingness (+)")
    print("       past_recovery_rate -> customer_responsiveness -> ... (+)")
    print("       retry_count / prior_contacts: fatigue (-)")

    from backend.ml import outcome_features as feats
    lookup = inference._get_health_lookup(conn)
    regimes = set()

    def _enriched(o):
        """
        Context carrying the real (bank, method, psp) from the opportunity's
        latest payment, so the probe runs at network_health_known=1.0.

        optimize.load_context() still hardcodes bank=None/psp=None -- that is
        a frozen Phase 4 module and closing it needs a ruling -- so the channel
        is attached here rather than read from the context it returns.
        """
        c, _ = optimize.load_context(conn, o)
        if c is None:
            return None, None
        p = conn.execute(
            "SELECT method, bank, psp FROM payments WHERE opportunity_id=?"
            " ORDER BY created_at DESC LIMIT 1", (o,)).fetchone()
        if p is None or p["bank"] is None:
            return None, None
        c = dict(c)
        c["bank"], c["psp"] = p["bank"], p["psp"]
        c["decision_time_hours"] = float((hash(o) % 150) + 10)
        return c, {"action_type": "retry", "timing": "immediate",
                   "timing_hours": 0.0, "method": p["method"],
                   "channel": "n/a", "method_changed": False}

    def direction(field, lo, hi):
        import pandas as pd
        model = inference._load_model()
        up = down = 0
        deltas = []
        for o in ids:
            c, cd = _enriched(o)
            if c is None:
                continue
            ps = []
            for value in (lo, hi):
                ctx = dict(c); ctx[field] = value
                row = feats.build_feature_row(ctx, cd, lookup)
                regimes.add(row["network_health_known"])
                X = pd.DataFrame([row])[feats.ALL_FEATURES]
                ps.append(float(model["p_pipeline"].predict_proba(X)[:, 1][0]))
            d = ps[1] - ps[0]
            deltas.append(d)
            up += d > 0
            down += d <= 0
        n = len(deltas) or 1
        return up, down, len(deltas), sum(deltas) / n

    up, down, n, mean = direction("past_recovery_rate", 0.05, 0.95)
    R.check("higher past_recovery_rate raises p_recovery", up > down,
            f"raises in {up}/{n} ({up/max(n,1):.1%}), mean delta {mean:+.5f}",
            "majority raise -- generator is monotonic positive here")

    up, down, n, mean = direction("retry_count", 0, 3)
    R.check("more prior retries lowers p_recovery", down > up,
            f"lowers in {down}/{n} ({down/max(n,1):.1%}), mean delta {mean:+.5f}",
            "majority lower -- fatigue")

    up, down, n, mean = direction("prior_contacts_in_window", 0, 5)
    R.check("more prior contacts lowers p_recovery", down > up,
            f"lowers in {down}/{n} ({down/max(n,1):.1%}), mean delta {mean:+.5f}",
            "majority lower -- fatigue")

    # p10..p90 of payment_history_score in the training corpus. Swinging
    # outside the observed range measures leaf placement, not a learned
    # relationship: the corpus spans [0.2031, 0.9236] and contains ZERO rows
    # below 0.05 or above 0.95. Probing 0.05 -> 0.95 produced an apparent sign
    # inversion that was retracted as a probe artefact (PHASE5_NOTES, C2).
    up, down, n, mean = direction("payment_history_score", 0.4101, 0.8246)
    R.check("higher payment_history_score raises p_recovery", up > down,
            f"raises in {up}/{n} ({up/max(n,1):.1%}), mean delta {mean:+.5f}",
            "majority raise, swung within the corpus p10..p90 range "
            "[0.4101, 0.8246]")

    R.check("the directional probe ran with network health present",
            regimes == {1.0},
            f"network_health_known values seen: {sorted(regimes)}",
            "{1.0} -- the probe attaches the real channel from the payment row")

    R.sub("the live optimizer sees the network channel")
    from backend.engine import phase5_config as _cfg
    ctx_plain, _ = optimize.load_context(conn, ids[0])
    R.check("optimize.load_context supplies the network channel",
            ctx_plain.get("bank") is not None and ctx_plain.get("psp") is not None
            and _cfg.HEALTH_WINDOW_HOURS <= ctx_plain.get("decision_time_hours", -1)
            < _cfg.HEALTH_HORIZON_HOURS,
            f"bank={ctx_plain.get('bank')!r} psp={ctx_plain.get('psp')!r} "
            f"decision_time_hours={ctx_plain.get('decision_time_hours'):.3f}",
            f"real channel from the latest payment, and a simulated hour inside "
            f"[{_cfg.HEALTH_WINDOW_HOURS}, {_cfg.HEALTH_HORIZON_HOURS})")
    return None


# --------------------------------------------------------------------------
# PHASE 4
# --------------------------------------------------------------------------

def phase4(conn):
    R.section("PHASE 4 -- optimizer: bounded ranked list, do_nothing at zero, latency")

    from backend.engine import optimize
    from backend.engine import optimizer_config as ocfg

    if not (REPO_ROOT / "backend/ml/models/outcome_model.joblib").exists():
        R.skip("Phase 4 optimizer checks", "outcome_model.joblib missing")
        return None

    oid = conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE event_type='payment_failed'"
        " AND status IN ('open','recovering') LIMIT 1").fetchone()[0]

    R.sub(f"ranking a real opportunity ({oid})")
    res = optimize.optimize_opportunity(conn, oid, persist=False)
    ranked = res["ranked"]
    R.check("the optimizer returns a ranked list", res["error"] is None and ranked,
            f"error={res['error']}, {len(ranked)} ranked, "
            f"{res['candidate_count']} generated, {len(res['pruned'])} pruned",
            "non-empty ranked list, no error")

    print("\n     rank  action        timing      method   EIV")
    for row in ranked[:8]:
        print(f"     {row['rank']:>4}  {str(row['action_type']):<12} "
              f"{str(row['timing']):<10} {str(row['method']):<8} "
              f"{row['predicted_eiv']:>12.2f}")
    if len(ranked) > 8:
        print(f"     ... {len(ranked) - 8} more")

    dn = [r for r in ranked if r["action_type"] == "do_nothing"]
    R.check("do_nothing is always a scored candidate", len(dn) == 1,
            f"{len(dn)} do_nothing row(s)", "exactly one")
    if dn:
        R.check("do_nothing has an EIV of exactly zero", dn[0]["predicted_eiv"] == 0.0,
                f"{dn[0]['predicted_eiv']!r}", "exactly 0.0, by construction")

    eivs = [r["predicted_eiv"] for r in ranked]
    R.check("ranking is descending by EIV", eivs == sorted(eivs, reverse=True),
            f"first={eivs[0]:.2f} last={eivs[-1]:.2f}, monotonic="
            f"{eivs == sorted(eivs, reverse=True)}",
            "non-increasing")
    R.check("candidate count is within the declared ceiling",
            res["candidate_count"] <= ocfg.MAX_CANDIDATES,
            f"{res['candidate_count']} <= {ocfg.MAX_CANDIDATES}",
            f"at most MAX_CANDIDATES ({ocfg.MAX_CANDIDATES})")

    sel = {r.get("selected") for r in ranked}
    R.check("the optimizer never marks a candidate selected",
            sel <= {0, None},
            f"selected values in ranked rows: {sel}",
            "0 or unset -- only the rule engine may grant selection")

    R.sub("latency, measured not hidden")
    for oid2 in [r[0] for r in conn.execute(
            "SELECT opportunity_id FROM opportunities "
            "WHERE status IN ('open','recovering') LIMIT 3").fetchall()]:
        optimize.optimize_opportunity(conn, oid2, persist=False)   # warm
    times = []
    for oid2 in [r[0] for r in conn.execute(
            "SELECT opportunity_id FROM opportunities "
            "WHERE status IN ('open','recovering') LIMIT 12").fetchall()]:
        t0 = time.perf_counter()
        optimize.optimize_opportunity(conn, oid2, persist=False)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p50 = times[len(times) // 2]
    p95 = times[int(0.95 * (len(times) - 1))]
    R.disclosed("optimizer latency against the declared budget",
                f"p50 {p50:.1f}ms, p95 {p95:.1f}ms over {len(times)} opportunities",
                f"p95 <= {LATENCY_BUDGET_MS}ms",
                "BUDGET NOT MET, known since Phase 4 and unresolved.\n"
                "~99.7% is single-row model inference through ml/inference.py,\n"
                "a frozen Phase 3 module. Batching was measured at 6.6x but\n"
                "requires touching it and re-running Phase 3's parity gate.\n"
                "This is why the optimizer stays OFF at both request-synchronous\n"
                "entry points (see Phase 5 section).")
    return oid


# --------------------------------------------------------------------------
# PHASE 5
# --------------------------------------------------------------------------

def phase5(conn, tmp):
    R.section("PHASE 5 -- rule engine and bounded executor")

    from backend.engine import phase5_config as cfg
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action
    from backend.engine.execute_action import STATUS_MAP, execute_action

    def opp(oid):
        return dict(conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())

    def decide(o, ranked=None, **kw):
        return decide_action(o, classify(o["event_type"], o.get("root_cause")),
                             conn, ranked_candidates=ranked, **kw)

    ids = [r[0] for r in conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE status IN ('open','recovering')"
    ).fetchall()]

    R.sub("declared vocabulary")
    R.check("executable actions match the executor", set(STATUS_MAP) == set(cfg.EXECUTABLE_ACTIONS),
            f"STATUS_MAP={sorted(STATUS_MAP)}",
            f"equal to EXECUTABLE_ACTIONS={sorted(cfg.EXECUTABLE_ACTIONS)}")
    R.check("payment_link is dispatchable", "payment_link" in STATUS_MAP,
            f"payment_link in STATUS_MAP -> {'payment_link' in STATUS_MAP}",
            "present (added in W5 per EXECUTION_PLAN.md:206)")
    R.check("method change is declared non-executable",
            cfg.METHOD_CHANGE_IS_EXECUTABLE is False,
            f"METHOD_CHANGE_IS_EXECUTABLE={cfg.METHOD_CHANGE_IS_EXECUTABLE}",
            "False, permanently")

    R.sub("decide_action without a ranked list (pre-optimizer behaviour)")
    conn.execute("UPDATE opportunities SET created_at=?, status='open'"
                 " WHERE opportunity_id=?", (at_local_hour(12), ids[0]))
    conn.commit()
    o = opp(ids[0])
    base = decide(o)
    R.check("a decision is produced with the closed vocabulary",
            base["outcome"] in ("executed", "blocked_cooldown", "blocked_contact_hours",
                                "blocked_already_escalated", "blocked_already_stopped",
                                "flagged_manual_review"),
            f"{ids[0]}: action={base['action_type']} allowed={base['allowed']} "
            f"outcome={base['outcome']}",
            "outcome inside DECISION_OUTCOMES")
    R.check("no decision carries a payment method", "method" not in base,
            f"keys={sorted(base)}", "no 'method' key on any branch")

    R.sub("fallthrough: blocked top candidate -> next compliant candidate")
    # An opportunity created outside 9am-8pm blocks contact actions but not
    # escalation, which is the one action-dependent compliance rule.
    conn.execute("UPDATE opportunities SET created_at=? WHERE opportunity_id=?",
                 (at_local_hour(3), ids[1]))
    conn.commit()
    o2 = opp(ids[1])
    hard = decide(o2)
    ranked = [
        {"action_type": "reminder", "rank": 1, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 90.0, "eiv_confidence": "low", "eiv_gap_to_next": 0.004},
        {"action_type": "escalate", "rank": 2, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 40.0},
    ]
    fell = decide(o2, ranked=ranked)
    R.check("a contact-hours block falls through to escalation",
            hard["outcome"] == "blocked_contact_hours" and fell["action_type"] == "escalate",
            f"hardcoded -> {hard['action_type']}/{hard['outcome']}; "
            f"with ranked list -> {fell['action_type']}/{fell['outcome']}",
            "hardcoded blocks the reminder; ranked list reaches escalate")
    R.check("the fallthrough is disclosed in the reasoning",
            "contact window" in fell["reasoning"],
            f"reasoning: {fell['reasoning'][:110]}...",
            "names what was skipped and why")

    R.sub("method_change is evaluable but NEVER executable")
    conn.execute("UPDATE opportunities SET created_at=?, root_cause='expired_card',"
                 " event_type='payment_failed', status='open'"
                 " WHERE opportunity_id=?", (at_local_hour(12), ids[2]))
    conn.commit()
    o3 = opp(ids[2])
    cur = conn.execute("SELECT method FROM payments WHERE opportunity_id=?"
                       " ORDER BY created_at DESC LIMIT 1", (ids[2],)).fetchone()
    cur_method = cur[0] if cur else "card"
    mc_ranked = [
        {"action_type": "retry", "rank": 1, "timing": "immediate", "timing_hours": 0.0,
         "method": "upi" if cur_method != "upi" else "card", "channel": "n/a",
         "method_changed": True, "predicted_eiv": 999.0},
        {"action_type": "reminder", "rank": 2, "timing": "immediate",
         "timing_hours": 0.0, "method": "n/a", "channel": "email",
         "predicted_eiv": 10.0},
    ]
    lp = conn.execute("SELECT * FROM payments WHERE opportunity_id=? "
                      "ORDER BY created_at DESC LIMIT 1", (ids[2],)).fetchone()
    mc = decide(o3, ranked=mc_ranked, latest_payment=dict(lp) if lp else None)
    # The bound is that no APPROVED decision is the method change. A blocked
    # decision echoes the hardcoded default_action ("retry") without approving
    # anything, so action_type alone would be ambiguous.
    approved_method_change = mc["allowed"] and mc["action_type"] == "retry"
    R.check("a top-ranked method change is never approved",
            not approved_method_change,
            f"current method={cur_method}; rank 1 was a method-change retry "
            f"(EIV 999.0); result: action={mc['action_type']!r} "
            f"allowed={mc['allowed']} outcome={mc['outcome']}",
            "no approved retry on a different method -- rank is irrelevant here")
    R.check("the recommendation is visible in the reasoning",
            "payment-method change" in mc["reasoning"],
            f"reasoning: {mc['reasoning'][:110]}...",
            "recorded as considered-and-refused, not silently dropped")

    R.sub("the runtime disable switch actually disables, mid-run")
    conn.execute("UPDATE opportunities SET created_at=?, status='open'"
                 " WHERE opportunity_id=?", (at_local_hour(12), ids[3]))
    conn.commit()
    o4 = opp(ids[3])
    dn_first = [{"action_type": "do_nothing", "rank": 1, "timing": "immediate",
                 "timing_hours": 0.0, "method": None, "channel": None,
                 "predicted_eiv": 50.0, "candidate_id": 90001},
                {"action_type": "reminder", "rank": 2, "timing": "immediate",
                 "timing_hours": 0.0, "method": "n/a", "channel": "email",
                 "predicted_eiv": 20.0, "candidate_id": 90002}]
    on = decide(o4, ranked=dn_first)
    cfg.OPTIMIZER_PATHWAY_ENABLED = False
    off = decide(o4, ranked=dn_first)
    plain = decide(o4)
    cfg.OPTIMIZER_PATHWAY_ENABLED = True
    back = decide(o4, ranked=dn_first)
    R.check("flipping the switch reverts to pre-optimizer behaviour",
            off == plain and off != on and back == on,
            f"ON -> {on['action_type']} (candidate_id={on.get('candidate_id')}); "
            f"OFF -> {off['action_type']} (identical to no-list: {off == plain}); "
            f"back ON -> {back['action_type']}",
            "OFF is byte-identical to passing no list at all")
    R.check("the entry-point table keeps the optimizer off by default",
            not any(cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT.values()),
            str(cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT),
            "all False -- the latency budget is unmet")

    R.sub("execute_action: lifecycle, scheduling, and what must NOT be written")
    o5 = opp(ids[4])
    cur_ex = conn.execute("SELECT COUNT(*) FROM recovery_executions").fetchone()[0]
    execute_action(o5, {"action_type": "do_nothing", "allowed": True,
                        "outcome": "executed", "reasoning": "verify",
                        "triggered_by": "rule"}, conn)
    after_ex = conn.execute("SELECT COUNT(*) FROM recovery_executions").fetchone()[0]
    R.check("a do_nothing decision writes ZERO execution rows",
            after_ex == cur_ex,
            f"execution rows before={cur_ex} after={after_ex} (delta {after_ex-cur_ex})",
            "delta 0 -- deciding not to act is a decision, not an execution")

    o6 = opp(ids[5])
    cid = conn.execute(
        "INSERT INTO recovery_candidates (opportunity_id, action_type, timing,"
        " method, channel, predicted_eiv, rank, selected, created_at)"
        " VALUES (?, 'payment_link', '24h', 'n/a', 'email', 5.0, 1, 0, ?)",
        (ids[5], int(time.time()))).lastrowid
    conn.commit()
    execute_action(o6, {"action_type": "payment_link", "allowed": True,
                        "outcome": "executed", "reasoning": "verify",
                        "triggered_by": "rule", "candidate_id": cid}, conn)
    row = dict(conn.execute(
        "SELECT e.state, e.scheduled_for, e.executed_at, d.candidate_id"
        " FROM recovery_executions e JOIN recovery_decisions d"
        " ON d.decision_id = e.decision_id WHERE d.candidate_id = ?",
        (cid,)).fetchone())
    R.check("payment_link dispatches and a 24h candidate is SCHEDULED, not executed",
            row["state"] == "scheduled" and row["executed_at"] is None
            and row["scheduled_for"] is not None,
            f"state={row['state']} scheduled_for={row['scheduled_for']} "
            f"executed_at={row['executed_at']} candidate_id={row['candidate_id']}",
            "state='scheduled', scheduled_for set, executed_at NULL")
    sel = conn.execute("SELECT selected FROM recovery_candidates WHERE candidate_id=?",
                       (cid,)).fetchone()[0]
    R.check("the approved candidate is marked selected=1", sel == 1,
            f"selected={sel}", "1 -- set by the rule engine, never the optimizer")

    R.sub("full pipeline on a live opportunity (entry point: trigger_event)")
    from backend.engine.trigger_event import trigger_event
    out = trigger_event(event_type="payment_failed", amount=54321, conn=conn,
                        root_cause="gateway_timeout",
                        event_id=f"verify-{int(time.time())}")
    R.check("classify -> decide -> execute runs end to end",
            out.get("status") not in (None, "invalid_event_type")
            and out.get("decision") is not None,
            f"status={out.get('status')} "
            f"opportunity={(out.get('opportunity') or {}).get('opportunity_id')} "
            f"action={(out.get('decision') or {}).get('action_type')} "
            f"outcome={(out.get('decision') or {}).get('outcome')} "
            f"(the outcome depends on the wall-clock hour you run this at -- "
            f"contact outside 9am-8pm is correctly blocked)",
            "an opportunity is created and a logged decision comes back")

    total_d = conn.execute("SELECT COUNT(*) FROM recovery_decisions").fetchone()[0]
    silent = conn.execute(
        "SELECT COUNT(*) FROM recovery_decisions WHERE reasoning IS NULL"
        " OR reasoning = ''").fetchone()[0]
    R.check("no decision is ever silent", silent == 0,
            f"{total_d} decisions logged, {silent} without a reason",
            "0 without a reason")


# --------------------------------------------------------------------------
# Known gaps
# --------------------------------------------------------------------------

def known_gaps():
    R.section("KNOWN, DISCLOSED GAPS -- printed so they are not mistaken for news")

    R.sub(f"the {len(KNOWN_TEST_FAILURES)} pre-existing pytest failures, by name")
    print("     These were present at the Phase 5 W0 baseline (git 866e478) and")
    print("     have not changed since. If `pytest` shows exactly these, nothing")
    print("     new is broken. Anything else is new.\n")
    for i, name in enumerate(KNOWN_TEST_FAILURES, 1):
        print(f"       {i:>2}. {name}")
    R.disclosed("pre-existing pytest failures",
                f"{len(KNOWN_TEST_FAILURES)} known failures, listed above",
                "0 in an ideal world",
                "14 pre-date Phase 4; 2 are Phase 4's disclosed pair (the retracted\n"
                "G7 test and the latency budget). Run `python -m pytest -q` from\n"
                "backend/ to compare against this list.")

    R.disclosed("method_change is evaluable but never executable",
                "no action type exists for it; a method change is a retry whose\n"
                "method differs from the opportunity's current one",
                "structurally absent from the executor",
                "Proof is inline above: a rank-1 method change with EIV 999.0 is\n"
                "refused and the next compliant candidate is taken instead. The\n"
                "decision dict carries no 'method' key on any branch, so the\n"
                "executor has no field for one to ride on. SoT.md:63 was amended\n"
                "in Phase 5 to stop listing it as dispatchable.")

    R.disclosed("execute_action() is not idempotent at the call level",
                "calling it twice with one decision writes 2 decisions, 2 executions",
                "ideally 1 and 1",
                "The UNIQUE index only prevents two executions per decision, which\n"
                "IS the guarantee W6's sweep needs (it updates an existing row).\n"
                "If W6 is ever built by re-calling execute_action(), an idempotency\n"
                "key becomes mandatory. PHASE5_NOTES.md section 1c.")


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
    print("  This run writes only to a temp directory and touches no git state.")

    tmp = Path(tempfile.mkdtemp(prefix="verify_phases_"))
    started = time.perf_counter()
    conn = None
    try:
        conn, db_module = build_temp_world(tmp)
        print(f"  tempdir: {tmp}")
        phase01(conn, db_module, tmp)
        phase2(args.quick)
        phase3(conn)
        phase4(conn)
        phase5(conn, tmp)
        known_gaps()
    except Exception:
        print("\n" + "!" * 78)
        print("!!  THE VERIFICATION SCRIPT ITSELF CRASHED -- this is an unexpected result")
        print("!" * 78)
        traceback.print_exc()
        R.check("verification script ran to completion", False,
                "crashed, see traceback above", "ran to completion")
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  elapsed: {time.perf_counter() - started:.1f}s")
    unexpected = R.summary()
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
