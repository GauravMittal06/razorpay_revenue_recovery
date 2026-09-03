"""
Core loop: event -> classify -> decide_action -> execute_action -> log.
Runs end-to-end across all open/recovering opportunities.

Phase 1 (Schema Foundation): iterates opportunities (the economic object
the loop reasons about), not payments. Each opportunity's latest payment
attempt is fetched alongside it and passed through as context (method,
currency) -- it never drives compliance branching, only ML features and
message phrasing.
"""

from backend.db.db import get_connection
from backend.engine.pipeline import run_recovery_pipeline

# Which entry point this is, for the shared pipeline's lock and optimizer
# policy tables. "batch" is asynchronous, so it is one of the two entry
# points the optimizer may be enabled for once the latency budget is met.
ENTRY_POINT = "batch"


def _latest_payment(opportunity_id: str, conn):
    row = conn.execute(
        "SELECT * FROM payments WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def run_cycle():
    conn = get_connection()

    rows = conn.execute(
        "SELECT * FROM opportunities WHERE status IN ('open', 'recovering')"
    ).fetchall()
    opportunities = [dict(r) for r in rows]

    results = []
    for opportunity in opportunities:
        latest_payment = _latest_payment(opportunity["opportunity_id"], conn)
        # W7: the whole classify -> optimize -> authorize -> execute ->
        # message sequence now lives in ONE shared function, called by this
        # loop, trigger_event.py and handle_customer_reply.py alike. The lock
        # boundary, the optimizer's position outside it, and the decision_id
        # that delivery needs are all decided there, so this entry point
        # cannot drift away from the other two.
        outcome = run_recovery_pipeline(
            opportunity, conn,
            entry_point=ENTRY_POINT,
            latest_payment=latest_payment,
        )
        results.append(outcome["execution_result"])

    conn.close()
    return results


if __name__ == "__main__":
    # Run as: python -m backend.engine.core_loop  (from the directory
    # containing backend/), now that imports are backend.-prefixed.
    results = run_cycle()
    for r in results:
        print(f"{r['opportunity_id']} | {(r['action_type'] or 'none'):10s} | {r['outcome']:22s} | {r['reasoning']}")
    print(f"\nProcessed {len(results)} opportunities.")
