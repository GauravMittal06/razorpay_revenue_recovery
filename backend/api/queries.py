"""
Phase 1 (Schema Foundation): rewired onto opportunities/recovery_decisions/
recovery_executions. The mislabeled "amount at risk" metric (sums ALL
opportunities regardless of status, not just genuinely open ones) is
DELIBERATELY preserved as-is here -- fixing it is explicitly Phase 8's job
(execution plan Section 5, Phase 8: "Retire the currently-mislabeled
'amount at risk' metric... in favor of a correctly opportunity-scoped
figure"). Re-pointing it at the new schema with equivalent semantics is a
Phase 1 concern; redesigning what it means is not.

amount_recovered now sums partial_recovery_amount rather than the full
amount, which is a new correctness gain (not a Phase 8 deferral) --
partial recovery didn't exist as a concept in the old flat schema at all,
so there is no prior behavior to preserve here.
"""

def get_cases(conn, event_type=None, status=None, outcome=None):
    query = """
        SELECT
            o.opportunity_id, o.event_type, o.root_cause, o.amount_at_risk,
            o.status, o.days_overdue, o.created_at, o.resolved_at,
            o.recovered_bool, o.partial_recovery_amount, o.recovered_at,
            o.customer_id, c.name as customer_name,
            rd.action_type, rd.outcome, rd.triggered_by,
            rd.timestamp as last_decision_timestamp, rd.flag_type
        FROM opportunities o
        LEFT JOIN customers c ON o.customer_id = c.customer_id
        LEFT JOIN recovery_decisions rd ON rd.decision_id = (
            SELECT decision_id FROM recovery_decisions
            WHERE opportunity_id = o.opportunity_id
            ORDER BY timestamp DESC LIMIT 1
        )
        WHERE 1=1
    """
    params = []
    if event_type:
        query += " AND o.event_type = ?"
        params.append(event_type)
    if status:
        query += " AND o.status = ?"
        params.append(status)
    if outcome:
        query += " AND rd.outcome = ?"
        params.append(outcome)

    cur = conn.execute(query, params)
    return [dict(row) for row in cur.fetchall()]


def get_case_detail(conn, opportunity_id):
    opportunity_row = conn.execute(
        """
        SELECT o.*, c.name as customer_name
        FROM opportunities o
        LEFT JOIN customers c ON o.customer_id = c.customer_id
        WHERE o.opportunity_id = ?
        """,
        (opportunity_id,),
    ).fetchone()

    if opportunity_row is None:
        return None

    # Every payment attempt belonging to this opportunity -- can be more
    # than one (retries against the same underlying failure), the exact
    # aggregation the schema is built to represent correctly.
    payments = conn.execute(
        """
        SELECT * FROM payments
        WHERE opportunity_id = ?
        ORDER BY created_at ASC
        """,
        (opportunity_id,),
    ).fetchall()

    decisions = conn.execute(
        """
        SELECT * FROM recovery_decisions
        WHERE opportunity_id = ?
        ORDER BY timestamp ASC
        """,
        (opportunity_id,),
    ).fetchall()

    decision_ids = [d["decision_id"] for d in decisions]
    executions = []
    if decision_ids:
        placeholders = ",".join("?" for _ in decision_ids)
        executions = conn.execute(
            f"""
            SELECT * FROM recovery_executions
            WHERE decision_id IN ({placeholders})
            ORDER BY execution_id ASC
            """,
            decision_ids,
        ).fetchall()

    messages = conn.execute(
        """
        SELECT * FROM messages
        WHERE opportunity_id = ?
        ORDER BY timestamp ASC
        """,
        (opportunity_id,),
    ).fetchall()

    return {
        "opportunity": dict(opportunity_row),
        "payments": [dict(row) for row in payments],
        "recovery_decisions": [dict(row) for row in decisions],
        "recovery_executions": [dict(row) for row in executions],
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
    total_cases = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    recovered_cases = conn.execute(
        "SELECT COUNT(*) FROM opportunities WHERE status='recovered'"
    ).fetchone()[0]
    overall_recovery_rate_pct = (
        round(recovered_cases / total_cases * 100, 1) if total_cases else 0
    )

    recovery_by_root_cause = {}
    rows = conn.execute(
        """
        SELECT root_cause,
               SUM(CASE WHEN status='recovered' THEN 1 ELSE 0 END) as recovered,
               COUNT(*) as total
        FROM opportunities
        WHERE event_type = 'payment_failed'
        GROUP BY root_cause
        """
    ).fetchall()
    found = {row["root_cause"]: row for row in rows}
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

    # partial_recovery_amount is the actual $ that came back -- a concept
    # that did not exist in the pre-Phase-1 flat schema, so this is more
    # correct than "sum full amount for recovered rows" was, not a
    # deferred-to-Phase-8 figure.
    amount_recovered = conn.execute(
        "SELECT COALESCE(SUM(partial_recovery_amount),0) FROM opportunities WHERE status='recovered'"
    ).fetchone()[0]

    # NOTE: intentionally unchanged semantics from the pre-Phase-1 metric --
    # sums ALL opportunities regardless of status, including already-
    # recovered ones. This is the exact mislabeling Phase 8 is scoped to
    # fix (see module docstring). Phase 1 only re-points it at the correct
    # table.
    amount_at_risk_total = conn.execute(
        "SELECT COALESCE(SUM(amount_at_risk),0) FROM opportunities"
    ).fetchone()[0]
    recovery_value_pct = (
        round(amount_recovered / amount_at_risk_total * 100, 1)
        if amount_at_risk_total
        else 0
    )
    current_amount_exposed = conn.execute(
        "SELECT COALESCE(SUM(amount_at_risk),0) FROM opportunities WHERE status != 'recovered'"
    ).fetchone()[0]

    unresolved_exceptions_count = conn.execute(
        "SELECT COUNT(*) FROM recovery_decisions WHERE outcome='flagged_manual_review'"
    ).fetchone()[0]

    flag_rows = conn.execute(
        """
        SELECT flag_type, COUNT(*) as cnt
        FROM recovery_decisions
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
        FROM opportunities
        WHERE status = 'recovered' AND recovered_at IS NOT NULL
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
