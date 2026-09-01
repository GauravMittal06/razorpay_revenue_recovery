"""
SQLite DB setup + loader.
Schema: Revenue Recovery Intelligence Engine execution plan, Section 3
(Data Model) / Section 5, Phase 1 (Schema Foundation).

Phase 1 retires the old flat schema's single overloaded `recovery_actions`
table and its payment-row-is-the-case assumption. Three separations are
structural here, not conventions a caller has to remember:
  - a payment (one transactional attempt) is distinct from an opportunity
    (the economic situation it belongs to -- many payments can aggregate
    under one opportunity, e.g. three retries against the same failure);
  - a decision (was this compliant) is distinct from an execution (has it
    actually fired);
  - both of those are distinct from the business outcome, which lives on
    the opportunity itself (recovered_bool / recovered_at / time_to_recovery
    / resolution_type), not on any per-action row.

Run this once to create db/recovery.db and load data/*.json into it.
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "recovery.db"
DATA_DIR = BASE_DIR / "data"

# Closed outcome vocabulary for recovery_decisions.outcome (execution plan
# Section 3). Enforced by application code (decide_action.py), listed here
# as the authoritative reference for what a migration/structural test
# should expect -- SQLite has no native enum type.
DECISION_OUTCOMES = (
    "executed",
    "blocked_cooldown",
    "blocked_max_retries",
    "blocked_contact_hours",
    "blocked_already_escalated",
    "blocked_already_stopped",
    "flagged_manual_review",
)

# Closed state vocabulary for recovery_executions.state.
EXECUTION_STATES = (
    "pending", "scheduled", "dispatched", "executed", "cancelled",
    "superseded", "failed",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchants (
    merchant_id TEXT PRIMARY KEY,
    name TEXT,
    cohort TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    merchant_id TEXT,
    name TEXT,
    payment_history_score REAL,
    past_recovery_rate REAL,
    preferred_channel TEXT,
    FOREIGN KEY (merchant_id) REFERENCES merchants(merchant_id)
);

-- The economic object the entire loop reasons about: one row per distinct
-- revenue-at-risk situation, not per payment attempt.
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    merchant_id TEXT,
    customer_id TEXT,
    event_type TEXT,               -- checkout_abandoned | payment_failed | invoice_overdue
    root_cause TEXT,                -- meaningful only for payment_failed
    amount_at_risk INTEGER,
    days_overdue INTEGER,           -- event-specific timing input for invoice_overdue
    status TEXT,                    -- open | recovering | escalated | stopped | recovered
    created_at INTEGER,
    resolved_at INTEGER,
    recovered_bool INTEGER,         -- 0/1, NULL until resolved
    partial_recovery_amount INTEGER,
    recovered_at INTEGER,
    time_to_recovery INTEGER,       -- seconds, derived: recovered_at - created_at
    resolution_type TEXT,           -- recovered | stopped | escalated_resolved | NULL if still open
    -- Idempotency key for event ingestion (nullable -- Live Agent Console
    -- manual triggers have no upstream event id to dedupe against; only
    -- delivered events that carry one can be deduplicated). UNIQUE via the
    -- index below, not inline, so multiple NULLs are still allowed.
    ingestion_event_id TEXT,
    FOREIGN KEY (merchant_id) REFERENCES merchants(merchant_id),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

-- SQLite treats every NULL as distinct for UNIQUE purposes, so this only
-- rejects two opportunities sharing the same *non-null* event id -- exactly
-- the "duplicate-delivered event" case, without blocking manual triggers
-- that have no event id at all.
CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_ingestion_event_id
    ON opportunities(ingestion_event_id);

-- Transactional/event log. Many rows can belong to one opportunity.
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
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
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
);

-- Every candidate the optimizer considered for an opportunity, not just the
-- winner. Structurally present from Phase 1 onward; populated for real
-- starting in Phase 4 (Optimizer). Left empty here is a deliberate choice --
-- fabricating predicted_eiv etc. before a model exists would be exactly the
-- kind of fake derived-truth this project's own discipline forbids.
CREATE TABLE IF NOT EXISTS recovery_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL,
    action_type TEXT,
    timing TEXT,
    method TEXT,
    channel TEXT,
    predicted_p_treated REAL,
    predicted_p_baseline REAL,
    predicted_expected_amount_treated REAL,
    predicted_expected_amount_baseline REAL,
    cost REAL,
    predicted_eiv REAL,
    rank INTEGER,
    pruned_stage TEXT,
    selected INTEGER,               -- 0/1
    created_at INTEGER,
    -- Phase 4: the carried-forward Phase 3 near-tie disclosure. DISPLAY AND
    -- DOWNSTREAM-CONSUMPTION ONLY -- written after ranking is complete and
    -- never read back by the ranking code, which is a pure function of
    -- predicted_eiv. Present so the Control Tower can distinguish "this
    -- candidate clearly leads" from "these top candidates are within noise
    -- of each other" (Phase 3 hand-off section 3).
    eiv_confidence TEXT,            -- 'high' | 'low' | NULL for unscored rows
    eiv_confidence_reason TEXT,     -- 'near_tie' | 'phase3_flagged_bucket' |
                                    -- both, '+'-joined | NULL
    eiv_gap_to_next REAL,           -- EIV gap to the next-ranked scored
                                    -- candidate; NULL for the last-ranked row
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
);

-- Compliance adjudication only. Closed outcome vocabulary (DECISION_OUTCOMES
-- above). This table's job ends at "was this compliant" -- it never records
-- whether an approved action has actually fired (that's recovery_executions)
-- and never records whether money came back (that's on opportunities).
CREATE TABLE IF NOT EXISTS recovery_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL,
    candidate_id INTEGER,           -- nullable until Phase 4 populates real candidates
    action_type TEXT,
    outcome TEXT NOT NULL,
    reasoning TEXT,
    triggered_by TEXT,
    ml_recovery_probability REAL,
    flag_type TEXT,
    timestamp INTEGER,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id),
    FOREIGN KEY (candidate_id) REFERENCES recovery_candidates(candidate_id)
);

-- One decision per candidate. SQLite allows unlimited NULLs through a
-- UNIQUE index (candidate_id is NULL for every Phase 1 decision, since
-- real candidates don't exist until Phase 4) -- this only starts rejecting
-- once a second decision tries to reference the same real candidate_id.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_decisions_candidate_id
    ON recovery_decisions(candidate_id);

-- Execution lifecycle state, entirely separate from the compliance outcome
-- above. A decision can be outcome='executed' (compliant, cleared to fire)
-- while its execution is still 'pending' or 'scheduled' -- that distinction
-- is the whole point of keeping these tables apart.
CREATE TABLE IF NOT EXISTS recovery_executions (
    execution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,
    state TEXT NOT NULL,            -- one of EXECUTION_STATES
    scheduled_for INTEGER,
    executed_at INTEGER,
    channel TEXT,
    FOREIGN KEY (decision_id) REFERENCES recovery_decisions(decision_id)
);

-- One execution row per decision -- a decision's execution state mutates
-- in place (pending -> scheduled -> ... -> executed), it is never
-- represented as multiple rows. execute_action.py already only ever
-- inserts once per decision; this makes that a schema guarantee instead
-- of an assumption about caller behavior.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_executions_decision_id
    ON recovery_executions(decision_id);

-- Live control/treatment holdout. Structurally present from Phase 1;
-- populated starting Phase 6 (Live Experiment Assignment).
CREATE TABLE IF NOT EXISTS experiment_assignment (
    opportunity_id TEXT PRIMARY KEY,
    "group" TEXT,                   -- control | treatment
    assigned_at INTEGER,
    assignment_method TEXT,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
);

-- Network-health signal source. Structurally present from Phase 1;
-- populated starting Phase 2 (Data Factory) / consumed from Phase 3 onward.
CREATE TABLE IF NOT EXISTS bank_health_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank TEXT,
    method TEXT,
    psp TEXT,
    window_start INTEGER,
    window_end INTEGER,
    success_rate REAL,
    timeout_rate REAL,
    health_score REAL
);

-- Conversation thread, spanning the whole opportunity (not one payment
-- attempt -- a customer's reply concerns the situation, not a specific
-- retry).
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL,
    sender TEXT,
    content TEXT,
    intent_extracted TEXT,
    intent_confidence REAL,
    mentioned_reason TEXT,
    timestamp INTEGER,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
);

-- Reproducibility manifest for every Data Factory generation run.
-- Structurally present from Phase 1; populated starting Phase 2.
CREATE TABLE IF NOT EXISTS dataset_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_name TEXT,
    version TEXT,
    seed INTEGER,
    calibration_profile TEXT,
    generator_version TEXT,
    row_count INTEGER,
    case_count INTEGER,
    validator_results TEXT,         -- JSON blob
    created_at INTEGER
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


def load_merchants(conn):
    path = DATA_DIR / "merchants.json"
    if not path.exists():
        return 0
    with open(path) as f:
        merchants = json.load(f)
    conn.executemany(
        """
        INSERT OR REPLACE INTO merchants (merchant_id, name, cohort)
        VALUES (:merchant_id, :name, :cohort)
        """,
        merchants,
    )
    conn.commit()
    return len(merchants)


def load_customers(conn):
    with open(DATA_DIR / "customers.json") as f:
        customers = json.load(f)

    conn.executemany(
        """
        INSERT OR REPLACE INTO customers
        (customer_id, merchant_id, name, payment_history_score, past_recovery_rate, preferred_channel)
        VALUES (:customer_id, :merchant_id, :name, :payment_history_score, :past_recovery_rate, :preferred_channel)
        """,
        customers,
    )
    conn.commit()
    return len(customers)


def load_opportunities(conn):
    with open(DATA_DIR / "opportunities.json") as f:
        opportunities = json.load(f)

    # ingestion_event_id is a live-trigger concept (see trigger_event.py) --
    # seed/demo data was never "delivered" as an event, so it has none.
    # Defaulting missing keys to None keeps this loader backward compatible
    # with seed files generated before this column existed.
    for o in opportunities:
        o.setdefault("ingestion_event_id", None)

    conn.executemany(
        """
        INSERT OR REPLACE INTO opportunities
        (opportunity_id, merchant_id, customer_id, event_type, root_cause,
         amount_at_risk, days_overdue, status, created_at, resolved_at,
         recovered_bool, partial_recovery_amount, recovered_at,
         time_to_recovery, resolution_type, ingestion_event_id)
        VALUES
        (:opportunity_id, :merchant_id, :customer_id, :event_type, :root_cause,
         :amount_at_risk, :days_overdue, :status, :created_at, :resolved_at,
         :recovered_bool, :partial_recovery_amount, :recovered_at,
         :time_to_recovery, :resolution_type, :ingestion_event_id)
        """,
        opportunities,
    )
    conn.commit()
    return len(opportunities)


def load_payments(conn):
    with open(DATA_DIR / "payments.json") as f:
        payments = json.load(f)

    conn.executemany(
        """
        INSERT OR REPLACE INTO payments
        (id, opportunity_id, entity, amount, currency, status, order_id, invoice_id,
         method, email, contact, error_code, error_description, error_source,
         error_step, error_reason, created_at)
        VALUES
        (:id, :opportunity_id, :entity, :amount, :currency, :status, :order_id, :invoice_id,
         :method, :email, :contact, :error_code, :error_description, :error_source,
         :error_step, :error_reason, :created_at)
        """,
        payments,
    )
    conn.commit()
    return len(payments)


def main():
    conn = get_connection()
    create_schema(conn)
    n_merchants = load_merchants(conn)
    n_customers = load_customers(conn)
    n_opportunities = load_opportunities(conn)
    n_payments = load_payments(conn)
    conn.close()

    print(f"DB ready at {DB_PATH}")
    print(f"Loaded {n_merchants} merchants, {n_customers} customers, "
          f"{n_opportunities} opportunities, {n_payments} payments")
    print("recovery_candidates, recovery_decisions, recovery_executions, "
          "experiment_assignment, bank_health_observations, messages, "
          "dataset_registry created empty")


if __name__ == "__main__":
    main()