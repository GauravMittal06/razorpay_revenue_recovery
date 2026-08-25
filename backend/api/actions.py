"""
Thin write-side API wrappers for the Live Agent Console.
Every function here delegates to an existing engine function.
No compliance/decision logic lives here.
"""

from backend.engine.trigger_event import trigger_event as _trigger_event
from backend.engine.handle_customer_reply import handle_customer_reply as _handle_customer_reply
from backend.engine.mark_payment_recovered import mark_payment_recovered as _mark_payment_recovered

def trigger_event(event_type, amount, conn, root_cause=None, customer_id=None, days_overdue=None):
    return _trigger_event(
        event_type=event_type,
        amount=amount,
        conn=conn,
        root_cause=root_cause,
        customer_id=customer_id,
        days_overdue=days_overdue,
    )


def submit_reply(payment_id, message, conn):
    return _handle_customer_reply(payment_id, message, conn)


def simulate_recovery(payment_id, conn):
    return _mark_payment_recovered(payment_id, conn)


def get_audit_feed(conn, limit=20):
    rows = conn.execute(
        """
        SELECT ra.action_id, ra.payment_id, ra.action_type, ra.timestamp,
               ra.triggered_by, ra.reasoning, ra.outcome, ra.flag_type,
               p.event_type, p.amount
        FROM recovery_actions ra
        JOIN payments p ON p.id = ra.payment_id
        ORDER BY ra.timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]