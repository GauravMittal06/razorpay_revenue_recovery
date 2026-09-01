"""
Thin write-side API wrappers for the Live Agent Console.
Every function here delegates to an existing engine function.
No compliance/decision logic lives here.

Phase 1 (Schema Foundation): submit_reply/simulate_recovery/get_audit_feed
are addressed by opportunity_id now; the audit feed reads recovery_decisions
joined to recovery_executions (compliance outcome and dispatch state
separately visible), not the retired recovery_actions table.
"""

from backend.engine.trigger_event import trigger_event as _trigger_event
from backend.engine.handle_customer_reply import handle_customer_reply as _handle_customer_reply
from backend.engine.mark_opportunity_recovered import mark_opportunity_recovered as _mark_opportunity_recovered

def trigger_event(event_type, amount, conn, root_cause=None, customer_id=None, days_overdue=None, event_id=None):
    return _trigger_event(
        event_type=event_type,
        amount=amount,
        conn=conn,
        root_cause=root_cause,
        customer_id=customer_id,
        days_overdue=days_overdue,
        event_id=event_id,
    )


def submit_reply(opportunity_id, message, conn):
    return _handle_customer_reply(opportunity_id, message, conn)


def simulate_recovery(opportunity_id, conn, partial_recovery_amount=None):
    return _mark_opportunity_recovered(opportunity_id, conn, partial_recovery_amount=partial_recovery_amount)


def get_audit_feed(conn, limit=20):
    rows = conn.execute(
        """
        SELECT rd.decision_id, rd.opportunity_id, rd.action_type, rd.timestamp,
               rd.triggered_by, rd.reasoning, rd.outcome, rd.flag_type,
               o.event_type, o.amount_at_risk,
               re.state as execution_state, re.executed_at
        FROM recovery_decisions rd
        JOIN opportunities o ON o.opportunity_id = rd.opportunity_id
        LEFT JOIN recovery_executions re ON re.decision_id = rd.decision_id
        ORDER BY rd.timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]