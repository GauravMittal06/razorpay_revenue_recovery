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
from backend.engine.classify import classify
from backend.engine.decide_action import decide_action
from backend.engine.execute_action import execute_action
from backend.engine.deliver_message import deliver_recovery_message


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
        classification = classify(
            opportunity["event_type"],
            latest_payment.get("error_reason") if latest_payment else opportunity.get("root_cause"),
        )
        decision = decide_action(opportunity, classification, conn, latest_payment=latest_payment)
        result = execute_action(opportunity, decision, conn)
        deliver_recovery_message(opportunity, classification, decision, conn, latest_payment=latest_payment)
        results.append(result)

    conn.close()
    return results


if __name__ == "__main__":
    # Run as: python -m backend.engine.core_loop  (from the directory
    # containing backend/), now that imports are backend.-prefixed.
    results = run_cycle()
    for r in results:
        print(f"{r['opportunity_id']} | {(r['action_type'] or 'none'):10s} | {r['outcome']:22s} | {r['reasoning']}")
    print(f"\nProcessed {len(results)} opportunities.")
