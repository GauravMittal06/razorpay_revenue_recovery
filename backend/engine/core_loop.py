"""
Core loop: event -> classify -> decide_action -> execute_action -> log.
Stage 1 proof: rules only, runs end-to-end across all open/recovering payments.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from db.db import get_connection
from engine.classify import classify
from engine.decide_action import decide_action
from engine.execute_action import execute_action
from engine.deliver_message import deliver_recovery_message


def run_cycle():
    conn = get_connection()

    rows = conn.execute(
        "SELECT * FROM payments WHERE recovery_status IN ('open', 'recovering')"
    ).fetchall()
    payments = [dict(r) for r in rows]

    results = []
    for payment in payments:
        classification = classify(payment)
        decision = decide_action(payment, classification, conn)
        result = execute_action(payment, decision, conn)
        deliver_recovery_message(payment, classification, decision, conn)
        results.append(result)

    conn.close()
    return results


if __name__ == "__main__":
    results = run_cycle()
    for r in results:
        print(f"{r['payment_id']} | {r['action_type']:10s} | {r['outcome']:22s} | {r['reasoning']}")
    print(f"\nProcessed {len(results)} payments.")