"""
Seed/demo dataset generator -- Phase 1 (opportunity-centric schema).

Produces merchants.json, customers.json, opportunities.json, payments.json
for db/db.py to load. This is the small, deterministic demo/evaluation
dataset referenced (but never shipped) in the existing comments in
ml/simulate_training_data.py and ml/train_risk_model.py -- distinct from,
and never used to train, the 8000-case synthetic training corpus. Hidden
state, stochastic outcome sampling, and counterfactual candidate rows
belong only to the ml/ layer (and, from Phase 2 onward, the Data Factory);
this generator exists purely to populate a runnable demo database with
plausible, varied, schema-valid rows -- including a deliberate mix of
single-attempt and multi-retry opportunities, so the opportunity/payment
aggregation the schema is built around has something real to demonstrate.

Reproducible: fixed seed + fixed generator version, printed on every run.
`created_at`/`recovered_at`/`resolved_at` are anchored to wall-clock time
at generation by default (correct for a demo dataset that should look
freshly-happened whenever it's loaded); event_type/root_cause/amount/
customer/status/method/channel/retry-count are fully determined by
RNG_SEED regardless of that anchor. Set SEED_DATA_NOW=<unix ts> for a
byte-identical rerun.
"""

import json
import os
import time
import uuid
from pathlib import Path

import numpy as np

GENERATOR_VERSION = "seed-data-v2-opportunity-schema"
RNG_SEED = 7
N_MERCHANTS = 5
N_CUSTOMERS = 40
N_OPPORTUNITIES = 150

DATA_DIR = Path(__file__).resolve().parent

MERCHANT_COHORTS = ["d2c", "smb", "enterprise", "marketplace"]
EVENT_TYPES = ["checkout_abandoned", "payment_failed", "invoice_overdue"]
EVENT_TYPE_WEIGHTS = [0.35, 0.45, 0.20]

ROOT_CAUSES = [
    "insufficient_funds", "payment_declined", "gateway_timeout",
    "authentication_failed", "expired_card", "network_error",
]
METHODS = ["card", "netbanking", "upi", "wallet"]
CHANNELS = ["email", "sms", "whatsapp"]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Ananya", "Diya", "Priya", "Saanvi", "Aadhya", "Kavya",
    "Myra", "Anika", "Riya", "Ishita",
]
LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Iyer", "Nair", "Reddy", "Rao", "Mehta",
    "Kapoor", "Joshi", "Patel", "Singh", "Das", "Bose", "Menon",
]
MERCHANT_NAMES = [
    "Kirana Direct", "Bloom & Basket", "Vertex Fitness", "Northline Logistics",
    "Paperplane Stationery",
]

DAY_SECONDS = 86400


def _rand_name(rng):
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def generate_merchants(rng):
    merchants = []
    for i in range(N_MERCHANTS):
        merchants.append({
            "merchant_id": f"merch_{i:03d}",
            "name": MERCHANT_NAMES[i % len(MERCHANT_NAMES)],
            "cohort": str(rng.choice(MERCHANT_COHORTS)),
        })
    return merchants


def generate_customers(merchants, rng):
    customers = []
    for i in range(N_CUSTOMERS):
        merchant = merchants[int(rng.integers(0, len(merchants)))]
        customers.append({
            "customer_id": f"cust_{i:04d}",
            "merchant_id": merchant["merchant_id"],
            "name": _rand_name(rng),
            "payment_history_score": round(float(np.clip(rng.beta(5, 3), 0, 1)), 3),
            "past_recovery_rate": round(float(np.clip(rng.beta(4, 3), 0, 1)), 3),
            "preferred_channel": str(rng.choice(CHANNELS)),
        })
    return customers


def _weighted_status(rng):
    """
    Mixed distribution so a fresh demo load has variety: some opportunities
    already resolved (recovered/stopped) with a plausible history, some
    routed to a human queue (escalated), most still open/recovering so
    core_loop.py has real work to do on first run.
    """
    r = rng.random()
    if r < 0.30:
        return "recovered"
    if r < 0.38:
        return "escalated"
    if r < 0.44:
        return "stopped"
    if r < 0.60:
        return "recovering"
    return "open"


def generate_opportunities_and_payments(merchants, customers, rng, now=None):
    """
    `now` anchors relative ages to a point in time -- see module docstring
    for what is and isn't controlled by RNG_SEED.
    """
    if now is None:
        now = int(time.time())

    opportunities = []
    payments = []

    for i in range(N_OPPORTUNITIES):
        opportunity_id = f"opp_seed_{i:04d}"
        event_type = str(rng.choice(EVENT_TYPES, p=EVENT_TYPE_WEIGHTS))
        customer = customers[int(rng.integers(0, len(customers)))]
        merchant_id = customer["merchant_id"]

        age_days = float(rng.uniform(0, 12))
        created_at = now - int(age_days * DAY_SECONDS)

        amount_at_risk = int(rng.integers(500, 500_000))
        status = _weighted_status(rng)

        root_cause = None
        if event_type == "payment_failed":
            root_cause = str(rng.choice(ROOT_CAUSES))

        days_overdue = None
        if event_type == "invoice_overdue":
            days_overdue = int(rng.integers(1, 25))

        # --- business outcome fields, opportunity-level only ---
        resolved_at = None
        recovered_bool = None
        partial_recovery_amount = None
        recovered_at = None
        time_to_recovery = None
        resolution_type = None

        if status == "recovered":
            recovered_at = created_at + int(rng.uniform(3600, max(3601, now - created_at)))
            resolved_at = recovered_at
            recovered_bool = 1
            time_to_recovery = recovered_at - created_at
            resolution_type = "recovered"
            # ~20% of recoveries are partial
            if rng.random() < 0.20:
                partial_recovery_amount = int(amount_at_risk * float(rng.uniform(0.3, 0.85)))
            else:
                partial_recovery_amount = amount_at_risk
        elif status == "stopped":
            resolved_at = created_at + int(rng.uniform(3600, max(3601, now - created_at)))
            recovered_bool = 0
            resolution_type = "stopped"
            partial_recovery_amount = 0
        # open / recovering / escalated: unresolved, all business-outcome
        # fields stay NULL -- there is nothing to claim yet.

        opportunities.append({
            "opportunity_id": opportunity_id,
            "merchant_id": merchant_id,
            "customer_id": customer["customer_id"],
            "event_type": event_type,
            "root_cause": root_cause,
            "amount_at_risk": amount_at_risk,
            "days_overdue": days_overdue,
            "status": status,
            "created_at": created_at,
            "resolved_at": resolved_at,
            "recovered_bool": recovered_bool,
            "partial_recovery_amount": partial_recovery_amount,
            "recovered_at": recovered_at,
            "time_to_recovery": time_to_recovery,
            "resolution_type": resolution_type,
            "ingestion_event_id": None,  # seed data isn't a delivered event
        })

        # --- payment attempts belonging to this opportunity ---
        # payment_failed opportunities that are past the "fresh" open stage
        # plausibly have multiple retry attempts logged against them --
        # this is the deliberate multi-retry fixture the schema's
        # aggregation is built to handle correctly.
        n_attempts = 1
        if event_type == "payment_failed" and status != "open":
            n_attempts = int(rng.integers(1, 4))  # 1-3 attempts

        for attempt_idx in range(n_attempts):
            attempt_created_at = created_at + attempt_idx * int(rng.uniform(3600, 26 * 3600))
            error_code = error_description = error_source = error_step = None
            if event_type == "payment_failed":
                error_code = f"BAD_REQUEST_{root_cause.upper()[:12]}"
                error_description = root_cause.replace("_", " ").capitalize()
                error_source = str(rng.choice(["customer", "bank", "gateway"]))
                error_step = str(rng.choice(["authorization", "authentication", "processing"]))

            payments.append({
                "id": f"pay_seed_{i:04d}_{attempt_idx}",
                "opportunity_id": opportunity_id,
                "entity": "payment",
                "amount": amount_at_risk,
                "currency": "INR",
                "status": "captured" if (status == "recovered" and attempt_idx == n_attempts - 1) else "failed",
                "order_id": f"order_seed_{i:04d}",
                "invoice_id": f"inv_seed_{i:04d}" if event_type == "invoice_overdue" else None,
                "method": str(rng.choice(METHODS)),
                "email": f"customer{i}@example.com",
                "contact": f"+9198{rng.integers(10000000, 99999999)}",
                "error_code": error_code,
                "error_description": error_description,
                "error_source": error_source,
                "error_step": error_step,
                "error_reason": root_cause,
                "created_at": attempt_created_at,
            })

    return opportunities, payments


def main():
    rng = np.random.default_rng(RNG_SEED)
    now_override = os.environ.get("SEED_DATA_NOW")
    now = int(now_override) if now_override else int(time.time())

    merchants = generate_merchants(rng)
    customers = generate_customers(merchants, rng)
    opportunities, payments = generate_opportunities_and_payments(merchants, customers, rng, now=now)

    multi_attempt_opps = sum(
        1 for opp in opportunities
        if sum(1 for p in payments if p["opportunity_id"] == opp["opportunity_id"]) > 1
    )

    os.makedirs(DATA_DIR, exist_ok=True)
    for name, obj in [
        ("merchants.json", merchants),
        ("customers.json", customers),
        ("opportunities.json", opportunities),
        ("payments.json", payments),
    ]:
        with open(DATA_DIR / name, "w") as f:
            json.dump(obj, f, indent=2)

    print(f"generator={GENERATOR_VERSION} seed={RNG_SEED} now={now}")
    print(f"{len(merchants)} merchants, {len(customers)} customers, "
          f"{len(opportunities)} opportunities, {len(payments)} payments")
    print(f"{multi_attempt_opps} opportunities have >1 payment attempt "
          f"(multi-retry aggregation fixture)")
    print("Note: event_type/root_cause/amount/customer/status/method/channel/retry-count")
    print("are fully determined by seed regardless of `now`; created_at/recovered_at/")
    print("resolved_at are anchored to `now`. Set SEED_DATA_NOW=<unix ts> for a")
    print("byte-identical rerun.")


if __name__ == "__main__":
    main()