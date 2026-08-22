"""
SQLite DB setup + loader.
Schema locked in PROJECT_SOURCE_OF_TRUTH.md section 6.
Run this once to create db/recovery.db and load data/*.json into it.
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "recovery.db"
DATA_DIR = BASE_DIR / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT,
    payment_history_score REAL,
    past_recovery_rate REAL,
    preferred_channel TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    entity TEXT,
    amount INTEGER,
    currency TEXT,
    status TEXT,
    order_id TEXT,
    invoice_id TEXT,
    method TEXT,
    email TEXT,
    contact TEXT,
    error_code TEXT,
    error_description TEXT,
    error_source TEXT,
    error_step TEXT,
    error_reason TEXT,
    created_at INTEGER,
    event_type TEXT,
    recovery_status TEXT,
    customer_id TEXT,
    days_overdue INTEGER,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS recovery_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT,
    action_type TEXT,
    timestamp INTEGER,
    triggered_by TEXT,
    reasoning TEXT,
    outcome TEXT,
    ml_recovery_probability REAL,
    FOREIGN KEY (payment_id) REFERENCES payments(id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT,
    sender TEXT,
    content TEXT,
    intent_extracted TEXT,
    timestamp INTEGER,
    FOREIGN KEY (payment_id) REFERENCES payments(id)
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def load_customers(conn):
    with open(DATA_DIR / "customers.json") as f:
        customers = json.load(f)

    conn.executemany(
        """
        INSERT OR REPLACE INTO customers
        (customer_id, name, payment_history_score, past_recovery_rate, preferred_channel)
        VALUES (:customer_id, :name, :payment_history_score, :past_recovery_rate, :preferred_channel)
        """,
        customers,
    )
    conn.commit()
    return len(customers)


def load_payments(conn):
    with open(DATA_DIR / "payments.json") as f:
        payments = json.load(f)

    conn.executemany(
        """
        INSERT OR REPLACE INTO payments
        (id, entity, amount, currency, status, order_id, invoice_id, method, email, contact,
         error_code, error_description, error_source, error_step, error_reason,
         created_at, event_type, recovery_status, customer_id, days_overdue)
        VALUES
        (:id, :entity, :amount, :currency, :status, :order_id, :invoice_id, :method, :email, :contact,
         :error_code, :error_description, :error_source, :error_step, :error_reason,
         :created_at, :event_type, :recovery_status, :customer_id, :days_overdue)
        """,
        payments,
    )
    conn.commit()
    return len(payments)


def main():
    conn = get_connection()
    create_schema(conn)
    n_customers = load_customers(conn)
    n_payments = load_payments(conn)
    conn.close()

    print(f"DB ready at {DB_PATH}")
    print(f"Loaded {n_customers} customers, {n_payments} payments")
    print("recovery_actions, messages tables created empty")


if __name__ == "__main__":
    main()