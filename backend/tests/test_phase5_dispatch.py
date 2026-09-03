"""
Phase 5 / W6 -- the scheduled dispatch sweep.

Mapped to the Phase 5 acceptance gates:

  Scheduling          due fires / future untouched / invalidated not fired
  Idempotent dispatch the same due row swept twice produces exactly one
                      execution and one customer-visible action
  Execution separation no compliance token is ever written to a lifecycle
                      field, and vice versa
  Method change       no reachable dispatch of a payment-method change

The concurrency tests here are barrier-forced rather than "run it twice and
hope". This project has already recorded that a green concurrency test is weak
evidence by construction -- two of its own race tests passed 12/12 and 40/40
while the races were fully reproducible -- so the sequential gate is kept
because the plan calls for it, and the barrier test is what actually
establishes the property.
"""

import sqlite3
import threading
import time

import pytest

from backend.db.db import DECISION_OUTCOMES, EXECUTION_STATES
from backend.engine import dispatch_scheduled as ds
from backend.engine import phase5_config as cfg
from backend.engine.dispatch_scheduled import (dispatch_due_execution,
                                               run_dispatch_cycle,
                                               stuck_dispatches)
from backend.engine.execute_action import execute_action
from backend.tests.conftest import make_opportunity, recent_in_window_ts

HOUR = 3600


def now_in_window(hour: int = 12) -> int:
    """
    A dispatch clock inside the 9am-8pm contact window.

    Every test here must pin `now` rather than let the sweep read the real
    clock. Ruling A2 made the contact window depend on the moment the action
    fires, so a suite that used wall-clock time would pass all day and fail
    every evening -- which is exactly what happened on the first run of this
    file at 23:00 local: every due action was correctly cancelled as
    blocked_contact_hours, and every scheduling test failed.

    This is the `now` counterpart of conftest.recent_in_window_ts(), which
    exists for the same reason on the `created_at` side.
    """
    from datetime import datetime
    return int(datetime.now().replace(hour=hour, minute=0, second=0,
                                      microsecond=0).timestamp())


NOW = now_in_window()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _candidate(conn, opportunity_id, action="reminder", timing="4h"):
    cur = conn.execute(
        """
        INSERT INTO recovery_candidates
        (opportunity_id, action_type, timing, method, channel,
         predicted_eiv, rank, selected, created_at)
        VALUES (?, ?, ?, 'n/a', 'email', 12.5, 1, 0, ?)
        """,
        (opportunity_id, action, timing, int(time.time())),
    )
    conn.commit()
    return cur.lastrowid


def _schedule(conn, oid, action="reminder", timing="4h", hour=12):
    """
    One approved-and-queued action, exactly as W5's executor writes it.

    Returns (opportunity, execution_row). The opportunity is created inside
    the contact window so the fixture is not accidentally testing the window
    when it means to test the lifecycle.
    """
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=hour),
                     status="open")
    candidate_id = _candidate(conn, oid, action=action, timing=timing)
    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    decision = {"action_type": action, "allowed": True, "outcome": "executed",
                "reasoning": "queued by the ranked path", "triggered_by": "rule",
                "candidate_id": candidate_id}
    result = execute_action(opportunity, decision, conn)
    execution = dict(conn.execute(
        "SELECT * FROM recovery_executions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone())
    assert execution["state"] == "scheduled", "fixture must queue, not fire"
    return opportunity, execution


def _make_due(conn, execution_id, seconds_overdue=60, now=None):
    """Move a queued action's due time into the past, relative to `now`."""
    now = NOW if now is None else now
    conn.execute(
        "UPDATE recovery_executions SET scheduled_for = ? WHERE execution_id = ?",
        (now - seconds_overdue, execution_id))
    # Keep the decision old enough that its own cooldown is not what blocks it.
    conn.execute(
        """
        UPDATE recovery_decisions SET timestamp = ?
        WHERE decision_id = (SELECT decision_id FROM recovery_executions
                             WHERE execution_id = ?)
        """,
        (now - 2 * 24 * HOUR, execution_id))
    conn.commit()


def _execution(conn, execution_id):
    return dict(conn.execute(
        "SELECT * FROM recovery_executions WHERE execution_id = ?",
        (execution_id,)).fetchone())


def _agent_messages(conn, oid):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM messages WHERE opportunity_id = ? AND sender = 'agent'",
        (oid,)).fetchall()]


def _counts(conn):
    return {
        "decisions": conn.execute(
            "SELECT COUNT(*) c FROM recovery_decisions").fetchone()["c"],
        "executions": conn.execute(
            "SELECT COUNT(*) c FROM recovery_executions").fetchone()["c"],
    }


# --------------------------------------------------------------------------
# Gate: Scheduling -- due actions fire
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.scheduling")
def test_an_action_due_in_the_past_fires(empty_db):
    conn = empty_db
    opportunity, execution = _schedule(conn, "opp_due_0001")
    _make_due(conn, execution["execution_id"])

    results = run_dispatch_cycle(now=NOW, conn=conn)

    row = _execution(conn, execution["execution_id"])
    assert len(results) == 1, results
    assert results[0]["dispatched"] is True, results[0]
    assert row["state"] == "executed", row
    assert row["executed_at"] is not None
    assert len(_agent_messages(conn, "opp_due_0001")) == 1


@pytest.mark.gate("phase5.scheduling")
def test_an_action_due_exactly_now_fires(empty_db):
    """
    Pins DISPATCH_DUE_GRACE_SECONDS == 0 behaviourally: due means
    scheduled_for <= now, so the boundary instant is inside, not outside.
    """
    assert cfg.DISPATCH_DUE_GRACE_SECONDS == 0
    conn = empty_db
    opportunity, execution = _schedule(conn, "opp_due_0002")
    now = NOW
    conn.execute(
        "UPDATE recovery_executions SET scheduled_for = ? WHERE execution_id = ?",
        (now, execution["execution_id"]))
    conn.execute(
        "UPDATE recovery_decisions SET timestamp = ? WHERE decision_id = ?",
        (now - 2 * 24 * HOUR, execution["decision_id"]))
    conn.commit()

    run_dispatch_cycle(now=now, conn=conn)

    assert _execution(conn, execution["execution_id"])["state"] == "executed"


# --------------------------------------------------------------------------
# Gate: Scheduling -- future actions are untouched
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.scheduling")
def test_an_action_due_in_the_future_is_untouched(empty_db):
    """Byte-level: not merely 'not executed', but not modified at all."""
    conn = empty_db
    opportunity, execution = _schedule(conn, "opp_future_0001")
    # Pin the due time relative to the sweep's clock rather than trusting the
    # real-clock value execute_action() wrote. Mixing the two is a genuine
    # flake: this test failed once when the date rolled over mid-run, because
    # `now + 4h` off the real clock at 00:42 lands before a NOW pinned to
    # 12:00 the same day, making the "future" row overdue.
    conn.execute(
        "UPDATE recovery_executions SET scheduled_for = ? WHERE execution_id = ?",
        (NOW + 4 * HOUR, execution["execution_id"]))
    conn.commit()
    before = _execution(conn, execution["execution_id"])
    assert before["scheduled_for"] > NOW

    results = run_dispatch_cycle(now=NOW, conn=conn)

    assert results == []
    assert _execution(conn, execution["execution_id"]) == before
    assert _agent_messages(conn, "opp_future_0001") == []


@pytest.mark.gate("phase5.scheduling")
@pytest.mark.parametrize("state", ["pending", "cancelled", "superseded",
                                   "failed", "dispatched", "executed"])
def test_only_scheduled_rows_are_swept_even_when_overdue(empty_db, state):
    """
    The sweep is state-scoped, not merely time-scoped. An overdue row in any
    other lifecycle state is not the sweep's business -- and in particular a
    'dispatched' row is never re-fired, which is what keeps the at-most-once
    guarantee (ruling A4).
    """
    conn = empty_db
    opportunity, execution = _schedule(conn, f"opp_state_{state}")
    _make_due(conn, execution["execution_id"])
    conn.execute("UPDATE recovery_executions SET state = ? WHERE execution_id = ?",
                 (state, execution["execution_id"]))
    conn.commit()
    before = _execution(conn, execution["execution_id"])

    run_dispatch_cycle(now=NOW, conn=conn)

    assert _execution(conn, execution["execution_id"]) == before
    assert _agent_messages(conn, f"opp_state_{state}") == []


# --------------------------------------------------------------------------
# Gate: Scheduling -- invalidated/superseded actions are not fired
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.scheduling")
@pytest.mark.parametrize("terminal", ["stopped", "escalated", "recovered"])
def test_an_opportunity_closed_meanwhile_abandons_its_queued_action(empty_db, terminal):
    """
    Ruling A3. 'recovered' is the case decide_action() cannot see at all --
    mark_opportunity_recovered() writes no decision row -- so without the
    liveness precondition the dispatcher would send a payment reminder to a
    customer who has already paid.
    """
    conn = empty_db
    oid = f"opp_closed_{terminal}"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])
    conn.execute("UPDATE opportunities SET status = ? WHERE opportunity_id = ?",
                 (terminal, oid))
    conn.commit()

    results = run_dispatch_cycle(now=NOW, conn=conn)

    row = _execution(conn, execution["execution_id"])
    assert row["state"] == "cancelled", row
    assert row["executed_at"] is None
    assert _agent_messages(conn, oid) == [], "a closed case was contacted"
    assert terminal in results[0]["reason"]
    assert row["state_reason"] and terminal in row["state_reason"], (
        "an abandoned action must record why -- 'every action the system "
        "takes or declines to take is logged with a reason'")


@pytest.mark.gate("phase5.scheduling")
def test_an_action_stopped_by_the_rule_engine_meanwhile_is_abandoned(empty_db):
    """
    The compliance-history route rather than the status route: another path
    wrote a stop decision, so decide_action() now returns
    blocked_already_stopped and the queued action must not fire.
    """
    conn = empty_db
    oid = "opp_stopped_by_decision"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])
    conn.execute(
        """
        INSERT INTO recovery_decisions
        (opportunity_id, action_type, outcome, reasoning, triggered_by, timestamp)
        VALUES (?, 'stop', 'executed', 'ceiling reached', 'rule', ?)
        """,
        (oid, int(time.time()) - HOUR))
    conn.commit()

    run_dispatch_cycle(now=NOW, conn=conn)

    row = _execution(conn, execution["execution_id"])
    assert row["state"] == "cancelled", row
    assert "blocked_already_stopped" in row["state_reason"]
    assert _agent_messages(conn, oid) == []


@pytest.mark.gate("phase5.contact_window_revalidation")
def test_an_action_coming_due_outside_contact_hours_is_not_fired(empty_db):
    """
    Ruling A2, end to end and the reason it was not deferred. The opportunity
    was created at noon, so the pre-amendment revalidation would have judged
    the window at noon and fired. It comes due at 3am.
    """
    conn = empty_db
    oid = "opp_window_dispatch"
    opportunity, execution = _schedule(conn, oid, hour=12)

    from datetime import datetime, timedelta
    at_3am = int((datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
                  - timedelta(days=0)).timestamp())
    conn.execute(
        "UPDATE recovery_executions SET scheduled_for = ? WHERE execution_id = ?",
        (at_3am - 60, execution["execution_id"]))
    conn.execute(
        "UPDATE recovery_decisions SET timestamp = ? WHERE decision_id = ?",
        (at_3am - 2 * 24 * HOUR, execution["decision_id"]))
    conn.commit()

    run_dispatch_cycle(now=at_3am, conn=conn)

    row = _execution(conn, execution["execution_id"])
    assert row["state"] == "cancelled", row
    assert "blocked_contact_hours" in row["state_reason"], row["state_reason"]
    assert _agent_messages(conn, oid) == []


# --------------------------------------------------------------------------
# Gate: Idempotent dispatch
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.idempotent_dispatch")
def test_sweeping_the_same_due_row_twice_produces_one_execution(empty_db, capsys):
    """
    The gate as originally specified: run the sweep twice over the same due
    row, confirm exactly one execution and one customer-visible action.
    """
    conn = empty_db
    oid = "opp_idem_0001"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])
    before = _counts(conn)

    run_dispatch_cycle(now=NOW, conn=conn)
    after_first = _execution(conn, execution["execution_id"])
    counts_first = _counts(conn)

    run_dispatch_cycle(now=NOW, conn=conn)
    after_second = _execution(conn, execution["execution_id"])
    counts_second = _counts(conn)

    messages = _agent_messages(conn, oid)
    print(f"  decisions  before={before['decisions']} "
          f"after1={counts_first['decisions']} after2={counts_second['decisions']}")
    print(f"  executions before={before['executions']} "
          f"after1={counts_first['executions']} after2={counts_second['executions']}")
    print(f"  agent messages after two sweeps: {len(messages)}")

    assert counts_second["executions"] == cfg.DISPATCH_IDEMPOTENCY_EXPECTED_ROWS
    assert len(messages) == cfg.DISPATCH_IDEMPOTENCY_EXPECTED_ROWS
    assert counts_second["decisions"] == counts_first["decisions"], (
        "the second sweep minted a decision row -- execute_action() was "
        "called instead of the row being advanced")
    assert after_second == after_first, (
        "the second sweep mutated an already-dispatched row")


@pytest.mark.gate("phase5.idempotent_dispatch")
def test_concurrent_dispatchers_produce_exactly_one_contact(db_path, capsys):
    """
    The barrier-forced version, which is what actually establishes the
    property. Two sweeps on separate connections released simultaneously onto
    the same due row.

    Reports raw counts across trials rather than a verdict.
    """
    from backend.db import db as db_module

    trials = 25
    workers = 2
    dispatched_counts, message_counts = [], []

    for trial in range(trials):
        conn = db_module.get_connection()
        db_module.create_schema(conn)
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM recovery_executions")
        conn.execute("DELETE FROM recovery_decisions")
        conn.execute("DELETE FROM recovery_candidates")
        conn.execute("DELETE FROM opportunities")
        conn.commit()

        oid = f"opp_race_{trial}"
        _, execution = _schedule(conn, oid)
        _make_due(conn, execution["execution_id"])
        conn.close()

        barrier = threading.Barrier(workers)
        outcomes = []
        lock = threading.Lock()

        def worker():
            own = db_module.get_connection()
            try:
                barrier.wait()
                res = run_dispatch_cycle(now=NOW, conn=own)
                with lock:
                    outcomes.append(res)
            except Exception as exc:            # pragma: no cover - diagnostic
                with lock:
                    outcomes.append(exc)
            finally:
                own.close()

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        check = db_module.get_connection()
        fired = sum(1 for res in outcomes if isinstance(res, list)
                    and any(r.get("dispatched") for r in res))
        msgs = len(_agent_messages(check, oid))
        errors = [o for o in outcomes if isinstance(o, Exception)]
        assert not errors, f"trial {trial} raised: {errors}"
        check.close()

        dispatched_counts.append(fired)
        message_counts.append(msgs)

    print(f"  {trials} trials x {workers} concurrent sweeps on one due row")
    print(f"  sweeps reporting a dispatch : {sorted(set(dispatched_counts))} "
          f"(total {sum(dispatched_counts)})")
    print(f"  agent messages per trial    : {sorted(set(message_counts))} "
          f"(total {sum(message_counts)})")

    assert set(message_counts) == {1}, (
        f"customer contacted more than once in some trial: {message_counts}")
    assert set(dispatched_counts) == {1}, (
        f"more than one sweep believed it dispatched: {dispatched_counts}")


@pytest.mark.gate("phase5.idempotent_dispatch")
def test_negative_control_without_the_claim_predicate_double_fires(empty_db, capsys, monkeypatch):
    """
    NEGATIVE CONTROL. The idempotency tests above are only worth their pass if
    they can detect the failure they claim to prevent.

    Remove the compare-and-swap predicate -- so the UPDATE succeeds whatever
    the current state -- and the same fixture must double-fire.
    """
    conn = empty_db

    def unguarded_advance(conn, execution_id, expected, nxt,
                          executed_at=None, reason=None):
        conn.execute(
            """
            UPDATE recovery_executions
            SET state = ?, executed_at = COALESCE(?, executed_at),
                state_reason = COALESCE(?, state_reason)
            WHERE execution_id = ?
            """,
            (nxt, executed_at, reason, execution_id))
        return True

    oid = "opp_negctl_0001"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])

    monkeypatch.setattr(ds, "_advance", unguarded_advance)
    # Re-queue between sweeps to model the second dispatcher seeing the row
    # before the first completed it -- the interleaving the CAS defends.
    run_dispatch_cycle(now=NOW, conn=conn)
    conn.execute("UPDATE recovery_executions SET state = 'scheduled' "
                 "WHERE execution_id = ?", (execution["execution_id"],))
    conn.commit()
    run_dispatch_cycle(now=NOW, conn=conn)

    messages = _agent_messages(conn, oid)
    print(f"  negative control: agent messages = {len(messages)} (expected 2)")
    assert len(messages) == 2, (
        "the negative control did not reproduce a double contact, so the "
        "idempotency tests above prove nothing")


# --------------------------------------------------------------------------
# Structural gates -- the two hard constraints, enforced mechanically
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.idempotent_dispatch")
def test_the_dispatcher_never_calls_execute_action_or_inserts(source_files):
    """
    CONSTRAINT 1, mechanically. execute_action() is not idempotent at the call
    level, so the dispatcher must advance an existing row with an UPDATE and
    never re-execute. Convention is not enough: this is the property the whole
    idempotency gate rests on.
    """
    import re

    path = next(p for p in source_files if p.name == "dispatch_scheduled.py")
    code = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#"))
    body = code.split('"""')[-1]          # drop the module docstring

    assert not re.search(r"\bexecute_action\s*\(", body), (
        "the dispatcher calls execute_action(), which mints a second decision "
        "and a second execution rather than advancing the queued one")
    assert not re.search(r"INSERT\s+INTO\s+recovery_(decisions|executions)",
                         body, re.I), (
        "the dispatcher inserts into a recovery table; it may only UPDATE")


@pytest.mark.gate("phase5.idempotent_dispatch")
def test_every_lifecycle_write_is_a_compare_and_swap(source_files):
    """Each UPDATE of recovery_executions must carry a state precondition."""
    import re

    path = next(p for p in source_files if p.name == "dispatch_scheduled.py")
    text = path.read_text(encoding="utf-8")
    # Drop the module docstring: it contains an illustrative UPDATE written in
    # placeholder form, which is prose about the rule, not a statement the
    # module executes.
    body = text.split('"""', 2)[-1]
    updates = re.findall(
        r"UPDATE\s+recovery_executions.*?(?=\"\"\"|;)", body, re.S | re.I)
    assert updates, "expected at least one lifecycle UPDATE"
    for stmt in updates:
        assert re.search(r"WHERE.*state\s*=\s*\?", stmt, re.S | re.I), (
            f"lifecycle UPDATE without a state precondition:\n{stmt}")


@pytest.mark.gate("phase5.optimizer_boundary")
def test_the_dispatcher_does_not_call_the_optimizer(source_files):
    """
    CONSTRAINT 2, mechanically. The optimizer costs ~650ms against a ~6ms lock
    hold; opportunity_lock.py records that putting it inside takes the number
    of workers that can queue before a timeout from ~850 to ~7. The dispatcher
    sidesteps the question entirely by never ranking: the action it fires was
    ranked and authorised at schedule time.
    """
    import re

    path = next(p for p in source_files if p.name == "dispatch_scheduled.py")
    code = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#"))
    body = code.split('"""')[-1]

    assert not re.search(r"\boptimize_opportunity\s*\(", body)
    assert "optimize" not in body.lower() or "optimizer" not in body.lower(), (
        "the dispatcher appears to reach for the optimizer")


# --------------------------------------------------------------------------
# Gate: Execution separation / vocabulary
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.execution_separation")
def test_every_state_the_dispatcher_writes_is_in_the_closed_vocabulary():
    for state in (ds.CLAIMABLE_STATE, ds.CLAIMED_STATE, ds.COMPLETED_STATE,
                  ds.ABANDONED_STATE):
        assert state in EXECUTION_STATES, state


@pytest.mark.gate("phase5.execution_separation")
def test_no_compliance_outcome_is_ever_written_as_a_lifecycle_state(empty_db):
    """
    The two closed vocabularies share the token 'executed' and nothing else.
    A dispatcher that wrote a DECISION_OUTCOMES value into
    recovery_executions.state would collapse the decision/execution
    separation the schema exists to hold apart.
    """
    conn = empty_db
    oid = "opp_vocab_0001"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])
    conn.execute("UPDATE opportunities SET status = 'stopped' "
                 "WHERE opportunity_id = ?", (oid,))
    conn.commit()

    run_dispatch_cycle(now=NOW, conn=conn)

    states = {r["state"] for r in conn.execute(
        "SELECT state FROM recovery_executions").fetchall()}
    leaked = states & (set(DECISION_OUTCOMES) - set(EXECUTION_STATES))
    assert not leaked, f"compliance outcomes written as lifecycle states: {leaked}"
    assert states <= set(EXECUTION_STATES)


@pytest.mark.gate("phase5.execution_separation")
def test_the_abandonment_reason_is_free_text_not_a_compliance_token(empty_db):
    """
    state_reason may quote a compliance outcome inside a sentence, but the
    column must never be usable as a compliance field -- i.e. never hold a
    bare DECISION_OUTCOMES value that a query could join on.
    """
    conn = empty_db
    oid = "opp_vocab_0002"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])
    conn.execute("UPDATE opportunities SET status = 'recovered' "
                 "WHERE opportunity_id = ?", (oid,))
    conn.commit()

    run_dispatch_cycle(now=NOW, conn=conn)

    reason = _execution(conn, execution["execution_id"])["state_reason"]
    assert reason
    assert reason not in DECISION_OUTCOMES, (
        "state_reason holds a bare compliance token, which makes the "
        "lifecycle table queryable as if it were the compliance table")


# --------------------------------------------------------------------------
# Gate: Method change
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.method_change")
def test_the_dispatcher_cannot_fire_a_method_change(empty_db):
    """
    Re-verified against the now-integrated code, as the gate requires. A
    method change is a retry carrying a method other than the opportunity's
    current one; the dispatcher fires whatever decide_action() re-approves,
    and decide_action() never approves one.
    """
    conn = empty_db
    oid = "opp_method_0001"
    make_opportunity(conn, oid, event_type="payment_failed",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    conn.execute(
        """
        INSERT INTO payments (id, opportunity_id, amount, currency,
                              status, method, created_at)
        VALUES ('pay_m1', ?, 5000, 'INR', 'failed', 'card', ?)
        """,
        (oid, recent_in_window_ts(days_ago=0, hour=12)))
    conn.commit()

    candidate_id = _candidate(conn, oid, action="retry", timing="4h")
    conn.execute("UPDATE recovery_candidates SET method = 'upi' "
                 "WHERE candidate_id = ?", (candidate_id,))
    conn.commit()

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    result = execute_action(opportunity, {
        "action_type": "retry", "allowed": True, "outcome": "executed",
        "reasoning": "queued", "triggered_by": "rule",
        "candidate_id": candidate_id}, conn)
    execution = dict(conn.execute(
        "SELECT * FROM recovery_executions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone())
    _make_due(conn, execution["execution_id"])

    run_dispatch_cycle(now=NOW, conn=conn)

    # Whatever the dispatcher did, no message may carry a method switch and
    # the candidate's method must not have been applied to any payment.
    methods = {r["method"] for r in conn.execute(
        "SELECT method FROM payments WHERE opportunity_id = ?", (oid,)).fetchall()}
    assert methods == {"card"}, (
        f"the dispatcher changed the payment method autonomously: {methods}")


# --------------------------------------------------------------------------
# Ruling A4 -- the stuck-row question, answered explicitly
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.scheduling")
def test_a_delivery_error_leaves_the_row_claimed_and_never_retried(empty_db, monkeypatch):
    """
    Ruling A4's error path, made explicit rather than assumed.

    If delivery raises, the row stays 'dispatched' with executed_at NULL. A
    later sweep does NOT retry it, because the sweep selects only 'scheduled'.
    That is at-most-once by choice: a claimed row may already have reached the
    customer, so retrying is the duplicate contact the CAS exists to prevent.
    The row stays visible through stuck_dispatches().
    """
    conn = empty_db
    oid = "opp_stuck_0001"
    opportunity, execution = _schedule(conn, oid)
    _make_due(conn, execution["execution_id"])

    def boom(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(ds, "deliver_recovery_message", boom)
    results = run_dispatch_cycle(now=NOW, conn=conn)

    row = _execution(conn, execution["execution_id"])
    assert row["state"] == "dispatched", row
    assert row["executed_at"] is None
    assert "delivery raised" in results[0]["reason"]

    stuck = stuck_dispatches(conn)
    assert [s["execution_id"] for s in stuck] == [execution["execution_id"]]

    # A later, healthy sweep must not pick it up again.
    monkeypatch.undo()
    assert run_dispatch_cycle(now=NOW, conn=conn) == []
    assert _execution(conn, execution["execution_id"])["state"] == "dispatched"
    assert _agent_messages(conn, oid) == []


# --------------------------------------------------------------------------
# End-to-end: the whole point of W6
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.scheduling")
def test_a_queued_action_contacts_the_customer_exactly_once_end_to_end(empty_db):
    """
    Schedule, wait, dispatch. Exactly one contact, and it happens at dispatch
    time -- not at schedule time (ruling A7) and not twice (the CAS).
    """
    conn = empty_db
    oid = "opp_e2e_0001"
    opportunity, execution = _schedule(conn, oid)

    assert _agent_messages(conn, oid) == [], (
        "the customer was contacted at schedule time")

    _make_due(conn, execution["execution_id"])
    run_dispatch_cycle(now=NOW, conn=conn)
    run_dispatch_cycle(now=NOW, conn=conn)

    messages = _agent_messages(conn, oid)
    assert len(messages) == 1, f"expected exactly one contact, got {len(messages)}"
    assert _execution(conn, execution["execution_id"])["state"] == "executed"
