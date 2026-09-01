"""
Phase 0 exit gates -- "Bootstrap & Repository Truth".

Each test maps to a numbered gate in the acceptance document. The theme of
Phase 0 is that the repository can be rebuilt from a clean checkout and
produce the same artifacts, so most of these tests re-run a bootstrap step
in a temp directory rather than inspecting the checked-in output: the gate
is about the *procedure* being reproducible, not about a file happening to
be present.
"""

import hashlib
import re
import warnings
from importlib.metadata import PackageNotFoundError, version

import pytest

from backend.tests.conftest import BACKEND_DIR, FIXED_NOW

# Distribution names differ from requirement lines that carry extras.
_REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(?P<version>[^\s#]+)")


def _parse_requirements(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _REQ_LINE.match(line)
        if m:
            out[m.group("name")] = m.group("version")
    return out


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Gate: dependency install in a fresh venv / pinned versions
# --------------------------------------------------------------------------

@pytest.mark.gate("phase0.dependencies")
def test_every_runtime_requirement_is_pinned_exactly():
    """
    A `>=` or unpinned line makes "install succeeds in a fresh venv" a claim
    about today's PyPI, not about this repository. Two of these pins
    (scikit-learn, numpy) are additionally load-bearing for unpickling the
    shipped .joblib artifacts.
    """
    path = BACKEND_DIR / "requirements.txt"
    unpinned = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not _REQ_LINE.match(s):
            unpinned.append(s)
    assert not unpinned, "requirement lines without an exact `==` pin:\n" + "\n".join(unpinned)


@pytest.mark.gate("phase0.dependencies")
def test_installed_versions_match_the_pins():
    """
    The environment this suite is running in must be the pinned one --
    otherwise a green model-loading gate below proves nothing about the
    environment the pins describe.
    """
    mismatches, missing = [], []
    for name, pinned in _parse_requirements(BACKEND_DIR / "requirements.txt").items():
        try:
            installed = version(name)
        except PackageNotFoundError:
            missing.append(name)
            continue
        if installed != pinned:
            mismatches.append(f"{name}: pinned {pinned}, installed {installed}")
    assert not missing, f"pinned packages not installed: {missing}"
    assert not mismatches, "environment does not match requirements.txt:\n" + "\n".join(mismatches)


@pytest.mark.gate("phase0.dependencies")
def test_test_tooling_is_not_mixed_into_runtime_requirements():
    """Test tools must not enter the runtime resolution graph."""
    runtime = _parse_requirements(BACKEND_DIR / "requirements.txt")
    dev = _parse_requirements(BACKEND_DIR / "requirements-dev.txt")
    assert "pytest" in dev, "requirements-dev.txt does not pin pytest"
    assert not (set(runtime) & set(dev)), \
        f"packages present in both files: {sorted(set(runtime) & set(dev))}"


# --------------------------------------------------------------------------
# Gate: seed generation is byte-identical with the same seed + fixed clock
# --------------------------------------------------------------------------

def _generate_into(target, monkeypatch, now=FIXED_NOW):
    from backend.data import generate_seed_data as gsd

    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gsd, "DATA_DIR", target)
    monkeypatch.setenv("SEED_DATA_NOW", str(now))
    gsd.main()
    return {p.name: _sha256(p) for p in sorted(target.iterdir()) if p.is_file()}


@pytest.mark.gate("phase0.seed_determinism")
def test_seed_generation_is_byte_identical_across_runs(tmp_path, monkeypatch):
    first = _generate_into(tmp_path / "run_a", monkeypatch)
    second = _generate_into(tmp_path / "run_b", monkeypatch)
    assert first and second, "generator emitted no files"
    assert set(first) == set(second), \
        f"different file sets: {sorted(first)} vs {sorted(second)}"
    differing = {k for k in first if first[k] != second[k]}
    assert not differing, f"non-deterministic output files: {sorted(differing)}"


@pytest.mark.gate("phase0.seed_determinism")
def test_seed_output_actually_depends_on_the_clock_override(tmp_path, monkeypatch):
    """
    Counter-test for the one above. If the generator ignored SEED_DATA_NOW,
    the determinism gate would pass for the wrong reason and would not
    protect the "same seed + fixed clock" property at all -- so prove the
    clock is a real input by moving it and observing a change.
    """
    baseline = _generate_into(tmp_path / "at_t0", monkeypatch)
    shifted = _generate_into(tmp_path / "at_t1", monkeypatch, now=FIXED_NOW + 30 * 86400)
    assert any(baseline[k] != shifted[k] for k in baseline), \
        "SEED_DATA_NOW has no effect on output; the fixed-clock gate is vacuous"


@pytest.mark.gate("phase0.seed_determinism")
def test_seed_volumes_match_the_declared_contract(seed_data_dir):
    import json

    from backend.data.generate_seed_data import (N_CUSTOMERS, N_MERCHANTS,
                                                 N_OPPORTUNITIES)

    def load(name):
        return json.loads((seed_data_dir / name).read_text(encoding="utf-8"))

    merchants, customers = load("merchants.json"), load("customers.json")
    opportunities, payments = load("opportunities.json"), load("payments.json")

    assert len(merchants) == N_MERCHANTS
    assert len(customers) == N_CUSTOMERS
    assert len(opportunities) == N_OPPORTUNITIES

    per_opp = {}
    for p in payments:
        per_opp[p["opportunity_id"]] = per_opp.get(p["opportunity_id"], 0) + 1
    assert max(per_opp.values()) > 1, (
        "no opportunity has more than one payment attempt, so the seed data "
        "cannot exercise the Phase 1 multi-attempt aggregation gate")


# --------------------------------------------------------------------------
# Gate: database builds from scratch, row counts and FKs valid
# --------------------------------------------------------------------------

@pytest.mark.gate("phase0.database_bootstrap")
def test_database_row_counts_match_the_seed_files(seeded_db, seed_data_dir):
    import json

    for table, filename in (("merchants", "merchants.json"),
                            ("customers", "customers.json"),
                            ("opportunities", "opportunities.json"),
                            ("payments", "payments.json")):
        expected = len(json.loads((seed_data_dir / filename).read_text(encoding="utf-8")))
        actual = seeded_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert actual == expected, f"{table}: loaded {actual}, seed file has {expected}"


@pytest.mark.gate("phase0.database_bootstrap")
def test_no_foreign_key_violations_after_load(seeded_db):
    """
    get_connection() enables `PRAGMA foreign_keys = ON`, but SQLite only
    enforces it for statements issued on that connection -- it says nothing
    about rows already present. foreign_key_check audits the stored rows.
    """
    violations = seeded_db.execute("PRAGMA foreign_key_check").fetchall()
    assert not violations, f"foreign key violations: {[tuple(v) for v in violations]}"


@pytest.mark.gate("phase0.database_bootstrap")
def test_every_payment_resolves_to_an_opportunity(seeded_db):
    orphans = seeded_db.execute(
        "SELECT p.id FROM payments p LEFT JOIN opportunities o "
        "ON p.opportunity_id = o.opportunity_id WHERE o.opportunity_id IS NULL"
    ).fetchall()
    assert not orphans, f"payments with no owning opportunity: {[r[0] for r in orphans]}"


@pytest.mark.gate("phase0.database_bootstrap")
def test_opportunity_fk_columns_resolve_when_present(seeded_db):
    dangling_merchant = seeded_db.execute(
        "SELECT o.opportunity_id FROM opportunities o LEFT JOIN merchants m "
        "ON o.merchant_id = m.merchant_id "
        "WHERE o.merchant_id IS NOT NULL AND m.merchant_id IS NULL"
    ).fetchall()
    dangling_customer = seeded_db.execute(
        "SELECT o.opportunity_id FROM opportunities o LEFT JOIN customers c "
        "ON o.customer_id = c.customer_id "
        "WHERE o.customer_id IS NOT NULL AND c.customer_id IS NULL"
    ).fetchall()
    assert not dangling_merchant, f"dangling merchant_id: {[r[0] for r in dangling_merchant]}"
    assert not dangling_customer, f"dangling customer_id: {[r[0] for r in dangling_customer]}"


# --------------------------------------------------------------------------
# Gate: shipped models load and score under the pinned environment
# --------------------------------------------------------------------------

MODEL_FILES = ("xgb_model.joblib", "lr_model.joblib")


@pytest.mark.gate("phase0.models_load")
@pytest.mark.parametrize("model_file", MODEL_FILES)
def test_shipped_model_loads_without_version_warnings(model_file):
    """
    The models are shipped as pickles and are NOT retrained during bootstrap,
    so an unpickling version warning is the early signal that the artifact
    and the installed scikit-learn have diverged. Warnings are captured
    explicitly here because pytest.ini silences DeprecationWarning globally.
    """
    import joblib

    path = BACKEND_DIR / "ml" / "models" / model_file
    assert path.exists(), f"shipped model missing: {path}"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = joblib.load(path)

    assert hasattr(model, "predict_proba"), f"{model_file} exposes no predict_proba"
    version_warnings = [str(w.message) for w in caught
                        if "version" in str(w.message).lower()]
    assert not version_warnings, (
        f"{model_file} unpickled with version warnings, meaning the artifact "
        f"was produced by a different library version than the pins install:\n"
        + "\n".join(version_warnings))


@pytest.mark.gate("phase0.train_serve_parity")
@pytest.mark.parametrize("model_file", MODEL_FILES)
def test_model_expects_exactly_the_declared_feature_contract(model_file):
    """
    Static half of train/serve parity: the fitted pipeline's input columns
    must equal the contract declared in train_risk_model.py, which is the
    same list decide_action.py builds at serve time.
    """
    import joblib

    from backend.ml.train_risk_model import (CATEGORICAL_FEATURES,
                                             NUMERIC_FEATURES)

    model = joblib.load(BACKEND_DIR / "ml" / "models" / model_file)
    declared = set(CATEGORICAL_FEATURES) | set(NUMERIC_FEATURES)

    seen = getattr(model, "feature_names_in_", None)
    if seen is None and hasattr(model, "__getitem__"):
        seen = getattr(model[0], "feature_names_in_", None)
    if seen is None:
        pytest.skip(f"{model_file} does not expose feature_names_in_")

    assert set(seen) == declared, (
        f"feature contract drift in {model_file}:\n"
        f"  model expects but contract omits: {sorted(set(seen) - declared)}\n"
        f"  contract declares but model lacks: {sorted(declared - set(seen))}")


@pytest.mark.gate("phase0.train_serve_parity")
def test_decide_action_produces_a_real_ml_probability(seeded_db):
    """
    Runtime half of train/serve parity, and the only externally visible
    symptom the current code offers.

    `_get_recovery_probability` catches every exception and returns None, so
    a missing artifact, a version-incompatible pickle, and a renamed feature
    column are all indistinguishable from "no signal" at runtime. If this
    assertion fails, run the model-loading tests above first -- they
    localise the cause that this one can only detect.
    """
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action
    from backend.tests.conftest import make_opportunity

    opp = make_opportunity(seeded_db, opportunity_id="opp_ml_parity_0001")
    classification = classify(opp["event_type"], opp["root_cause"])
    decision = decide_action(opp, classification, seeded_db)

    assert decision["allowed"] is True, \
        f"fixture did not reach a scoring branch: {decision['outcome']}"
    assert "ml_recovery_probability" in decision, \
        "allowed decision carries no ml_recovery_probability key at all"

    proba = decision["ml_recovery_probability"]
    assert proba is not None, (
        "ml_recovery_probability is None on an allowed decision. The model "
        "failed to load or scoring raised, and the exception was discarded "
        "inside engine/decide_action.py without a log line.")
    assert 0.0 <= proba <= 1.0, f"probability out of range: {proba}"


# --------------------------------------------------------------------------
# Gate: batch pipeline runs end-to-end
# --------------------------------------------------------------------------

@pytest.mark.gate("phase0.batch_pipeline")
def test_batch_pipeline_processes_the_seeded_database(seeded_db):
    from backend.db.db import DECISION_OUTCOMES
    from backend.engine.core_loop import run_cycle

    eligible = seeded_db.execute(
        "SELECT COUNT(*) FROM opportunities WHERE status IN ('open','recovering')"
    ).fetchone()[0]
    assert eligible > 0, "seed data leaves the batch loop nothing to process"

    results = run_cycle()
    assert len(results) == eligible, \
        f"processed {len(results)} of {eligible} eligible opportunities"

    outcomes = {r["outcome"] for r in results}
    unknown = outcomes - set(DECISION_OUTCOMES)
    assert not unknown, f"outcomes outside the closed vocabulary: {sorted(unknown)}"

    decisions = seeded_db.execute("SELECT COUNT(*) FROM recovery_decisions").fetchone()[0]
    assert decisions == len(results), \
        f"{len(results)} decisions returned but {decisions} rows persisted"


@pytest.mark.gate("phase0.batch_pipeline")
def test_batch_pipeline_leaves_decision_and_execution_tables_consistent(seeded_db):
    """
    Structural, not distributional. Branch *coverage* is asserted against
    synthetic fixtures in test_compliance_regression.py, because the seed
    clock is pinned to a fixed past instant while decide_action() reads the
    real system clock -- so which branch the seeded rows land in depends on
    how long ago FIXED_NOW was, which is not a property worth asserting.
    """
    from backend.engine.core_loop import run_cycle
    from backend.engine.execute_action import STATUS_MAP

    run_cycle()

    rows = [dict(r) for r in seeded_db.execute(
        "SELECT d.decision_id, d.opportunity_id, d.action_type, d.outcome, "
        "o.status, (SELECT COUNT(*) FROM recovery_executions e "
        "           WHERE e.decision_id = d.decision_id) AS n_exec "
        "FROM recovery_decisions d JOIN opportunities o "
        "ON o.opportunity_id = d.opportunity_id"
    ).fetchall()]
    assert rows, "no decisions persisted"

    bad_exec = [r for r in rows
                if r["n_exec"] != (1 if r["outcome"] == "executed" else 0)]
    assert not bad_exec, (
        "execution rows do not match executed decisions 1:1 -- a blocked "
        f"decision must produce none: {bad_exec[:5]}")

    bad_status = [r for r in rows
                  if r["outcome"] == "executed"
                  and r["status"] != STATUS_MAP.get(r["action_type"], r["status"])]
    assert not bad_status, f"opportunity status not advanced per STATUS_MAP: {bad_status[:5]}"


# --------------------------------------------------------------------------
# Gate: API starts and serves the documented endpoints
# --------------------------------------------------------------------------

@pytest.mark.gate("phase0.api")
def test_cases_endpoint_returns_the_seeded_opportunities(api_client, seeded_db):
    response = api_client.get("/api/cases")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list) and payload, "no cases returned"
    assert "opportunity_id" in payload[0], \
        f"case objects are not opportunity-addressed: {sorted(payload[0])}"


@pytest.mark.gate("phase0.api")
def test_case_detail_endpoint_is_opportunity_addressed(api_client, seeded_db):
    opportunity_id = seeded_db.execute(
        "SELECT opportunity_id FROM opportunities LIMIT 1").fetchone()[0]

    ok = api_client.get(f"/api/cases/{opportunity_id}")
    assert ok.status_code == 200, ok.text
    detail = ok.json()
    for key in ("payments", "recovery_decisions", "recovery_executions", "messages"):
        assert key in detail, f"detail response missing {key!r}: {sorted(detail)}"

    missing = api_client.get("/api/cases/opp_does_not_exist_9999")
    assert missing.status_code == 404, \
        f"unknown opportunity returned {missing.status_code}, not 404"


@pytest.mark.gate("phase0.api")
def test_metrics_endpoint_is_internally_consistent(api_client, seeded_db):
    response = api_client.get("/api/metrics")
    assert response.status_code == 200, response.text
    m = response.json()

    for key in ("overall_recovery_rate_pct", "recovery_by_root_cause",
                "amount_recovered", "amount_at_risk_total",
                "recovery_value_pct", "current_amount_exposed",
                "unresolved_exceptions_count",
                "unresolved_exceptions_by_flag_type",
                "time_to_recovery_distribution"):
        assert key in m, f"/api/metrics missing {key!r}; returned {sorted(m)}"

    def scalar(sql):
        return seeded_db.execute(sql).fetchone()[0]

    total = scalar("SELECT COUNT(*) FROM opportunities")
    recovered = scalar("SELECT COUNT(*) FROM opportunities WHERE status='recovered'")
    assert m["overall_recovery_rate_pct"] == (
        round(recovered / total * 100, 1) if total else 0)
    assert m["amount_recovered"] == scalar(
        "SELECT COALESCE(SUM(partial_recovery_amount),0) FROM opportunities "
        "WHERE status='recovered'")
    assert m["current_amount_exposed"] == scalar(
        "SELECT COALESCE(SUM(amount_at_risk),0) FROM opportunities "
        "WHERE status != 'recovered'")


@pytest.mark.gate("phase0.api")
def test_amount_at_risk_total_still_carries_its_documented_phase8_semantics(api_client,
                                                                           seeded_db):
    """
    Not a defect test -- a pin. `amount_at_risk_total` deliberately sums ALL
    opportunities including already-recovered ones, which is the mislabeling
    Phase 8 is scoped to fix. Pinning the current semantics means Phase 8
    has to change this test on purpose, and means nobody quietly "fixes" the
    number before the metric it feeds has been redefined.
    """
    m = api_client.get("/api/metrics").json()
    all_opportunities = seeded_db.execute(
        "SELECT COALESCE(SUM(amount_at_risk),0) FROM opportunities").fetchone()[0]
    unrecovered_only = seeded_db.execute(
        "SELECT COALESCE(SUM(amount_at_risk),0) FROM opportunities "
        "WHERE status != 'recovered'").fetchone()[0]

    assert m["amount_at_risk_total"] == all_opportunities
    if all_opportunities != unrecovered_only:
        assert m["amount_at_risk_total"] != unrecovered_only, (
            "amount_at_risk_total now excludes recovered opportunities. That "
            "is the Phase 8 change; update this pin and the metric's label "
            "together.")
