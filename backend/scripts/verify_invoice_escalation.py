"""
Verification script: confirms invoice_overdue escalation uses
days_overdue (not created_at age), per the point-5 fix.
Read-only -- does not modify the DB.
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "recovery.db"
AUTO_STOP_DAYS = 7
DAY_SECONDS = 86400

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

payments = conn.execute(
    "SELECT id, days_overdue, created_at, recovery_status FROM payments WHERE event_type = 'invoice_overdue'"
).fetchall()

now = int(time.time())

print(f"{'payment_id':<25} {'days_overdue':<13} {'age_days':<10} {'status':<12} {'expected_escalate_by_days_overdue'}")
for p in payments:
    age_days = round((now - p["created_at"]) / DAY_SECONDS, 1)
    expected = (p["days_overdue"] or 0) >= AUTO_STOP_DAYS
    print(f"{p['id']:<25} {str(p['days_overdue']):<13} {age_days:<10} {p['recovery_status']:<12} {expected}")

# cross-check against actual recovery_actions log
print("\n--- actual escalate actions logged for these payments ---")
for p in payments:
    action = conn.execute(
        "SELECT action_type, outcome, reasoning FROM recovery_actions WHERE payment_id = ? ORDER BY timestamp DESC LIMIT 1",
        (p["id"],),
    ).fetchone()
    if action:
        print(f"{p['id']} | days_overdue={p['days_overdue']} | {action['action_type']} | {action['outcome']}")

conn.close()