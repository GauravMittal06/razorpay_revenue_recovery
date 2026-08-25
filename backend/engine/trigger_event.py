"""
trigger_event(): Live Agent Console event trigger.
Creates one real payments row, then runs that single payment through
the exact same shared pipeline core_loop.py uses per-item:
classify() -> decide_action() -> execute_action() -> deliver_recovery_message().

Not a second pipeline. core_loop.py, classify.py, decide_action.py,
execute_action.py, deliver_message.py are all untouched -- this function
only sequences the same four calls once, on one new payment, the same
way core_loop.py's loop body already does per payment.
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from engine.classify import classify
from engine.decide_action import decide_action
from engine.execute_action import execute_action
from engine.deliver_message import deliver_recovery_message

VALID_EVENT_TYPES = {"checkout_abandoned", "payment_failed", "invoice_overdue"}
VALID_ROOT_CAUSES = {
    "insufficient_funds",
    "payment_declined",
    "gateway_timeout",
    "authentication_failed",
    "expired_card",
    "network_error",
}


def _new_payment_id():
    return "pay_" + uuid.uuid4().hex[:12]


def trigger_event(event_type: str, amount: int, conn,
                   root_cause: str = None,
                   customer_id: str = None,
                   days_overdue: int = None) -> dict:
    if event_type not in VALID_EVENT_TYPES:
        return {
            "status": "invalid_event_type",
            "error": f"event_type must be one of {sorted(VALID_EVENT_TYPES)}",
        }

    if amount is None or amount <= 0:
        return {
            "status": "invalid_amount",
            "error": "amount must be greater than 0",
        }

    if days_overdue is not None and event_type != "invoice_overdue":
        return {
            "status": "invalid_days_overdue",
            "error": "days_overdue is only valid for event_type='invoice_overdue'",
        }

    if event_type == "payment_failed":
        if root_cause is None or root_cause not in VALID_ROOT_CAUSES:
            return {
                "status": "invalid_root_cause",
                "error": f"root_cause is required for payment_failed and must be one of {sorted(VALID_ROOT_CAUSES)}",
            }
    elif root_cause is not None:
        return {
            "status": "invalid_root_cause",
            "error": "root_cause is only valid for event_type='payment_failed'",
        }

    if customer_id is not None:
        row = conn.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            return {
                "status": "invalid_customer_id",
                "error": f"No customer found with id={customer_id}",
            }

    payment_id = _new_payment_id()
    now = int(time.time())

    payment = {
        "id": payment_id,
        "entity": "payment",
        "amount": amount,
        "currency": "INR",
        "status": "created",
        "order_id": None,
        "invoice_id": None,
        "method": None,
        "email": None,
        "contact": None,
        "error_code": None,
        "error_description": None,
        "error_source": None,
        "error_step": None,
        "error_reason": root_cause if event_type == "payment_failed" else None,
        "created_at": now,
        "event_type": event_type,
        "recovery_status": "open",
        "customer_id": customer_id,
        "days_overdue": days_overdue,
    }

    conn.execute(
        """
        INSERT INTO payments
        (id, entity, amount, currency, status, order_id, invoice_id, method, email, contact,
         error_code, error_description, error_source, error_step, error_reason,
         created_at, event_type, recovery_status, customer_id, days_overdue)
        VALUES
        (:id, :entity, :amount, :currency, :status, :order_id, :invoice_id, :method, :email, :contact,
         :error_code, :error_description, :error_source, :error_step, :error_reason,
         :created_at, :event_type, :recovery_status, :customer_id, :days_overdue)
        """,
        payment,
    )
    conn.commit()

    # Exact same 4 calls core_loop.py makes per payment -- same shared
    # engine, no second pipeline, no other payment touched.
    classification = classify(payment)
    decision = decide_action(payment, classification, conn)
    result = execute_action(payment, decision, conn)
    delivery = deliver_recovery_message(payment, classification, decision, conn)

    return {
        "status": "ok",
        "payment": payment,
        "classification": classification,
        "decision": decision,
        "execution_result": result,
        "delivery": delivery,
    }