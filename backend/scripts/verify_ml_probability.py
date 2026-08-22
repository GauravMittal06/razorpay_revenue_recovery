"""
Read-only verification script. Confirms ml_recovery_probability is
being computed and stored on executed decisions after the ML
signal-injection patch.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "recovery.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT payment_id, action_type, outcome, ml_recovery_probability
        FROM recovery_actions
        WHERE outcome = 'executed'
        ORDER BY action_id ASC
        """
    ).fetchall()

    null_count = 0
    for r in rows:
        d = dict(r)
        if d["ml_recovery_probability"] is None:
            null_count += 1
        print(d)

    print(f"\nTotal executed rows: {len(rows)}")
    print(f"NULL ml_recovery_probability: {null_count}")
    print(f"Populated: {len(rows) - null_count}")

    conn.close()


if __name__ == "__main__":
    main()