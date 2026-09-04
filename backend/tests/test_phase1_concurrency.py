"""
Concurrency and idempotency gates.

The permanent gate is: "every state-writing operation is idempotent, or
concurrency-safe, or documented as neither -- and the claim is tested."
Phase 1's version of it is stronger: the guarantee must come from the
schema (a UNIQUE index, a PRIMARY KEY), not from a caller checking first,
because a check-then-write is exactly the pattern that survives every
single-threaded test and fails under two workers.

These tests therefore use real threads against the same SQLite file, with a
`threading.Barrier` so the writes are released simultaneously rather than
merely issued from different threads. A race test can pass by luck; it can
never fail by luck, so a failure here is a genuine finding.
"""

import sqlite3
import threading

import pytest

from backend.tests.conftest import (insert_decision, make_opportunity,
                                    recent_in_window_ts)

THREADS = 8


def _run_in_parallel(fn, n=THREADS):
    """Release n workers simultaneously and collect (result, exception) pairs."""
    barrier = threading.Barrier(n)
    results, errors = [], []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        try:
            value = fn(index)
        except Exception as exc:  # recorded, never swallowed
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker thread deadlocked"
    return results, errors


# --------------------------------------------------------------------------
# Duplicate event ingestion
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.race_safety")
def test_replayed_event_is_ignored_sequentially(seeded_db):
    """Baseline: the cheap pre-check path works when there is no contention."""
    from backend.engine.trigger_event import trigger_event

    first = trigger_event("payment_failed", 12_345, seeded_db,
                          root_cause="gateway_timeout", event_id="evt_dup_seq_1")
    second = trigger_event("payment_failed", 12_345, seeded_db,
                           root_cause="gateway_timeout", event_id="evt_dup_seq_1")

    assert first["status"] == "ok", first
    assert second["status"] == "duplicate_event_ignored", second
    assert second["opportunity"]["opportunity_id"] == first["opportunity"]["opportunity_id"]

    count = seeded_db.execute(
        "SELECT COUNT(*) FROM opportunities WHERE ingestion_event_id = ?",
        ("evt_dup_seq_1",)).fetchone()[0]
    assert count == 1


@pytest.mark.gate("phase1.race_safety")
def test_concurrent_delivery_of_the_same_event_creates_one_opportunity(seeded_db):
    """
    The SELECT-then-INSERT pre-check in trigger_event() has a race window by
    construction; the actual guarantee is the UNIQUE index on
    ingestion_event_id. This is the test that distinguishes the two: under
    simultaneous delivery, exactly one caller may be told "ok".
    """
    from backend.db.db import get_connection
    from backend.engine.trigger_event import trigger_event

    event_id = "evt_dup_race_1"

    def attempt(_index):
        conn = get_connection()
        try:
            return trigger_event("payment_failed", 9_900, conn,
                                 root_cause="gateway_timeout", event_id=event_id)
        finally:
            conn.close()

    results, errors = _run_in_parallel(attempt)
    assert not errors, f"unhandled exceptions during concurrent ingestion: {errors}"

    statuses = [r["status"] for r in results]
    assert statuses.count("ok") == 1, (
        f"{statuses.count('ok')} callers were told the event was accepted; "
        f"exactly one may be. Statuses: {sorted(statuses)}")
    assert set(statuses) <= {"ok", "duplicate_event_ignored"}, sorted(set(statuses))

    stored = seeded_db.execute(
        "SELECT COUNT(*) FROM opportunities WHERE ingestion_event_id = ?",
        (event_id,)).fetchone()[0]
    assert stored == 1, f"{stored} opportunities share ingestion_event_id={event_id!r}"


@pytest.mark.gate("phase1.race_safety")
def test_concurrent_distinct_events_all_persist(seeded_db):
    """
    The mirror of the test above: dedup must not silently drop legitimate
    concurrent events. Losing writes here would look like a clean pass on
    every uniqueness assertion.
    """
    from backend.db.db import get_connection
    from backend.engine.trigger_event import trigger_event

    def attempt(index):
        conn = get_connection()
        try:
            return trigger_event("payment_failed", 1_000 + index, conn,
                                 root_cause="gateway_timeout",
                                 event_id=f"evt_distinct_{index}")
        finally:
            conn.close()

    results, errors = _run_in_parallel(attempt)
    assert not errors, f"unhandled exceptions: {errors}"
    assert all(r["status"] == "ok" for r in results), \
        f"some distinct events were rejected: {[r['status'] for r in results]}"

    stored = seeded_db.execute(
        "SELECT COUNT(*) FROM opportunities WHERE ingestion_event_id LIKE 'evt_distinct_%'"
    ).fetchone()[0]
    assert stored == THREADS, f"{stored} of {THREADS} concurrent events persisted"


# --------------------------------------------------------------------------
# Experiment assignment under concurrent writes
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.experiment_assignment")
def test_concurrent_assignment_cannot_place_one_case_in_both_arms(seeded_db):
    """
    Phase 1 gate, concurrent clause. An opportunity assigned to both control
    and treatment does not merely add noise -- it makes the incremental
    estimate the experiment exists to produce uninterpretable, and nothing
    downstream would notice.
    """
    from backend.db.db import get_connection

    opp = make_opportunity(seeded_db, opportunity_id="opp_exp_race_0001")
    now = recent_in_window_ts()

    def assign(index):
        group = "treatment" if index % 2 == 0 else "control"
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO experiment_assignment (opportunity_id, \"group\", "
                "assigned_at, assignment_method) VALUES (?, ?, ?, 'race-test')",
                (opp["opportunity_id"], group, now))
            conn.commit()
            return "inserted"
        except sqlite3.IntegrityError:
            return "rejected"
        finally:
            conn.close()

    results, errors = _run_in_parallel(assign)
    assert not errors, f"unexpected exceptions: {errors}"
    assert results.count("inserted") == 1, \
        f"{results.count('inserted')} assignments accepted; the PRIMARY KEY must admit one"

    rows = seeded_db.execute(
        "SELECT \"group\" FROM experiment_assignment WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchall()
    assert len(rows) == 1, f"case is in {len(rows)} arms: {[tuple(r) for r in rows]}"


# --------------------------------------------------------------------------
# Schema-level uniqueness for decisions and executions
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.race_safety")
def test_one_decision_per_candidate_is_enforced_by_index(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_uniq_dec_0001")
    seeded_db.execute(
        "INSERT INTO recovery_candidates (opportunity_id, action_type, selected, "
        "created_at) VALUES (?, 'retry', 1, ?)",
        (opp["opportunity_id"], recent_in_window_ts()))
    seeded_db.commit()
    candidate_id = seeded_db.execute(
        "SELECT candidate_id FROM recovery_candidates WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone()[0]

    insert_decision(seeded_db, opp["opportunity_id"], "retry",
                    candidate_id=candidate_id)
    with pytest.raises(sqlite3.IntegrityError):
        insert_decision(seeded_db, opp["opportunity_id"], "retry",
                        candidate_id=candidate_id)
    seeded_db.rollback()

    assert seeded_db.execute(
        "SELECT COUNT(*) FROM recovery_decisions WHERE candidate_id = ?",
        (candidate_id,)).fetchone()[0] == 1


@pytest.mark.gate("phase1.race_safety")
def test_null_candidate_ids_are_still_permitted_many_times(seeded_db):
    """
    The uniqueness guarantee must not have been bought by forbidding the
    NULLs every Phase 1 decision carries -- that would have broken the
    pipeline instead of protecting it.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_uniq_null_0001")
    for _ in range(3):
        insert_decision(seeded_db, opp["opportunity_id"], "retry", candidate_id=None)
    assert seeded_db.execute(
        "SELECT COUNT(*) FROM recovery_decisions WHERE opportunity_id = ? "
        "AND candidate_id IS NULL", (opp["opportunity_id"],)).fetchone()[0] == 3


@pytest.mark.gate("phase1.race_safety")
def test_one_execution_row_per_decision_is_enforced_by_index(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_uniq_exec_0001")
    decision_id = insert_decision(seeded_db, opp["opportunity_id"], "retry")

    seeded_db.execute(
        "INSERT INTO recovery_executions (decision_id, state) VALUES (?, 'pending')",
        (decision_id,))
    seeded_db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute(
            "INSERT INTO recovery_executions (decision_id, state) VALUES (?, 'dispatched')",
            (decision_id,))
        seeded_db.commit()
    seeded_db.rollback()

    rows = seeded_db.execute(
        "SELECT state FROM recovery_executions WHERE decision_id = ?",
        (decision_id,)).fetchall()
    assert len(rows) == 1, (
        "an execution's lifecycle must mutate in place, not accumulate rows: "
        f"{[tuple(r) for r in rows]}")


# --------------------------------------------------------------------------
# Recovery marking: the one money-writing operation in Phase 1
#
# This is the operation where a correctness bug is most expensive, because
# `partial_recovery_amount` feeds every reported rupee. It is also the one
# state-writing operation whose guarantee is NOT backed by a constraint --
# it is a read-then-write in application code -- so it gets both a
# probabilistic race test and a deterministic structural one.
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.race_safety")
def test_marking_recovered_twice_is_rejected_the_second_time(seeded_db):
    """Sequential idempotency: the second caller must be told it lost."""
    from backend.engine.mark_opportunity_recovered import \
        mark_opportunity_recovered

    opp = make_opportunity(seeded_db, opportunity_id="opp_recov_seq_0001",
                           amount_at_risk=50_000)

    first = mark_opportunity_recovered(opp["opportunity_id"], seeded_db,
                                      partial_recovery_amount=50_000)
    second = mark_opportunity_recovered(opp["opportunity_id"], seeded_db,
                                       partial_recovery_amount=50_000)

    assert first["status"] == "ok", first
    assert second["status"] == "already_recovered", (
        "a replayed recovery confirmation was accepted a second time: "
        f"{second}")

    row = seeded_db.execute(
        "SELECT status, partial_recovery_amount FROM opportunities "
        "WHERE opportunity_id = ?", (opp["opportunity_id"],)).fetchone()
    assert row["status"] == "recovered"
    assert row["partial_recovery_amount"] == 50_000, (
        "recovered amount was double-counted or overwritten: "
        f"{row['partial_recovery_amount']}")


@pytest.mark.gate("phase1.race_safety")
def test_recovery_update_is_guarded_by_the_status_it_read(backend_dir):
    """
    Deterministic companion to the race test below.

    The recovery write SELECTs the status, decides, and then issues an UPDATE.
    In SQLite the only way to make that atomic without an explicit transaction
    is to repeat the precondition in the UPDATE's WHERE clause and check
    `rowcount` -- a compare-and-swap. This test reads the source rather than
    the behaviour because a race test can only ever *sometimes* observe the
    defect, while the missing guard is visible every time.

    AMENDMENT, Phase 6 / X4, locked 2026-09-04.
        Originally this read mark_opportunity_recovered.py, which owned the
        write. X4 made observe_outcome() the single ingestion path for every
        business outcome, and mark_opportunity_recovered became a thin
        labelled wrapper with no SQL of its own -- so the original file scope
        found no UPDATE at all and failed on its own `assert updates`.

        The guarded property did not change; it moved, and the compare-and-swap
        adopted was this one, unchanged. (execute_action()'s terminal `stop`
        branch was the OTHER former writer and had no such guard -- unifying
        on the guarded implementation is what closed that divergence.)

        NOT a weakening. The check now follows the write to whichever module
        owns it, and adds a second assertion the original could not make: the
        wrapper must contain no outcome write of its own. A future edit that
        reintroduced a direct unguarded UPDATE in either file fails here.
    """
    import ast

    writer = backend_dir / "engine" / "observe_outcome.py"
    tree = ast.parse(writer.read_text(encoding="utf-8"))

    updates = [n for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and "UPDATE opportunities" in n.value]
    assert updates, (
        f"no UPDATE against opportunities found in {writer.name}; the single "
        "outcome-ingestion path must be the module that owns this write")

    unguarded = [u.value for u in updates
                 if "status" not in u.value.split("WHERE", 1)[-1]]
    assert not unguarded, (
        "the recovery UPDATE does not repeat the status precondition it just "
        "read, so two concurrent callers can both pass the check and both be "
        "told \"ok\". Needed: `... WHERE opportunity_id = ? AND status NOT IN "
        "('recovered', 'stopped')` plus a cursor.rowcount check.\n"
        + "\n".join(f"  {sql.strip()}" for sql in unguarded))

    # The wrapper must delegate, not write. Anything else is a second route
    # back, and it would not be covered by the guard checked above.
    wrapper = backend_dir / "engine" / "mark_opportunity_recovered.py"
    wrapper_tree = ast.parse(wrapper.read_text(encoding="utf-8"))
    wrapper_updates = [n for n in ast.walk(wrapper_tree)
                       if isinstance(n, ast.Constant)
                       and isinstance(n.value, str)
                       and "UPDATE opportunities" in n.value]
    assert not wrapper_updates, (
        f"{wrapper.name} writes opportunities directly again; it must delegate "
        "to observe_outcome(), the single ingestion path")


@pytest.mark.gate("phase1.race_safety")
def test_concurrent_recovery_confirmations_produce_one_winner(seeded_db):
    """
    Behavioural form of the same defect. Two callers being told "ok" for one
    recovery is how the same rupees get counted twice by an upstream ledger
    that trusts the return value -- the DB row itself still looks clean,
    which is what makes this class of bug survive inspection.

    May pass by luck; a failure is conclusive.
    """
    from backend.db.db import get_connection
    from backend.engine.mark_opportunity_recovered import \
        mark_opportunity_recovered

    opp = make_opportunity(seeded_db, opportunity_id="opp_recov_race_0001",
                           amount_at_risk=50_000)

    def confirm(_index):
        conn = get_connection()
        try:
            return mark_opportunity_recovered(opp["opportunity_id"], conn,
                                             partial_recovery_amount=50_000)
        finally:
            conn.close()

    results, errors = _run_in_parallel(confirm)
    assert not errors, f"unhandled exceptions during concurrent marking: {errors}"

    statuses = [r["status"] for r in results]
    assert statuses.count("ok") == 1, (
        f"{statuses.count('ok')} callers were told the recovery was recorded; "
        f"exactly one may be. Statuses: {sorted(statuses)}")


@pytest.mark.gate("phase1.race_safety")
def test_a_stopped_opportunity_cannot_be_marked_recovered(seeded_db):
    """
    The compliance-relevant half: `stop` is a terminal decision, and a
    recovery confirmation arriving afterwards must not resurrect the case.
    """
    from backend.engine.mark_opportunity_recovered import \
        mark_opportunity_recovered

    opp = make_opportunity(seeded_db, opportunity_id="opp_recov_stop_0001",
                           status="stopped")
    result = mark_opportunity_recovered(opp["opportunity_id"], seeded_db,
                                       partial_recovery_amount=50_000)

    assert result["status"] == "rejected_stopped", result
    assert seeded_db.execute(
        "SELECT status FROM opportunities WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone()[0] == "stopped"


# --------------------------------------------------------------------------
# Batch loop re-entrancy
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.race_safety")
def test_two_overlapping_batch_cycles_do_not_double_act_on_one_case(seeded_db):
    """
    The realistic operational failure: a cron-triggered cycle overlapping the
    previous one because the first has not finished. Cooldown is the intended
    protection, but cooldown is a read of prior decisions -- so under overlap
    both workers can read "no recent contact" and both act.

    Asserted as an upper bound on executed customer-contact actions per
    opportunity rather than on decision count, because a *blocked* second
    decision is the correct, expected outcome and must not fail this test.
    """
    from backend.db.db import get_connection
    from backend.engine.core_loop import run_cycle

    def cycle(_index):
        conn = get_connection()
        try:
            return len(run_cycle())
        finally:
            conn.close()

    _results, errors = _run_in_parallel(cycle, n=2)
    assert not errors, (
        "an overlapping batch cycle raised instead of degrading gracefully. "
        "'database is locked' here means concurrent cycles are unsafe rather "
        f"than merely redundant: {errors}")

    offenders = seeded_db.execute(
        """
        SELECT d.opportunity_id, COUNT(*) AS n
        FROM recovery_decisions d
        JOIN recovery_executions e ON e.decision_id = d.decision_id
        WHERE d.action_type IN ('retry', 'reminder')
        GROUP BY d.opportunity_id
        HAVING n > 1
        """).fetchall()
    assert not offenders, (
        "the same opportunity was contacted more than once across two "
        "overlapping cycles; cooldown did not survive concurrency: "
        f"{[tuple(r) for r in offenders][:5]}")
