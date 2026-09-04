"""
The counterfactual-consistency report.

EXECUTION_PLAN Phase 6: "A counterfactual-consistency test: control-group
opportunities never show a selected, executed action past their assignment
point."

--------------------------------------------------------------------------
Why the treatment arm is measured too
--------------------------------------------------------------------------
"Control shows nothing" is unfalsifiable on its own. A system that acted on
nobody at all -- a broken optimizer, an executor that silently no-ops, a
misconfigured entry point -- would pass a control-only check perfectly. The
same probes are therefore run against the treatment arm as a NEGATIVE CONTROL,
and the gate requires them to be non-zero there.

Without that half, this gate would certify a system that does nothing as
having perfect control suppression.

--------------------------------------------------------------------------
"past their assignment point"
--------------------------------------------------------------------------
Every probe is bounded at `assigned_at`. Activity strictly before assignment
is not a violation -- an opportunity can legitimately have been acted on
before it entered the experiment. In this system that window is empty by
construction, because assignment happens at creation before the first
decision, but the probes are written to the plan's wording rather than to the
current implementation's guarantees: if assignment ever moved, this report
would still be measuring the right thing.

`recovery_candidates.selected` carries no timestamp of its own, so it is
joined through the decision that set it, which does.
"""

from backend.engine import phase6_config as _cfg

# Each probe: a label and the SQL counting violations for one arm. Every query
# takes (arm,) and counts rows at or after the opportunity's assigned_at.
PROBES = {
    "selected_candidates": """
        SELECT COUNT(*) FROM recovery_candidates c
        JOIN experiment_assignment a ON a.opportunity_id = c.opportunity_id
        JOIN recovery_decisions d ON d.candidate_id = c.candidate_id
        WHERE a."group" = ? AND c.selected = 1
          AND d.timestamp >= a.assigned_at
    """,
    "executed_decisions": """
        SELECT COUNT(*) FROM recovery_decisions d
        JOIN experiment_assignment a ON a.opportunity_id = d.opportunity_id
        WHERE a."group" = ? AND d.outcome = 'executed'
          AND d.timestamp >= a.assigned_at
    """,
    "dispatched_or_executed": """
        SELECT COUNT(*) FROM recovery_executions e
        JOIN recovery_decisions d ON d.decision_id = e.decision_id
        JOIN experiment_assignment a ON a.opportunity_id = d.opportunity_id
        WHERE a."group" = ? AND e.state IN ('dispatched', 'executed')
          AND d.timestamp >= a.assigned_at
    """,
    "outbound_messages": """
        SELECT COUNT(*) FROM messages m
        JOIN experiment_assignment a ON a.opportunity_id = m.opportunity_id
        WHERE a."group" = ? AND m.sender != 'customer'
          AND m.timestamp >= a.assigned_at
    """,
}

# Not a violation probe -- a completeness one. A control arm that produced no
# decision rows at all would mean suppression was implemented by returning
# early and logging nothing, which breaks the invariant that every declined
# action is logged with a reason.
SUPPRESSION_PROBE = """
    SELECT COUNT(*) FROM recovery_decisions d
    JOIN experiment_assignment a ON a.opportunity_id = d.opportunity_id
    WHERE a."group" = ? AND d.outcome = ?
"""


def consistency_report(conn) -> dict:
    """Raw per-probe counts for both arms. No verdict."""
    report = {"arms": {}, "suppression_rows": {}, "n": {}}
    for arm in _cfg.GROUPS:
        report["arms"][arm] = {
            name: conn.execute(sql, (arm,)).fetchone()[0]
            for name, sql in PROBES.items()
        }
        report["suppression_rows"][arm] = conn.execute(
            SUPPRESSION_PROBE, (arm, _cfg.SUPPRESSION_OUTCOME)).fetchone()[0]
        report["n"][arm] = conn.execute(
            'SELECT COUNT(*) FROM experiment_assignment WHERE "group" = ?',
            (arm,)).fetchone()[0]
    return report


def verdict(report: dict) -> dict:
    reasons = []

    control = report["arms"][_cfg.CONTROL_GROUP]
    for name, count in control.items():
        if count != _cfg.COUNTERFACTUAL_CONTROL_EXPECTED:
            reasons.append(
                f"control arm shows {count} {name} at or after assignment; "
                f"expected {_cfg.COUNTERFACTUAL_CONTROL_EXPECTED}")

    # The negative control. Without it, a system that acts on nobody passes.
    treatment = report["arms"][_cfg.TREATMENT_GROUP]
    acted = sum(treatment.values())
    if acted < _cfg.COUNTERFACTUAL_TREATMENT_MIN:
        reasons.append(
            "treatment arm shows no activity either, so the control result is "
            "unfalsifiable -- a system acting on nobody would pass this gate")

    if report["n"][_cfg.CONTROL_GROUP] == 0:
        reasons.append("control arm is empty; nothing was suppressed to check")

    suppressed = report["suppression_rows"][_cfg.CONTROL_GROUP]
    if report["n"][_cfg.CONTROL_GROUP] and suppressed == 0:
        reasons.append(
            "control arm has no suppression decision rows; suppression that "
            "logs nothing breaks the invariant that every declined action is "
            "logged with a reason")

    return {"verdict": "FAIL" if reasons else "PASS", "reasons": reasons}


def format_report(report: dict, decision: dict) -> str:
    lines = ["COUNTERFACTUAL CONSISTENCY -- RAW COUNTS",
             f"  {'probe':<26} {'control':>10} {'treatment':>12}"]
    for name in PROBES:
        lines.append(f"  {name:<26} "
                     f"{report['arms'][_cfg.CONTROL_GROUP][name]:>10} "
                     f"{report['arms'][_cfg.TREATMENT_GROUP][name]:>12}")
    lines.append(f"  {'-- opportunities':<26} "
                 f"{report['n'][_cfg.CONTROL_GROUP]:>10} "
                 f"{report['n'][_cfg.TREATMENT_GROUP]:>12}")
    lines.append(f"  {'-- suppression rows':<26} "
                 f"{report['suppression_rows'][_cfg.CONTROL_GROUP]:>10} "
                 f"{report['suppression_rows'][_cfg.TREATMENT_GROUP]:>12}")
    lines.append(f"\n  control must be 0 on every probe; treatment must be "
                 f">= {_cfg.COUNTERFACTUAL_TREATMENT_MIN} in total")
    lines.append(f"  VERDICT: {decision['verdict']}")
    for reason in decision["reasons"]:
        lines.append(f"    - {reason}")
    return "\n".join(lines)
