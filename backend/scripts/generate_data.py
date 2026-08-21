"""
Synthetic data generator.
Produces data/customers.json and data/payments.json
Schema locked in PROJECT_SOURCE_OF_TRUTH.md section 6.
Do not add/remove fields without updating SoT first.
"""

import json
import random
import time
import uuid
from pathlib import Path

from faker import Faker

fake = Faker("en_IN")
random.seed(42)
Faker.seed(42)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

NUM_RECORDS = 150
NUM_CUSTOMERS = 60

EVENT_TYPES = ["checkout_abandoned", "payment_failed", "invoice_overdue"]
METHODS = ["card", "netbanking", "upi", "wallet"]

# root_cause -> error_reason mapping (locked, section 6)
ROOT_CAUSE_MAP = {
    "insufficient_funds": "insufficient_funds",
    "payment_declined": "payment_declined",
    "gateway_timeout": "gateway_timeout",
    "authentication_failed": "authentication_failed",
    "expired_card": "expired_card",
    "network_error": "network_error",
}
ERROR_REASONS = list(ROOT_CAUSE_MAP.values())

ERROR_SOURCES = ["customer", "business", "bank", "gateway"]
PREFERRED_CHANNELS = ["email", "sms", "whatsapp"]

NOW = int(time.time())
DAY = 86400


def gen_customer(customer_id: str) -> dict:
    return {
        "customer_id": customer_id,
        "name": fake.name(),
        "payment_history_score": round(random.uniform(0.0, 1.0), 2),
        "past_recovery_rate": round(random.uniform(0.0, 1.0), 2),
        "preferred_channel": random.choice(PREFERRED_CHANNELS),
    }


def gen_payment(customer_ids: list) -> dict:
    event_type = random.choice(EVENT_TYPES)
    customer_id = random.choice(customer_ids)

    # error_reason only meaningful for payment_failed (per SoT section 3)
    error_reason = None
    error_code = None
    error_description = None
    error_source = None
    error_step = None

    if event_type == "payment_failed":
        error_reason = random.choice(ERROR_REASONS)
        error_code = f"BAD_REQUEST_{error_reason.upper()}"
        error_description = error_reason.replace("_", " ").capitalize()
        error_source = random.choice(ERROR_SOURCES)
        error_step = random.choice(["payment_authentication", "payment_authorization", "payment_capture"])

    # days_overdue only meaningful for invoice_overdue
    days_overdue = random.randint(1, 45) if event_type == "invoice_overdue" else None

    created_at = NOW - random.randint(0, 10 * DAY)

    status = "failed" if event_type == "payment_failed" else random.choice(["created", "authorized"])

    payment = {
        "id": f"pay_{uuid.uuid4().hex[:14]}",
        "entity": "payment",
        "amount": random.randint(50000, 5000000),  # paise
        "currency": "INR",
        "status": status,
        "order_id": f"order_{uuid.uuid4().hex[:14]}",
        "invoice_id": f"inv_{uuid.uuid4().hex[:14]}" if event_type == "invoice_overdue" else None,
        "method": random.choice(METHODS),
        "email": fake.email(),
        "contact": fake.msisdn()[:10],
        "error_code": error_code,
        "error_description": error_description,
        "error_source": error_source,
        "error_step": error_step,
        "error_reason": error_reason,
        "created_at": created_at,
        "event_type": event_type,
        "recovery_status": "open",  # locked: no pre-baked outcomes
        "customer_id": customer_id,
        "days_overdue": days_overdue,
    }
    return payment


def main():
    customer_ids = [f"cust_{uuid.uuid4().hex[:12]}" for _ in range(NUM_CUSTOMERS)]
    customers = [gen_customer(cid) for cid in customer_ids]
    payments = [gen_payment(customer_ids) for _ in range(NUM_RECORDS)]

    with open(OUTPUT_DIR / "customers.json", "w") as f:
        json.dump(customers, f, indent=2)

    with open(OUTPUT_DIR / "payments.json", "w") as f:
        json.dump(payments, f, indent=2)

    print(f"Generated {len(customers)} customers -> data/customers.json")
    print(f"Generated {len(payments)} payments -> data/payments.json")


if __name__ == "__main__":
    main()