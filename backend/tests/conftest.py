"""
Shared fixtures + the gate-evidence recorder.

Two hard rules this file enforces for the whole suite:

1. **No test ever touches backend/db/recovery.db.** Every fixture builds a
   throwaway SQLite file under pytest's tmp_path and monkeypatches
   `backend.db.db.DB_PATH` to point at it. `get_connection()` reads that
   module global at call time, so patching it redirects every consumer --
   engine modules, api modules, and the FastAPI app alike -- without any of
   them needing a test-aware code path.

2. **Every test declares which acceptance gate it is evidence for**, via
   `@pytest.mark.gate("phase1.state_separation")`. The session hook at the
   bottom writes those outcomes to tests/evidence/, because the gate
   document's rule is that a gate marked passed with no independently
   re-checkable artifact is not passed -- it is unverified.
"""

import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"

# Fixed clock anchor. Any test that needs byte-identical seed output sets
# SEED_DATA_NOW to this, so `created_at` stops drifting with wall clock.
FIXED_NOW = 1_756_000_000  # 2025-08-24T02:26:40Z, arbitrary but committed


@pytest.fixture(scope="session")
def backend_dir() -> Path:
    return BACKEND_DIR


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def source_files() -> list[Path]:
    """Every first-party .py file, excluding the test suite itself."""
    out = []
    for p in sorted(BACKEND_DIR.rglob("*.py")):
        parts = set(p.parts)
        if "__pycache__" in parts or "tests" in parts or ".venv" in parts:
            continue
        out.append(p)
    return out


# --------------------------------------------------------------------------
# Database isolation
# --------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    """
    Redirect the whole application at a throwaway DB file.

    Patches the module global rather than the imported name, because
    `get_connection()` resolves DB_PATH at call time -- so api/server.py's
    `from backend.db.db import get_connection` picks this up too.
    """
    from backend.db import db as db_module

    p = tmp_path / "test_recovery.db"
    monkeypatch.setattr(db_module, "DB_PATH", p)
    return p


@pytest.fixture
def empty_db(db_path):
    """Schema applied, no rows. Returns an open connection."""
    from backend.db.db import create_schema, get_connection

    conn = get_connection()
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def seed_data_dir(tmp_path, monkeypatch) -> Path:
    """
    Generate a fresh seed dataset into a temp directory with the clock
    pinned, and point both the generator and the DB loader at it.
    """
    from backend.data import generate_seed_data as gsd
    from backend.db import db as db_module

    out = tmp_path / "data"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gsd, "DATA_DIR", out)
    monkeypatch.setattr(db_module, "DATA_DIR", out)
    monkeypatch.setenv("SEED_DATA_NOW", str(FIXED_NOW))
    gsd.main()
    return out


@pytest.fixture
def seeded_db(db_path, seed_data_dir):
    """
    The full Phase 0 bootstrap end state: schema created from DDL, seed
    JSON loaded. This is the fixture most Phase 1 gates run against.
    """
    from backend.db.db import (create_schema, get_connection, load_customers,
                               load_merchants, load_opportunities,
                               load_payments)

    conn = get_connection()
    create_schema(conn)
    load_merchants(conn)
    load_customers(conn)
    load_opportunities(conn)
    load_payments(conn)
    yield conn
    conn.close()


@pytest.fixture
def api_client(seeded_db):
    """
    FastAPI TestClient bound to the temp DB. Avoids needing a live uvicorn
    process, and avoids the real recovery.db entirely.
    """
    from fastapi.testclient import TestClient

    from backend.api.server import app

    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------
# Network isolation for the LLM boundary
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def stub_llm_boundary(request, monkeypatch):
    """
    Replace the two Gemini entry points with deterministic stubs for every
    test, unless the test is marked `requires_network`.

    Three reasons this is autouse rather than opt-in:

    1. `run_cycle()` calls deliver_recovery_message() for every eligible
       opportunity, which calls the live API once per case. A 150-case seed
       set would mean 150 real requests per invocation of a gate test.
    2. A gate that fails because a rate limit was hit, or because the key in
       backend/.env has been rotated, is a false failure -- it says nothing
       about whether Phase 0 or Phase 1 is complete.
    3. The suite must be runnable offline and by a reviewer who does not hold
       the key, which is what "independently re-checkable" requires.

    Both stubs return the *shape* the real functions promise, including the
    `status` field, so nothing downstream is tested against a looser contract
    than production sees. They patch the names as imported by the consumer
    modules, since both use `from ... import name`.
    """
    if request.node.get_closest_marker("requires_network"):
        return

    def fake_generate(payment, classification, action_type):
        return {"message": f"[stub {action_type}] amount={payment.get('amount')}",
                "status": "ok"}

    def fake_parse(customer_message, conversation_history=None, event_type=None):
        return {"intent": "unclear", "confidence": 0.0,
                "mentioned_reason": None, "extracted_detail": None}

    from backend.engine import deliver_message
    monkeypatch.setattr(deliver_message, "generate_recovery_message", fake_generate)

    # handle_customer_reply is imported lazily by some tests; patch it only if
    # importing it does not itself require network configuration.
    try:
        from backend.engine import handle_customer_reply
    except Exception:
        return
    if hasattr(handle_customer_reply, "parse_reply_intent"):
        monkeypatch.setattr(handle_customer_reply, "parse_reply_intent", fake_parse)


# --------------------------------------------------------------------------
# Row builders. Deliberately explicit rather than helper-heavy: a fixture
# that quietly fills in a field is how a schema regression hides.
# --------------------------------------------------------------------------

DAY = 86400


def recent_in_window_ts(days_ago: int = 1, hour: int = 12) -> int:
    """
    A timestamp that is both recent enough to avoid decide_action()'s
    7-day auto-escalate branch and inside the 9am-8pm contact window.

    Derived from the real clock rather than hard-coded, because
    decide_action() compares against `time.time()` while the contact-window
    check reads the *local* hour of `created_at`. A fixed literal would make
    which branch a fixture lands in depend on how old this test file is.
    """
    from datetime import timedelta

    anchor = (datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
              - timedelta(days=days_ago))
    return int(anchor.timestamp())


def outside_window_ts(days_ago: int = 1, hour: int = 3) -> int:
    """Recent, but at an hour outside the permitted contact window."""
    return recent_in_window_ts(days_ago=days_ago, hour=hour)


def make_opportunity(conn, opportunity_id="opp_test_0001", **overrides):
    """Insert one opportunity. FK columns default to NULL (permitted)."""
    row = {
        "opportunity_id": opportunity_id,
        "merchant_id": None,
        "customer_id": None,
        "event_type": "payment_failed",
        "root_cause": "gateway_timeout",
        "amount_at_risk": 50_000,
        "days_overdue": None,
        "status": "open",
        # Recent and inside the 9am-8pm contact window, so the default
        # fixture reaches the pass-through branch rather than accidentally
        # landing in auto-escalate or blocked_contact_hours.
        "created_at": recent_in_window_ts(),
        "resolved_at": None,
        "recovered_bool": None,
        "partial_recovery_amount": None,
        "recovered_at": None,
        "time_to_recovery": None,
        "resolution_type": None,
        "ingestion_event_id": None,
    }
    row.update(overrides)
    cols = ", ".join(row)
    ph = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO opportunities ({cols}) VALUES ({ph})", row)
    conn.commit()
    return row


def make_payment(conn, opportunity_id, payment_id="pay_test_0001", **overrides):
    row = {
        "id": payment_id,
        "opportunity_id": opportunity_id,
        "entity": "payment",
        "amount": 50_000,
        "currency": "INR",
        "status": "failed",
        "order_id": None,
        "invoice_id": None,
        "method": "card",
        "email": None,
        "contact": None,
        "error_code": None,
        "error_description": None,
        "error_source": None,
        "error_step": None,
        "error_reason": "gateway_timeout",
        "created_at": recent_in_window_ts(),
    }
    row.update(overrides)
    cols = ", ".join(row)
    ph = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO payments ({cols}) VALUES ({ph})", row)
    conn.commit()
    return row


def insert_decision(conn, opportunity_id, action_type, outcome="executed",
                    timestamp=None, candidate_id=None):
    """
    Write compliance history directly, bypassing execute_action(). Used to
    construct a specific prior state (e.g. 3 executed contacts) without
    depending on the very function under test to build its own fixture.
    """
    ts = int(time.time()) if timestamp is None else timestamp
    cur = conn.execute(
        """
        INSERT INTO recovery_decisions
        (opportunity_id, candidate_id, action_type, outcome, reasoning,
         triggered_by, ml_recovery_probability, flag_type, timestamp)
        VALUES (?, ?, ?, ?, 'test fixture', 'rule', NULL, NULL, ?)
        """,
        (opportunity_id, candidate_id, action_type, outcome, ts),
    )
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------
# Gate-evidence recorder
#
# The gate document requires that "passes" means a saved report exists at a
# known path, is referenced by the phase's sign-off record, and could be
# independently re-checked by someone who did not implement the phase. This
# hook produces that artifact. It records the environment the run happened
# in, because a green run on an unpinned environment proves less than the
# same run on the pinned one.
# --------------------------------------------------------------------------

_RESULTS: dict[str, dict] = {}


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" and not (report.when == "setup" and report.outcome != "passed"):
        return

    marker = item.get_closest_marker("gate")
    gate_id = marker.args[0] if marker and marker.args else "unmapped"

    prev = _RESULTS.get(report.nodeid, {})
    # A setup error must not be overwritten by a later phase's result.
    if prev.get("outcome") in ("failed", "error"):
        return

    _RESULTS[report.nodeid] = {
        "nodeid": report.nodeid,
        "gate": gate_id,
        "outcome": "error" if (report.when == "setup" and report.failed) else report.outcome,
        "duration_s": round(report.duration, 4),
        "longrepr": str(report.longrepr)[:4000] if report.failed else None,
    }


def _env_provenance() -> dict:
    def _ver(pkg):
        try:
            from importlib.metadata import version
            return version(pkg)
        except Exception:
            return None

    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or None
    except Exception:
        git_sha = None

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_sha,
        "host": socket.gethostname(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "pinned_packages": {
            p: _ver(p) for p in (
                "scikit-learn", "numpy", "pandas", "xgboost", "joblib",
                "fastapi", "pydantic", "pytest",
            )
        },
    }


def pytest_sessionfinish(session, exitstatus):
    if not _RESULTS:
        return

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    by_gate: dict[str, list[dict]] = {}
    for r in _RESULTS.values():
        by_gate.setdefault(r["gate"], []).append(r)

    gates = {}
    for gate_id, rows in sorted(by_gate.items()):
        outcomes = {r["outcome"] for r in rows}
        if outcomes & {"failed", "error"}:
            verdict = "NOT COMPLETE"
        elif outcomes == {"skipped"}:
            verdict = "UNVERIFIED (all skipped)"
        else:
            verdict = "PASS"
        gates[gate_id] = {"verdict": verdict, "tests": rows}

    payload = {
        "provenance": _env_provenance(),
        "pytest_exit_status": int(exitstatus),
        "summary": {
            "total": len(_RESULTS),
            "passed": sum(1 for r in _RESULTS.values() if r["outcome"] == "passed"),
            "failed": sum(1 for r in _RESULTS.values() if r["outcome"] == "failed"),
            "errors": sum(1 for r in _RESULTS.values() if r["outcome"] == "error"),
            "skipped": sum(1 for r in _RESULTS.values() if r["outcome"] == "skipped"),
            "gates_passing": sum(1 for g in gates.values() if g["verdict"] == "PASS"),
            "gates_total": len(gates),
        },
        "gates": gates,
    }

    (EVIDENCE_DIR / f"gate_report_{stamp}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")
    _write_markdown(EVIDENCE_DIR / f"gate_report_{stamp}.md", payload)
    # Stable path for the most recent run, so a sign-off record can cite one
    # filename; the timestamped copies above are the immutable history.
    (EVIDENCE_DIR / "latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(path: Path, payload: dict) -> None:
    p = payload["provenance"]
    s = payload["summary"]
    lines = [
        "# Gate evidence report",
        "",
        f"Generated: {p['generated_at_utc']}  ",
        f"git HEAD: `{p['git_head']}`  ",
        f"Python {p['python']} / SQLite {p['sqlite']} / {p['platform']}  ",
        f"Host: {p['host']}",
        "",
        "## Environment pins as actually installed",
        "",
        "| package | version |",
        "|---|---|",
    ]
    for k, v in p["pinned_packages"].items():
        lines.append(f"| {k} | {v or 'NOT INSTALLED'} |")
    lines += [
        "",
        "## Summary",
        "",
        f"Gates passing: **{s['gates_passing']}/{s['gates_total']}**  ",
        f"Tests: {s['passed']} passed, {s['failed']} failed, "
        f"{s['errors']} errors, {s['skipped']} skipped (of {s['total']})",
        "",
        "## Per-gate verdict",
        "",
        "| gate | verdict | tests |",
        "|---|---|---|",
    ]
    for gate_id, g in payload["gates"].items():
        lines.append(f"| `{gate_id}` | **{g['verdict']}** | {len(g['tests'])} |")

    failing = [(gid, t) for gid, g in payload["gates"].items()
               for t in g["tests"] if t["outcome"] in ("failed", "error")]
    if failing:
        lines += ["", "## Failures", ""]
        for gid, t in failing:
            lines += [f"### `{gid}` — {t['nodeid']}", "", "```",
                      (t["longrepr"] or "").strip(), "```", ""]
    else:
        lines += ["", "No failures recorded in this run.", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
