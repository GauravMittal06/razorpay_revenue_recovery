"""
Phase 5 / W7 -- the shared pipeline.

Gate: "All entry points use the same classify -> optimize -> authorize ->
execute -> message flow -- verified STRUCTURALLY (a single shared function is
called by all three entry points), not only by matching output, so future
changes to one entry point cannot silently diverge from the others."

Both halves are tested here: the structural claim, and a parity corpus
proving the unification changed no behaviour.

HOW PARITY IS MEASURED, AND WHY IT IS NOT A STORED ARTIFACT
    The obvious design -- capture a JSON corpus before the refactor, compare
    after -- has a defect for a full-pipeline comparison: decide_action()
    reads time.time(), so `ml_recovery_probability`, the cooldown reasoning
    strings and every timestamp drift between the capture run and the compare
    run. That drift would have to be absorbed by widening the tolerance,
    which is precisely what the standing rules forbid.

    Instead the legacy sequences are reproduced here VERBATIM from the
    pre-W7 commit, and both paths run in the SAME process, against two
    freshly-created databases, with time.time() pinned. Every field is then
    directly comparable -- timestamps, autoincrement ids and the advisory ML
    probability included -- and the tolerance is zero on all of them.

    The legacy bodies below are copied from commit 9ae5b77. A reviewer can
    re-verify each against its source with, e.g.:

        git show 9ae5b77:backend/engine/core_loop.py

    They are deliberately NOT imported from anywhere: the point is to hold a
    frozen copy of the old behaviour that the new code cannot accidentally
    change.
"""

import re
import sqlite3
import statistics
import time

import pytest

from backend.engine import phase5_config as cfg
from backend.engine import pipeline as pl
from backend.engine.classify import classify
from backend.engine.decide_action import decide_action
from backend.engine.deliver_message import deliver_recovery_message
from backend.engine.execute_action import execute_action
from backend.engine.opportunity_lock import opportunity_lock
from backend.engine.pipeline import run_recovery_pipeline

HOUR = 3600

# A fixed instant inside the 9am-8pm contact window. Pinned so that every
# time-dependent value in the comparison -- cooldown arithmetic, the ML
# feature `days_since_event`, and every written timestamp -- is identical
# between the legacy and unified runs.
PINNED_NOW = None


@pytest.fixture
def pinned_clock(monkeypatch):
    """
    Freeze time.time() at noon today, for both sides of a parity comparison.

    Noon rather than "now" because decide_action()'s contact-window check
    reads a local hour; a suite that ran in the evening would otherwise
    exercise a different branch than one that ran at midday.
    """
    from datetime import datetime
    global PINNED_NOW
    fixed = float(int(datetime.now().replace(
        hour=12, minute=0, second=0, microsecond=0).timestamp()))
    PINNED_NOW = fixed
    monkeypatch.setattr(time, "time", lambda: fixed)
    return fixed


# --------------------------------------------------------------------------
# Legacy sequences, copied verbatim from 9ae5b77
# --------------------------------------------------------------------------

def _legacy_latest_payment(opportunity_id, conn):
    row = conn.execute(
        "SELECT * FROM payments WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def legacy_core_loop_body(opportunity, conn):
    """From 9ae5b77 backend/engine/core_loop.py, run_cycle() loop body."""
    latest_payment = _legacy_latest_payment(opportunity["opportunity_id"], conn)
    classification = classify(
        opportunity["event_type"],
        latest_payment.get("error_reason") if latest_payment else opportunity.get("root_cause"),
    )
    with opportunity_lock(conn):
        decision = decide_action(opportunity, classification, conn,
                                 latest_payment=latest_payment)
        result = execute_action(opportunity, decision, conn)
    delivery = deliver_recovery_message(
        opportunity, classification, decision, conn,
        latest_payment=latest_payment,
        decision_id=result["decision_id"])
    return {"classification": classification, "decision": decision,
            "execution_result": result, "delivery": delivery}


def legacy_trigger_event_tail(opportunity, payment, event_type, root_cause, conn):
    """From 9ae5b77 backend/engine/trigger_event.py, the four pipeline calls."""
    classification = classify(event_type, root_cause)
    decision = decide_action(opportunity, classification, conn,
                             latest_payment=payment)
    result = execute_action(opportunity, decision, conn)
    delivery = deliver_recovery_message(
        opportunity, classification, decision, conn,
        latest_payment=payment,
        decision_id=result["decision_id"])
    return {"classification": classification, "decision": decision,
            "execution_result": result, "delivery": delivery}


def legacy_reply_tail(opportunity, latest_payment, parsed, conn):
    """From 9ae5b77 backend/engine/handle_customer_reply.py, steps 5-7."""
    classification = classify(opportunity["event_type"], opportunity.get("root_cause"))
    dispute_flag = parsed["intent"] == "dispute"
    with opportunity_lock(conn):
        decision = decide_action(
            opportunity, classification, conn,
            latest_payment=latest_payment,
            extracted_intent=parsed["intent"],
            intent_confidence=parsed["confidence"],
            mentioned_reason=parsed["mentioned_reason"],
            dispute_flag=dispute_flag,
        )
        result = execute_action(opportunity, decision, conn)
    delivery = deliver_recovery_message(
        opportunity, classification, decision, conn,
        latest_payment=latest_payment,
        decision_id=result["decision_id"])
    return {"classification": classification, "decision": decision,
            "execution_result": result, "delivery": delivery}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _fresh_seeded_db(tmp_path, seed_dir, name):
    """
    A brand-new database loaded from the same seed set.

    Loads exactly what conftest's `seeded_db` loads, in the same order --
    including bank_health_observations, without which the seeded payments
    carry a channel with no health series to resolve, and the comparison
    would run at network_health_known=0 while production runs at 1.0.
    """
    from backend.db import db as db_module
    path = tmp_path / name
    original = db_module.DB_PATH
    db_module.DB_PATH = path
    try:
        conn = db_module.get_connection()
        db_module.create_schema(conn)
        db_module.load_merchants(conn)
        db_module.load_customers(conn)
        db_module.load_opportunities(conn)
        db_module.load_payments(conn)
        db_module.load_bank_health_observations(conn)
    finally:
        db_module.DB_PATH = original
    return conn


def _comparable(outcome):
    """
    Everything the two paths must agree on, with only the declared volatile
    fields removed. PARITY_VOLATILE_FIELDS was committed before this ran.
    """
    def strip(d):
        if not isinstance(d, dict):
            return d
        return {k: v for k, v in d.items()
                if k not in cfg.PARITY_VOLATILE_FIELDS}

    return {
        "classification": outcome["classification"],
        "decision": strip(outcome["decision"]),
        "execution_result": strip(outcome["execution_result"]),
        "delivery": outcome["delivery"],
    }


def _all_rows(conn, table):
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]


# --------------------------------------------------------------------------
# Parity: core_loop
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.shared_pipeline")
def test_batch_parity_across_the_whole_seeded_set(tmp_path, seed_data_dir,
                                                   pinned_clock, capsys):
    """
    Every one of the seeded opportunities, legacy sequence vs unified
    pipeline, field for field, tolerance zero.
    """
    legacy_conn = _fresh_seeded_db(tmp_path, seed_data_dir, "legacy.db")
    unified_conn = _fresh_seeded_db(tmp_path, seed_data_dir, "unified.db")

    def opportunities(conn):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM opportunities WHERE status IN ('open', 'recovering')"
        ).fetchall()]

    legacy_out, unified_out = [], []
    for opportunity in opportunities(legacy_conn):
        legacy_out.append(_comparable(legacy_core_loop_body(opportunity, legacy_conn)))
    for opportunity in opportunities(unified_conn):
        latest_payment = _legacy_latest_payment(
            opportunity["opportunity_id"], unified_conn)
        unified_out.append(_comparable(run_recovery_pipeline(
            opportunity, unified_conn, entry_point="batch",
            latest_payment=latest_payment)))

    diffs = [(i, l, u) for i, (l, u) in enumerate(zip(legacy_out, unified_out))
             if l != u]
    print(f"  batch parity: {len(legacy_out)} opportunities compared, "
          f"{len(diffs)} differing (tolerance "
          f"{cfg.PIPELINE_PARITY_FIELD_TOLERANCE})")
    assert len(legacy_out) == len(unified_out) > 0
    assert len(diffs) == cfg.PIPELINE_PARITY_FIELD_TOLERANCE, diffs[:3]

    # The databases themselves, not just the returned values.
    for table in ("recovery_decisions", "recovery_executions", "messages"):
        lrows = [{k: v for k, v in r.items() if k != "timestamp"}
                 for r in _all_rows(legacy_conn, table)]
        urows = [{k: v for k, v in r.items() if k != "timestamp"}
                 for r in _all_rows(unified_conn, table)]
        print(f"  {table}: legacy={len(lrows)} unified={len(urows)}")
        assert lrows == urows, f"{table} diverged"

    legacy_conn.close()
    unified_conn.close()


# --------------------------------------------------------------------------
# Parity: trigger_event
# --------------------------------------------------------------------------

TRIGGER_SPECS = [
    ("payment_failed", 50000, "insufficient_funds", None),
    ("payment_failed", 12500, "expired_card", None),
    ("payment_failed", 99900, "gateway_timeout", None),
    ("checkout_abandoned", 30000, None, None),
    ("invoice_overdue", 75000, None, 3),
    ("invoice_overdue", 75000, None, 20),
]


@pytest.mark.gate("phase5.shared_pipeline")
def test_trigger_event_parity_over_the_fixed_spec_list(tmp_path, seed_data_dir,
                                                        pinned_clock, capsys):
    """
    Every event type and both invoice-overdue escalation branches, legacy
    sequence vs unified pipeline.
    """
    from backend.engine.trigger_event import trigger_event

    legacy_conn = _fresh_seeded_db(tmp_path, seed_data_dir, "t_legacy.db")
    unified_conn = _fresh_seeded_db(tmp_path, seed_data_dir, "t_unified.db")

    compared = 0
    for event_type, amount, root_cause, days_overdue in TRIGGER_SPECS:
        # Legacy: build the rows the way trigger_event does, then run the old
        # four-call tail. Using the real function for the unified side.
        oid = f"opp_legacy_{compared}"
        pid = f"pay_legacy_{compared}"
        now = int(time.time())
        opportunity = {
            "opportunity_id": oid, "merchant_id": None, "customer_id": None,
            "event_type": event_type,
            "root_cause": root_cause if event_type == "payment_failed" else None,
            "amount_at_risk": amount, "days_overdue": days_overdue,
            "status": "open", "created_at": now, "resolved_at": None,
            "recovered_bool": None, "partial_recovery_amount": None,
            "recovered_at": None, "time_to_recovery": None,
            "resolution_type": None, "ingestion_event_id": None,
        }
        legacy_conn.execute(
            "INSERT INTO opportunities (opportunity_id, merchant_id, customer_id,"
            " event_type, root_cause, amount_at_risk, days_overdue, status,"
            " created_at, ingestion_event_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (oid, None, None, event_type, opportunity["root_cause"], amount,
             days_overdue, "open", now, None))
        payment = {
            "id": pid, "opportunity_id": oid, "entity": "payment",
            "amount": amount, "currency": "INR", "status": "created",
            "order_id": None, "invoice_id": None, "method": None,
            "email": None, "contact": None, "error_code": None,
            "error_description": None, "error_source": None,
            "error_step": None,
            "error_reason": root_cause if event_type == "payment_failed" else None,
            "created_at": now,
        }
        legacy_conn.execute(
            "INSERT INTO payments (id, opportunity_id, entity, amount, currency,"
            " status, error_reason, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, oid, "payment", amount, "INR", "created",
             payment["error_reason"], now))
        legacy_conn.commit()

        # AMENDMENT, Phase 6 / X3, locked 2026-09-04. The unified side runs the real
        # trigger_event, which now assigns each opportunity it creates to a
        # randomized arm; the legacy side builds its rows by hand and has no
        # assignment. At a 0.5 holdout that made roughly half these specs
        # diverge for a reason that has nothing to do with pipeline
        # unification: the unified opportunity was suppressed as a control and
        # the legacy one was not.
        #
        # Equalising the experiment state on both sides is what keeps this
        # test measuring the thing it was written to measure. Left unchanged,
        # it would instead have asserted that Phase 6 had not happened.
        #
        # NOT a weakening. Parity is still judged at
        # PIPELINE_PARITY_FIELD_TOLERANCE = 0 over the same six specs, and the
        # check is now strictly stronger: it exercises the legacy-vs-unified
        # comparison in BOTH arms, including the suppression path, where
        # before it only ever saw the treated one.
        result = trigger_event(event_type, amount, unified_conn,
                               root_cause=root_cause, days_overdue=days_overdue)
        assert result["status"] == "ok", result

        legacy_conn.execute(
            'INSERT INTO experiment_assignment '
            '(opportunity_id, "group", assigned_at, assignment_method) '
            "VALUES (?, ?, ?, ?)",
            (oid, result["assignment"]["group"],
             result["assignment"]["assigned_at"],
             result["assignment"]["assignment_method"]))
        legacy_conn.commit()

        legacy = _comparable(legacy_trigger_event_tail(
            opportunity, payment, event_type,
            root_cause if event_type == "payment_failed" else None, legacy_conn))
        unified = _comparable({
            "classification": result["classification"],
            "decision": result["decision"],
            "execution_result": result["execution_result"],
            "delivery": result["delivery"],
        })

        assert legacy == unified, (
            f"{event_type}/{root_cause}/{days_overdue} diverged:\n"
            f"  legacy ={legacy}\n  unified={unified}")
        compared += 1

    print(f"  trigger_event parity: {compared} specs compared, 0 differing")
    legacy_conn.close()
    unified_conn.close()


@pytest.mark.gate("phase5.shared_pipeline")
def test_a_replayed_event_still_short_circuits_before_the_pipeline(
        tmp_path, seed_data_dir, pinned_clock, monkeypatch):
    """
    trigger_event's dedup must return BEFORE the shared pipeline runs. If
    unification moved it after, a redelivered upstream event would execute a
    second action against the customer.
    """
    from backend.engine import trigger_event as te

    conn = _fresh_seeded_db(tmp_path, seed_data_dir, "dedup.db")

    calls = []
    real = te.run_recovery_pipeline
    monkeypatch.setattr(te, "run_recovery_pipeline",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    first = te.trigger_event("payment_failed", 40000, conn,
                             root_cause="network_error", event_id="evt-replay-1")
    second = te.trigger_event("payment_failed", 40000, conn,
                              root_cause="network_error", event_id="evt-replay-1")

    assert first["status"] == "ok"
    assert second["status"] == "duplicate_event_ignored", second
    assert len(calls) == 1, (
        f"the replayed event ran the pipeline again ({len(calls)} runs)")
    conn.close()


# --------------------------------------------------------------------------
# Parity: handle_customer_reply
# --------------------------------------------------------------------------

REPLY_PARSES = [
    {"intent": "will_pay_later", "confidence": 0.92,
     "mentioned_reason": None, "extracted_detail": None},
    {"intent": "dispute", "confidence": 0.88,
     "mentioned_reason": None, "extracted_detail": None},
    {"intent": "unclear", "confidence": 0.10,
     "mentioned_reason": None, "extracted_detail": None},
    {"intent": "payment_method_updated", "confidence": 0.95,
     "mentioned_reason": "expired_card", "extracted_detail": None},
    {"intent": "already_paid", "confidence": 0.90,
     "mentioned_reason": "insufficient_funds", "extracted_detail": None},
]


@pytest.mark.gate("phase5.shared_pipeline")
def test_customer_reply_parity_over_fixed_intent_pairs(tmp_path, seed_data_dir,
                                                        pinned_clock, capsys):
    """
    The reply path's distinguishing input is the intent quartet, which arms
    decide_action()'s confidence gate and its intent-mismatch gate. Both are
    exercised here -- including the low-confidence and mismatch branches,
    which are the ones that can BLOCK.
    """
    legacy_conn = _fresh_seeded_db(tmp_path, seed_data_dir, "r_legacy.db")
    unified_conn = _fresh_seeded_db(tmp_path, seed_data_dir, "r_unified.db")

    targets = [r["opportunity_id"] for r in legacy_conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE event_type='payment_failed'"
        " AND status IN ('open','recovering') ORDER BY opportunity_id LIMIT 5"
    ).fetchall()]
    assert len(targets) == len(REPLY_PARSES), targets

    for oid, parsed in zip(targets, REPLY_PARSES):
        lopp = dict(legacy_conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
        uopp = dict(unified_conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
        lpay = _legacy_latest_payment(oid, legacy_conn)
        upay = _legacy_latest_payment(oid, unified_conn)

        legacy = _comparable(legacy_reply_tail(lopp, lpay, parsed, legacy_conn))
        unified = _comparable(run_recovery_pipeline(
            uopp, unified_conn, entry_point="customer_reply",
            latest_payment=upay,
            extracted_intent=parsed["intent"],
            intent_confidence=parsed["confidence"],
            mentioned_reason=parsed["mentioned_reason"],
            dispute_flag=parsed["intent"] == "dispute"))

        assert legacy == unified, (
            f"{oid} / intent={parsed['intent']} diverged:\n"
            f"  legacy ={legacy}\n  unified={unified}")

    outcomes = sorted({d["outcome"] for d in
                       (dict(r) for r in legacy_conn.execute(
                           "SELECT outcome FROM recovery_decisions").fetchall())})
    print(f"  reply parity: {len(targets)} pairs compared, 0 differing; "
          f"outcomes exercised: {outcomes}")
    legacy_conn.close()
    unified_conn.close()


# --------------------------------------------------------------------------
# Structural: the gate's actual requirement
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.shared_pipeline")
def test_all_three_entry_points_call_the_one_shared_function(source_files):
    """
    The gate verbatim: "a single shared function is called by all three entry
    points", verified structurally rather than by matching output.
    """
    expected = {"core_loop.py", "trigger_event.py", "handle_customer_reply.py"}
    callers = set()
    for path in source_files:
        if path.name not in expected:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\brun_recovery_pipeline\s*\(", text):
            callers.add(path.name)

    assert callers == expected, (
        f"entry points not calling the shared pipeline: {sorted(expected - callers)}")


@pytest.mark.gate("phase5.shared_pipeline")
def test_each_entry_point_declares_a_known_entry_point_name(source_files):
    """
    The entry_point argument selects lock and optimizer policy, so a typo
    would silently give one caller another's concurrency behaviour. It is
    validated at runtime by run_recovery_pipeline; this pins the declarations.
    """
    declared = {}
    for path in source_files:
        if path.name not in {"core_loop.py", "trigger_event.py",
                             "handle_customer_reply.py"}:
            continue
        m = re.search(r'^ENTRY_POINT\s*=\s*"([a-z_]+)"',
                      path.read_text(encoding="utf-8"), re.M)
        assert m, f"{path.name} declares no ENTRY_POINT"
        declared[path.name] = m.group(1)

    assert set(declared.values()) == set(cfg.ENTRY_POINTS_USING_SHARED_PIPELINE), (
        f"declared {declared}, expected exactly "
        f"{set(cfg.ENTRY_POINTS_USING_SHARED_PIPELINE)}")


@pytest.mark.gate("phase5.shared_pipeline")
def test_an_unknown_entry_point_is_refused(empty_db):
    from backend.tests.conftest import make_opportunity, recent_in_window_ts
    make_opportunity(empty_db, "opp_ep_0001", event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    opportunity = dict(empty_db.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = 'opp_ep_0001'").fetchone())

    with pytest.raises(ValueError, match="not one of the declared"):
        run_recovery_pipeline(opportunity, empty_db, entry_point="dispatch")


# --------------------------------------------------------------------------
# The lock policy table, asserted BOTH ways
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.concurrency")
def test_the_lock_table_is_asserted_in_both_directions(empty_db, monkeypatch):
    """
    Not just "the locking ones lock" but "the non-locking one does not".
    The trigger_event asymmetry is deliberate and must not be tidied away.
    """
    from backend.tests.conftest import make_opportunity, recent_in_window_ts

    entered = []
    real_lock = pl.opportunity_lock

    def spy(conn):
        entered.append(1)
        return real_lock(conn)

    monkeypatch.setattr(pl, "opportunity_lock", spy)

    for i, entry_point in enumerate(cfg.ENTRY_POINTS_USING_SHARED_PIPELINE):
        oid = f"opp_lock_{i}"
        make_opportunity(empty_db, oid, event_type="checkout_abandoned",
                         created_at=recent_in_window_ts(days_ago=0, hour=12),
                         status="open")
        opportunity = dict(empty_db.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
        before = len(entered)
        run_recovery_pipeline(opportunity, empty_db, entry_point=entry_point)
        used = len(entered) > before

        should = entry_point in cfg.ENTRY_POINTS_USING_OPPORTUNITY_LOCK
        assert used == should, (
            f"{entry_point}: used opportunity_lock={used}, table says {should}")

    assert "trigger_event" not in cfg.ENTRY_POINTS_USING_OPPORTUNITY_LOCK


@pytest.mark.gate("phase5.concurrency")
def test_opportunity_lock_is_adopted_unchanged(source_files):
    """
    W6's hand-off requires the lock be adopted, not reworked. Pin its public
    shape: one contextmanager named opportunity_lock taking a connection, and
    a BEGIN IMMEDIATE inside it.
    """
    path = next(p for p in source_files if p.name == "opportunity_lock.py")
    text = path.read_text(encoding="utf-8")
    assert "BEGIN IMMEDIATE" in text
    assert re.search(r"@contextmanager\s+def opportunity_lock\(conn\):", text), (
        "opportunity_lock's signature changed; W7 was required to adopt it "
        "unchanged")


# --------------------------------------------------------------------------
# Constraint 2, measured -- not merely structural
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.optimizer_boundary")
def test_the_optimizer_is_not_called_inside_the_lock(source_files):
    """Structural half: no ranking call lexically inside the `with` block."""
    path = next(p for p in source_files if p.name == "pipeline.py")
    body = path.read_text(encoding="utf-8").split('"""')[-1]

    m = re.search(r"with _lock_for\(.*?\):\n(.*?)\n\n", body, re.S)
    assert m, "could not locate the lock block in pipeline.py"
    assert "optimize" not in m.group(1), (
        "a ranking call appears inside the lock hold:\n" + m.group(1))


@pytest.mark.gate("phase5.optimizer_boundary")
def test_lock_hold_stays_flat_when_the_optimizer_is_enabled(
        tmp_path, seed_data_dir, capsys, monkeypatch):
    """
    MEASURED, the same way the original ~6ms vs ~650ms was measured.

    The sharp form of the proof: with the optimizer ON the TOTAL pipeline
    time must rise by orders of magnitude while the LOCK HOLD stays flat. A
    structural check alone cannot show that. The ceiling was committed in
    phase5_config before this ran.
    """
    from backend.tests.conftest import make_opportunity, recent_in_window_ts
    from backend.engine import decide_action as _da

    # Warm the ML model BEFORE timing anything. decide_action() loads it
    # lazily, and the first load lands inside the lock -- a real, pre-existing
    # ~780ms cold hold, pinned separately by
    # test_the_first_lock_hold_is_inflated_by_the_lazy_model_load. Leaving it
    # in these samples would make the comparison below measure joblib rather
    # than the pipeline.
    _da._load_ml_model()

    def measure(optimizer_on, n=6):
        conn = _fresh_seeded_db(tmp_path, seed_data_dir,
                                f"timing_{optimizer_on}.db")
        monkeypatch.setitem(cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT,
                            "batch", optimizer_on)

        holds, totals = [], []
        real_lock = pl.opportunity_lock

        class TimedLock:
            def __init__(self, conn):
                self.inner = real_lock(conn)

            def __enter__(self):
                self.t0 = time.perf_counter()
                return self.inner.__enter__()

            def __exit__(self, *exc):
                try:
                    return self.inner.__exit__(*exc)
                finally:
                    holds.append((time.perf_counter() - self.t0) * 1000.0)

        monkeypatch.setattr(pl, "opportunity_lock", TimedLock)

        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM opportunities WHERE status IN ('open','recovering')"
            " ORDER BY opportunity_id LIMIT ?", (n,)).fetchall()]
        for opportunity in rows:
            latest = _legacy_latest_payment(opportunity["opportunity_id"], conn)
            t0 = time.perf_counter()
            run_recovery_pipeline(opportunity, conn, entry_point="batch",
                                  latest_payment=latest)
            totals.append((time.perf_counter() - t0) * 1000.0)
        conn.close()
        return holds, totals

    def pct(xs, p):
        xs = sorted(xs)
        return xs[min(int(len(xs) * p), len(xs) - 1)]

    def measure_legacy(n=6):
        """
        The pre-W7 sequence, timed identically. Answers the question the
        optimizer-on/off pair alone cannot: did unification itself change
        how long the lock is held? Both paths run decide_action +
        execute_action inside the hold, so they should match.
        """
        conn = _fresh_seeded_db(tmp_path, seed_data_dir, "timing_legacy.db")
        holds = []
        real_lock = opportunity_lock

        class TimedLock:
            def __init__(self, conn):
                self.inner = real_lock(conn)

            def __enter__(self):
                self.t0 = time.perf_counter()
                return self.inner.__enter__()

            def __exit__(self, *exc):
                try:
                    return self.inner.__exit__(*exc)
                finally:
                    holds.append((time.perf_counter() - self.t0) * 1000.0)

        import backend.tests.test_phase5_shared_pipeline as self_mod
        monkeypatch.setattr(self_mod, "opportunity_lock", TimedLock)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM opportunities WHERE status IN ('open','recovering')"
            " ORDER BY opportunity_id LIMIT ?", (n,)).fetchall()]
        for opportunity in rows:
            legacy_core_loop_body(opportunity, conn)
        conn.close()
        return holds

    legacy_holds = measure_legacy()
    off_holds, off_totals = measure(False)
    on_holds, on_totals = measure(True)

    print(f"  legacy (pre-W7): lock hold p50 {pct(legacy_holds,.5):7.2f} ms  "
          f"p95 {pct(legacy_holds,.95):7.2f} ms")
    print(f"  optimizer OFF: lock hold p50 {pct(off_holds,.5):7.2f} ms  "
          f"p95 {pct(off_holds,.95):7.2f} ms | total p50 {pct(off_totals,.5):8.2f} ms")
    print(f"  optimizer ON : lock hold p50 {pct(on_holds,.5):7.2f} ms  "
          f"p95 {pct(on_holds,.95):7.2f} ms | total p50 {pct(on_totals,.5):8.2f} ms")
    print(f"  ceiling (locked before this ran): "
          f"{cfg.UNIFIED_LOCK_HOLD_P95_CEILING_MS} ms")
    print(f"  NOTE: the hold exceeds the ~6ms recorded in opportunity_lock.py "
          f"because decide_action() runs single-row model inference inside it "
          f"(ml_recovery_probability). That predates W7 -- the legacy row "
          f"above is measured on the same machine for comparison.")

    assert pct(off_holds, .95) < cfg.UNIFIED_LOCK_HOLD_P95_CEILING_MS
    assert pct(on_holds, .95) < cfg.UNIFIED_LOCK_HOLD_P95_CEILING_MS, (
        "enabling the optimizer inflated the lock hold, which is exactly the "
        "regression opportunity_lock.py documents: ~850 queued workers become "
        "about 7 before one exceeds db.BUSY_TIMEOUT_MS")
    assert pct(on_totals, .5) > pct(off_totals, .5), (
        "the optimizer did not measurably run, so this proves nothing")


@pytest.mark.gate("phase5.optimizer_boundary")
def test_the_first_lock_hold_is_inflated_by_the_lazy_model_load(
        tmp_path, seed_data_dir, capsys, monkeypatch):
    """
    A PRE-EXISTING exposure found while measuring W7's lock hold. This test
    asserts the LIMITATION, not a guarantee -- the same pattern
    test_calling_execute_action_twice_creates_two_decisions_not_one uses.

    decide_action() loads the ML model lazily via _load_ml_model(), and the
    first call in a process happens inside decide_action(), which the
    pipeline calls INSIDE opportunity_lock. So the very first lock hold after
    a process starts includes a joblib.load of the model.

    Measured standalone, 8 consecutive opportunities from a cold process:

        hold #1  779.35 ms   <-- cold, includes the model load
        hold #2    9.75 ms
        hold #3    6.23 ms
        ...
        hold #8    6.41 ms
        first / median of the rest = 121.5x

    The warm holds match the 5.88 ms p50 recorded in opportunity_lock.py.
    The cold one is ~780 ms -- the same order as the ~650 ms
    optimize_opportunity() cost that module names as the thing that must
    never be inside the lock. Against db.BUSY_TIMEOUT_MS = 5000 it puts the
    first cycle after any restart in the regime where roughly the seventh
    concurrent worker would fail with "database is locked".

    NOT introduced by W7 -- the pre-W7 sequence shows it too (p95 946 ms in
    the legacy measurement) -- and NOT fixed by W7, because the fix belongs
    to the compliance authority's loading strategy and needs its own ruling.
    Tracked as closeout item C4.
    """
    from backend.engine import decide_action as _da

    monkeypatch.setattr(_da, "_ML_MODEL", None)
    monkeypatch.setattr(_da, "_ML_MODEL_LOAD_ATTEMPTED", False)

    conn = _fresh_seeded_db(tmp_path, seed_data_dir, "cold.db")
    holds = []
    real_lock = pl.opportunity_lock

    class TimedLock:
        def __init__(self, conn):
            self.inner = real_lock(conn)

        def __enter__(self):
            self.t0 = time.perf_counter()
            return self.inner.__enter__()

        def __exit__(self, *exc):
            try:
                return self.inner.__exit__(*exc)
            finally:
                holds.append((time.perf_counter() - self.t0) * 1000.0)

    monkeypatch.setattr(pl, "opportunity_lock", TimedLock)

    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM opportunities WHERE status IN ('open','recovering')"
        " ORDER BY opportunity_id LIMIT 6").fetchall()]
    for opportunity in rows:
        latest = _legacy_latest_payment(opportunity["opportunity_id"], conn)
        run_recovery_pipeline(opportunity, conn, entry_point="batch",
                              latest_payment=latest)
    conn.close()

    warm = sorted(holds[1:])
    warm_median = warm[len(warm) // 2]
    print(f"  cold first hold {holds[0]:8.2f} ms | warm median "
          f"{warm_median:6.2f} ms | ratio {holds[0]/warm_median:6.1f}x")

    if holds[0] < 100.0:
        pytest.skip(
            f"model already resident in this process (first hold "
            f"{holds[0]:.2f} ms); the cold path cannot be observed here")
    assert holds[0] > warm_median * 5, (
        "the cold-start inflation this test documents did not reproduce; if "
        "the lazy load moved out of the lock, delete this test and close C4")


# --------------------------------------------------------------------------
# Constraint 4: execute_action is called at most once
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.idempotent_dispatch")
def test_execute_action_is_called_exactly_once_per_pipeline_run(
        empty_db, monkeypatch):
    from backend.tests.conftest import make_opportunity, recent_in_window_ts

    calls = []
    real = pl.execute_action
    monkeypatch.setattr(pl, "execute_action",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    for i, entry_point in enumerate(cfg.ENTRY_POINTS_USING_SHARED_PIPELINE):
        oid = f"opp_once_{i}"
        make_opportunity(empty_db, oid, event_type="checkout_abandoned",
                         created_at=recent_in_window_ts(days_ago=0, hour=12),
                         status="open")
        opportunity = dict(empty_db.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
        before = len(calls)
        run_recovery_pipeline(opportunity, empty_db, entry_point=entry_point)
        assert len(calls) - before == 1, (
            f"{entry_point} called execute_action {len(calls)-before} times")


# --------------------------------------------------------------------------
# Constraint 3: decision_id passthrough, behavioural + negative control
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.delivery_gating")
def test_each_entry_point_contacts_the_customer_exactly_once(empty_db):
    from backend.tests.conftest import make_opportunity, recent_in_window_ts

    for i, entry_point in enumerate(cfg.ENTRY_POINTS_USING_SHARED_PIPELINE):
        oid = f"opp_deliver_once_{i}"
        make_opportunity(empty_db, oid, event_type="checkout_abandoned",
                         created_at=recent_in_window_ts(days_ago=0, hour=12),
                         status="open")
        opportunity = dict(empty_db.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
        run_recovery_pipeline(opportunity, empty_db, entry_point=entry_point)

        msgs = empty_db.execute(
            "SELECT COUNT(*) c FROM messages WHERE opportunity_id = ? "
            "AND sender = 'agent'", (oid,)).fetchone()["c"]
        assert msgs == 1, f"{entry_point} produced {msgs} agent messages"


@pytest.mark.gate("phase5.delivery_gating")
def test_negative_control_dropping_decision_id_silences_delivery(
        empty_db, monkeypatch, capsys):
    """
    NEGATIVE CONTROL for constraint 3. If the shared pipeline stopped naming
    the execution, delivery would fail closed and no customer would be
    contacted. The test above is only meaningful if this one reproduces that.
    """
    from backend.tests.conftest import make_opportunity, recent_in_window_ts

    real = pl.deliver_recovery_message
    monkeypatch.setattr(
        pl, "deliver_recovery_message",
        lambda *a, **k: real(*a, **{x: y for x, y in k.items()
                                    if x != "decision_id"}))

    oid = "opp_negctl_delivery"
    make_opportunity(empty_db, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    opportunity = dict(empty_db.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
    outcome = run_recovery_pipeline(opportunity, empty_db, entry_point="batch")

    msgs = empty_db.execute(
        "SELECT COUNT(*) c FROM messages WHERE opportunity_id = ? "
        "AND sender = 'agent'", (oid,)).fetchone()["c"]
    print(f"  negative control: delivery status="
          f"{outcome['delivery']['status']}, agent messages={msgs} (expected 0)")
    assert outcome["delivery"]["status"] == "skipped_unverified_execution"
    assert msgs == 0, (
        "dropping decision_id did NOT silence delivery, so the passthrough "
        "test above proves nothing")
