"""
trigger_event(): Live Agent Console event trigger.
Creates one real opportunity (the revenue-at-risk situation) plus its first
payments row (the transactional attempt that revealed it), then runs that
opportunity through the exact same shared pipeline core_loop.py uses
per-item: classify() -> decide_action() -> execute_action() ->
deliver_recovery_message().

Not a second pipeline. core_loop.py, classify.py, decide_action.py,
execute_action.py, deliver_message.py are all untouched -- this function
only sequences the same four calls once, on one new opportunity, the same
way core_loop.py's loop body already does per opportunity.

Phase 1 (Schema Foundation): every incoming event creates a new opportunity
here. Real deduplication against an existing open opportunity for the same
underlying *situation* ("Detect", plan Section 2.2 step 1 -- e.g. noticing
two different failures belong to the same customer's same order) is
explicitly a later-phase concern (Phase 2+ candidate/optimizer
infrastructure) and is NOT what this function does.

What this function DOES do: protect against the same event being
*delivered* more than once by an upstream at-least-once delivery system
(a real, narrower, Phase-1-appropriate concern). Callers that have a
stable upstream event id should pass it as `event_id`. If an opportunity
already exists with that `event_id`, this returns the existing opportunity
untouched instead of creating a duplicate -- no second pipeline run, no
second payment row. Callers with no stable event id (e.g. a human manually
triggering a test event from the Live Agent Console) simply omit it, in
which case no dedup is possible or attempted -- there is no way to
distinguish two genuinely separate manual triggers from an accidental
double-click without a stable key, and this function does not guess.
"""

import time
import uuid
import sqlite3

from backend.engine.pipeline import run_recovery_pipeline

# Request-synchronous entry point: the optimizer stays disabled here while
# the ~750ms latency budget is unmet, and it does NOT use opportunity_lock
# (see ENTRY_POINTS_USING_OPPORTUNITY_LOCK for why the asymmetry is correct).
ENTRY_POINT = "trigger_event"

VALID_EVENT_TYPES = {"checkout_abandoned", "payment_failed", "invoice_overdue"}
VALID_ROOT_CAUSES = {
    "insufficient_funds",
    "payment_declined",
    "gateway_timeout",
    "authentication_failed",
    "expired_card",
    "network_error",
}


def _new_opportunity_id():
    return "opp_" + uuid.uuid4().hex[:12]


def _new_payment_id():
    return "pay_" + uuid.uuid4().hex[:12]


def trigger_event(event_type: str, amount: int, conn,
                   root_cause: str = None,
                   customer_id: str = None,
                   days_overdue: int = None,
                   event_id: str = None) -> dict:
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

    # Idempotent replay: if this exact upstream event was already ingested,
    # return the existing opportunity rather than creating a duplicate.
    # Checked *before* the customer_id lookup below so a duplicate-delivered
    # event short-circuits cheaply without touching any other table.
    if event_id is not None:
        existing = conn.execute(
            "SELECT * FROM opportunities WHERE ingestion_event_id = ?", (event_id,)
        ).fetchone()
        if existing is not None:
            return {
                "status": "duplicate_event_ignored",
                "event_id": event_id,
                "opportunity": dict(existing),
            }

    merchant_id = None
    if customer_id is not None:
        row = conn.execute(
            "SELECT customer_id, merchant_id FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            return {
                "status": "invalid_customer_id",
                "error": f"No customer found with id={customer_id}",
            }
        merchant_id = row["merchant_id"]

    opportunity_id = _new_opportunity_id()
    payment_id = _new_payment_id()
    now = int(time.time())

    opportunity = {
        "opportunity_id": opportunity_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "event_type": event_type,
        "root_cause": root_cause if event_type == "payment_failed" else None,
        "amount_at_risk": amount,
        "days_overdue": days_overdue,
        "status": "open",
        "created_at": now,
        "resolved_at": None,
        "recovered_bool": None,
        "partial_recovery_amount": None,
        "recovered_at": None,
        "time_to_recovery": None,
        "resolution_type": None,
        "ingestion_event_id": event_id,
    }

    try:
        conn.execute(
            """
            INSERT INTO opportunities
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
            opportunity,
        )
    except sqlite3.IntegrityError:
        # The SELECT-based check above is a cheap optimization, not the
        # actual guarantee -- it has a check-then-insert race window. The
        # real guarantee is the UNIQUE index on ingestion_event_id: if a
        # concurrent call for the same event_id won that race, this INSERT
        # fails here instead of silently creating a duplicate. Resolve the
        # same way as the pre-check path: return the row that actually won.
        if event_id is not None:
            winner = conn.execute(
                "SELECT * FROM opportunities WHERE ingestion_event_id = ?", (event_id,)
            ).fetchone()
            if winner is not None:
                return {
                    "status": "duplicate_event_ignored",
                    "event_id": event_id,
                    "opportunity": dict(winner),
                }
        raise

    payment = {
        "id": payment_id,
        "opportunity_id": opportunity_id,
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
    }

    conn.execute(
        """
        INSERT INTO payments
        (id, opportunity_id, entity, amount, currency, status, order_id, invoice_id,
         method, email, contact, error_code, error_description, error_source,
         error_step, error_reason, created_at)
        VALUES
        (:id, :opportunity_id, :entity, :amount, :currency, :status, :order_id, :invoice_id,
         :method, :email, :contact, :error_code, :error_description, :error_source,
         :error_step, :error_reason, :created_at)
        """,
        payment,
    )
    conn.commit()

    # W7: the identical shared pipeline core_loop.py and
    # handle_customer_reply.py run -- one function, not a re-sequencing of
    # the same calls. Everything above this line (validation, the
    # duplicate-event short-circuit, the opportunity and payment INSERTs) is
    # this entry point's own work and stays here; only the recovery pipeline
    # is shared.
    #
    # Note this entry point is deliberately absent from
    # ENTRY_POINTS_USING_OPPORTUNITY_LOCK, so the pipeline runs it without
    # the lock: a fresh opportunity_id per call means concurrent calls touch
    # different rows, and duplicate delivery of one upstream event is already
    # guarded by the UNIQUE index on ingestion_event_id plus the
    # IntegrityError handler above.
    outcome = run_recovery_pipeline(
        opportunity, conn,
        entry_point=ENTRY_POINT,
        latest_payment=payment,
    )
    classification = outcome["classification"]
    decision = outcome["decision"]
    result = outcome["execution_result"]
    delivery = outcome["delivery"]

    return {
        "status": "ok",
        "opportunity": opportunity,
        "payment": payment,
        "classification": classification,
        "decision": decision,
        "execution_result": result,
        "delivery": delivery,
    }