"""
Serialises the read-decide-write cycle for one opportunity.

THE DEFECT THIS EXISTS FOR
    decide_action() enforces cooldown by *reading* prior decisions. Under two
    overlapping workers -- a cron cycle starting before the previous one
    finished, or two API callers on the same case -- both can read "no recent
    contact" before either writes, so both decide to act and both execute. The
    customer is contacted twice.

    Measured, four workers released from a barrier onto one opportunity:

        decisions produced: {('retry', 'executed'): 4}
        EXECUTED customer-contact actions: 4

    Cooldown is not wrong; it is simply not atomic. Reading it and acting on it
    are two steps, and nothing stopped a second worker slipping between them.

WHAT THIS DOES
    Wraps that whole cycle in one IMMEDIATE transaction. SQLite grants the
    RESERVED lock to exactly one writer, so the second worker blocks at the
    BEGIN (up to db.BUSY_TIMEOUT_MS) rather than proceeding on a stale read.
    When it resumes, the first worker's decision is committed and visible, and
    cooldown blocks it correctly -- which is the behaviour the rule was always
    meant to have.

WHAT THIS DELIBERATELY DOES NOT DO
    It adds no compliance logic and re-derives no rule. The cooldown check
    stays exactly where it is, in decide_action(), which remains the sole
    compliance authority. This only makes that authority's read-then-act
    indivisible. A guard that re-checked cooldown here would be a second
    component enforcing the same rule -- the authority drift the project's
    invariants forbid.

WHY IT IS A SHARED HELPER AND NOT INLINE IN THE BATCH LOOP
    core_loop.py, handle_customer_reply.py and trigger_event.py are due to be
    unified into one pipeline in W7. Putting this inline in the batch loop
    would either be moved by that work or, worse, left behind while the other
    entry points stayed exposed. As a helper, W7's shared pipeline adopts it
    unchanged.

    trigger_event.py does NOT use this and does not need to: it creates a new
    opportunity per call, so concurrent calls touch different rows, and
    duplicate delivery of one upstream event is already made safe by the UNIQUE
    index on opportunities.ingestion_event_id plus its IntegrityError handler.

WHAT MUST NEVER GO INSIDE THIS LOCK
    **The optimizer.** Measured, per opportunity:

        lock hold as used today (decide_action + execute_action)
            p50   5.88 ms      p95   6.24 ms
        optimize_opportunity() alone, warm
            p50 644    ms      p95 795    ms

    Putting the ranking call inside would take the hold time from ~6 ms to
    ~650 ms, roughly 110x. Against db.BUSY_TIMEOUT_MS that is the difference
    between ~850 workers able to queue before one exceeds the timeout and
    ~7 -- i.e. between "contention is invisible" and "the eighth concurrent
    worker crashes with 'database is locked'".

    It does not need to be inside, because the optimizer is advisory and holds
    no compliance authority. The correct shape when the optimizer is enabled
    (W7) is:

        ranked = optimize_opportunity(conn, opportunity_id)   # outside, ~650ms
        with opportunity_lock(conn):                          # inside, ~6ms
            decision = decide_action(..., ranked_candidates=ranked)
            execute_action(opportunity, decision, conn)

    A ranking computed outside the lock can be stale by the time the lock is
    held, and that is safe by construction: decide_action() re-adjudicates
    every candidate against fresh state INSIDE the lock, and blocks or falls
    through if one is no longer compliant. A stale ranking can therefore cost
    optimality; it cannot cost compliance.
"""

from contextlib import contextmanager


@contextmanager
def opportunity_lock(conn):
    """
    Make one opportunity's decide-then-execute indivisible.

    Usage:

        with opportunity_lock(conn):
            decision = decide_action(...)
            result = execute_action(...)

    execute_action() commits internally, which closes this transaction; the
    exit path checks `in_transaction` rather than committing blindly, so a
    branch that wrote nothing (or one that returned before any write) does not
    raise "cannot commit - no transaction is active".

    On any exception the transaction is rolled back and the exception
    propagates -- a worker that failed mid-cycle must not leave a partial
    decision committed.
    """
    # BEGIN IMMEDIATE takes the write lock now rather than on first write, so
    # two workers cannot both get past this point holding stale reads. A plain
    # BEGIN (DEFERRED) would not help: it acquires nothing until the first
    # write, by which time both have already read.
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    else:
        if conn.in_transaction:
            conn.commit()
