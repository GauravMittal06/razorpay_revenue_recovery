def get_cases(conn, event_type=None, recovery_status=None, outcome=None):
    query = """
        SELECT
            p.id, p.entity, p.amount, p.currency, p.status,
            p.event_type, p.recovery_status, p.days_overdue,
            p.customer_id, c.name as customer_name,
            ra.action_type, ra.outcome, ra.triggered_by,
            ra.timestamp as last_action_timestamp, ra.flag_type
        FROM payments p
        LEFT JOIN customers c ON p.customer_id = c.customer_id
        LEFT JOIN recovery_actions ra ON ra.action_id = (
            SELECT action_id FROM recovery_actions
            WHERE payment_id = p.id
            ORDER BY timestamp DESC LIMIT 1
        )
        WHERE 1=1
    """
    params = []
    if event_type:
        query += " AND p.event_type = ?"
        params.append(event_type)
    if recovery_status:
        query += " AND p.recovery_status = ?"
        params.append(recovery_status)
    if outcome:
        query += " AND ra.outcome = ?"
        params.append(outcome)

    cur = conn.execute(query, params)
    return [dict(row) for row in cur.fetchall()]

def get_case_detail(conn, payment_id):
    payment_row = conn.execute(
        """
        SELECT p.*, c.name as customer_name
        FROM payments p
        LEFT JOIN customers c ON p.customer_id = c.customer_id
        WHERE p.id = ?
        """,
        (payment_id,),
    ).fetchone()

    if payment_row is None:
        return None

    actions = conn.execute(
        """
        SELECT * FROM recovery_actions
        WHERE payment_id = ?
        ORDER BY timestamp ASC
        """,
        (payment_id,),
    ).fetchall()

    messages = conn.execute(
        """
        SELECT * FROM messages
        WHERE payment_id = ?
        ORDER BY timestamp ASC
        """,
        (payment_id,),
    ).fetchall()

    return {
        "payment": dict(payment_row),
        "recovery_actions": [dict(row) for row in actions],
        "messages": [dict(row) for row in messages],
    }

ROOT_CAUSES = [
    "insufficient_funds",
    "payment_declined",
    "gateway_timeout",
    "authentication_failed",
    "expired_card",
    "network_error",
]

FLAG_TYPES = ["mismatch", "root_cause_update_candidate", "dispute_flag"]


def get_metrics(conn):
    total_cases = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    recovered_cases = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE recovery_status='recovered'"
    ).fetchone()[0]
    overall_recovery_rate_pct = (
        round(recovered_cases / total_cases * 100, 1) if total_cases else 0
    )

    recovery_by_root_cause = {}
    rows = conn.execute(
        """
        SELECT error_reason,
               SUM(CASE WHEN recovery_status='recovered' THEN 1 ELSE 0 END) as recovered,
               COUNT(*) as total
        FROM payments
        WHERE event_type = 'payment_failed'
        GROUP BY error_reason
        """
    ).fetchall()
    found = {row["error_reason"]: row for row in rows}
    for rc in ROOT_CAUSES:
        if rc in found:
            r = found[rc]
            rate = round(r["recovered"] / r["total"] * 100, 1) if r["total"] else 0
            recovery_by_root_cause[rc] = {
                "recovered": r["recovered"],
                "total": r["total"],
                "recovery_rate_pct": rate,
            }
        else:
            recovery_by_root_cause[rc] = {
                "recovered": 0,
                "total": 0,
                "recovery_rate_pct": 0,
            }

    amount_recovered = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE recovery_status='recovered'"
    ).fetchone()[0]
    amount_at_risk_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments"
    ).fetchone()[0]
    recovery_value_pct = (
        round(amount_recovered / amount_at_risk_total * 100, 1)
        if amount_at_risk_total
        else 0
    )
    current_amount_exposed = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE recovery_status != 'recovered'"
    ).fetchone()[0]

    unresolved_exceptions_count = conn.execute(
        "SELECT COUNT(*) FROM recovery_actions WHERE outcome='flagged_manual_review'"
    ).fetchone()[0]

    flag_rows = conn.execute(
        """
        SELECT flag_type, COUNT(*) as cnt
        FROM recovery_actions
        WHERE outcome='flagged_manual_review'
        GROUP BY flag_type
        """
    ).fetchall()
    flag_found = {row["flag_type"]: row["cnt"] for row in flag_rows}
    unresolved_exceptions_by_flag_type = {}
    for ft in FLAG_TYPES:
        unresolved_exceptions_by_flag_type[ft] = flag_found.get(ft, 0)
    unresolved_exceptions_by_flag_type["none"] = flag_found.get(None, 0)

    EVENT_TYPES = ["checkout_abandoned", "payment_failed", "invoice_overdue"]

    time_to_recovery_distribution = {
        et: {"<1d": 0, "1-3d": 0, "3-7d": 0, "7d+": 0, "recovered_count": 0}
        for et in EVENT_TYPES
    }

    recovered_rows = conn.execute(
        """
        SELECT event_type, created_at, recovered_at
        FROM payments
        WHERE recovery_status = 'recovered' AND recovered_at IS NOT NULL
        """
    ).fetchall()

    for row in recovered_rows:
        et = row["event_type"]
        if et not in time_to_recovery_distribution:
            continue  # unexpected event_type, skip rather than crash
        delta = row["recovered_at"] - row["created_at"]
        if delta < 86400:
            bucket = "<1d"
        elif delta < 259200:
            bucket = "1-3d"
        elif delta < 604800:
            bucket = "3-7d"
        else:
            bucket = "7d+"
        time_to_recovery_distribution[et][bucket] += 1
        time_to_recovery_distribution[et]["recovered_count"] += 1
    
    return {
        "overall_recovery_rate_pct": overall_recovery_rate_pct,
        "recovery_by_root_cause": recovery_by_root_cause,
        "amount_recovered": amount_recovered,
        "amount_at_risk_total": amount_at_risk_total,
        "recovery_value_pct": recovery_value_pct,
        "current_amount_exposed": current_amount_exposed,
        "unresolved_exceptions_count": unresolved_exceptions_count,
        "unresolved_exceptions_by_flag_type": unresolved_exceptions_by_flag_type,
        "time_to_recovery_distribution": time_to_recovery_distribution,
    }