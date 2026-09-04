"""
Incremental recovery — the number the whole system exists to produce.

    incremental recovery rate = P(recovered | treatment) - P(recovered | control)
    incremental Rs            = mean(recovered Rs | treatment)
                              - mean(recovered Rs | control)
                              , scaled to the treated population

SCOPE, STATED BEFORE THE NUMBERS
    AGGREGATE ONLY. One figure across the whole assigned population. Segmented
    attribution -- by merchant, time window or root cause -- is NOT built, and
    the acceptance gate's per-segment underpowered-refusal test is NOT met.
    Cut for time by ruling on 2026-09-04 and disclosed rather than hidden; see
    PHASE7_NOTES.md. The minimum-N refusal IS implemented and enforced at
    aggregate scope, because a report that cannot refuse is not a report.

    Every figure here is SYNTHETIC. The outcomes it aggregates were drawn from
    the Data Factory's potential-outcome generator, not observed from any real
    payment system, and `outcome_source` records that per row. This module
    refuses to report at all if the population contains any non-synthetic
    source it was not told to expect, so the label can never drift off the
    number by accident.

THE ESTIMATOR, NAMED
    Difference in proportions for the rate, with a Wald (normal-approximation)
    confidence interval on the difference:

        se = sqrt( p_t(1-p_t)/n_t + p_c(1-p_c)/n_c )
        ci = (p_t - p_c) +/- z * se

    Difference in means for the rupee figure, with a Welch standard error
    (unpooled variances, which is correct when the arms differ in spread as
    they will here -- treatment's recovered amounts are shifted by the
    intervention).

    Chosen because it is the simplest estimator that is CORRECT for a
    completely randomized two-arm experiment with equal assignment
    probability. There is no covariate adjustment, no regression, no variance
    reduction. Randomization is what makes the naive difference unbiased; any
    adjustment would buy precision at the cost of an assumption this design
    does not need. Stated explicitly so nobody has to infer it from the code.

NOT CACHED
    Every figure is computed from a live query at call time. Nothing is stored,
    memoised or written back. `sql_trace()` returns the exact statements used,
    so any number here can be re-derived by hand against the database.
"""

import math

from backend.db import db as _db
from backend.engine import phase6_config as _cfg

# Minimum assigned opportunities per arm before ANY figure is reported.
#
# A permanent gate, not an option. Below it the module returns
# "insufficient data" rather than a number, because a confidence interval
# computed on a handful of rows is not a weak result -- it is a misleading
# one, wide enough to contain anything while still looking like a measurement.
#
# 30 per arm is the conventional floor at which the normal approximation
# behind the Wald interval is defensible at all. It is deliberately NOT tied
# to phase6_config.MIN_ASSIGNED_N (3500), which answers a different question:
# that floor is what the BALANCE gate needs to detect imbalance, this one is
# what the ESTIMATOR needs for its interval to mean anything.
MIN_N_PER_ARM = 30

# z for a two-sided 95% interval.
Z_95 = 1.959963985

# The outcome sources this module will aggregate. Anything else present in the
# population is a refusal, not a footnote: mixing a synthetic draw with a
# confirmed payment event and reporting one number would be exactly the
# "synthetic ground truth presented as real-world causal evidence" failure the
# acceptance gates name as do-not-proceed.
SYNTHETIC_SOURCES = ("synthetic_potential_outcome",)

RESOLVED_POPULATION_SQL = """
    SELECT a."group"                AS arm,
           o.opportunity_id,
           o.amount_at_risk,
           o.recovered_bool,
           COALESCE(o.partial_recovery_amount, 0) AS recovered_amount,
           o.outcome_source
    FROM opportunities o
    JOIN experiment_assignment a ON a.opportunity_id = o.opportunity_id
    WHERE o.resolution_type IS NOT NULL
"""

ASSIGNED_COUNT_SQL = """
    SELECT a."group" AS arm, COUNT(*) AS n
    FROM experiment_assignment a
    GROUP BY a."group"
"""

PREDICTED_EIV_SQL = """
    SELECT SUM(c.predicted_eiv) AS total_eiv, COUNT(*) AS n
    FROM recovery_candidates c
    JOIN experiment_assignment a ON a.opportunity_id = c.opportunity_id
    WHERE c.selected = 1 AND a."group" = ?
"""


def sql_trace() -> dict:
    """
    The exact statements every reported figure is derived from.

    The traceability gate asks that each number be reproducible from an
    inspectable live query. Returning the queries themselves is the strongest
    form of that: a reader can paste them into sqlite3 and check the module.
    """
    return {
        "resolved_population": RESOLVED_POPULATION_SQL.strip(),
        "assigned_counts": ASSIGNED_COUNT_SQL.strip(),
        "predicted_eiv_by_arm": PREDICTED_EIV_SQL.strip(),
    }


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs):
    """Sample variance, ddof=1. Zero for fewer than two observations."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def difference_in_proportions(k_t, n_t, k_c, n_c, z=Z_95):
    """
    (p_t - p_c) with a Wald interval on the difference.

    Returns (diff, lo, hi, se). The interval is symmetric and can extend
    outside [-1, 1] at tiny n -- which is precisely why MIN_N_PER_ARM exists
    rather than this function clamping and pretending otherwise.
    """
    p_t = k_t / n_t
    p_c = k_c / n_c
    se = math.sqrt(p_t * (1 - p_t) / n_t + p_c * (1 - p_c) / n_c)
    diff = p_t - p_c
    return diff, diff - z * se, diff + z * se, se


def difference_in_means(xs_t, xs_c, z=Z_95):
    """
    (mean_t - mean_c) with a Welch standard error.

    Unpooled variances on purpose: an intervention that works shifts the
    treated arm's spread as well as its centre, so a pooled estimate would
    assume away the very thing being measured.
    """
    m_t, m_c = _mean(xs_t), _mean(xs_c)
    se = math.sqrt(_var(xs_t) / len(xs_t) + _var(xs_c) / len(xs_c))
    diff = m_t - m_c
    return diff, diff - z * se, diff + z * se, se


def _predicted_eiv(conn, arm):
    row = conn.execute(PREDICTED_EIV_SQL, (arm,)).fetchone()
    return (row["total_eiv"] or 0.0), (row["n"] or 0)


def incremental_report(conn) -> dict:
    """
    The aggregate incremental figures, or an explicit refusal.

    Never returns a partial number: either both arms clear MIN_N_PER_ARM and
    every figure is present, or `reportable` is False and the reason says why.
    """
    rows = [dict(r) for r in conn.execute(RESOLVED_POPULATION_SQL).fetchall()]
    assigned = {r["arm"]: r["n"]
                for r in conn.execute(ASSIGNED_COUNT_SQL).fetchall()}

    treated = [r for r in rows if r["arm"] == _cfg.TREATMENT_GROUP]
    control = [r for r in rows if r["arm"] == _cfg.CONTROL_GROUP]

    sources = {r["outcome_source"] for r in rows}
    report = {
        "scope": "aggregate",
        "synthetic": True,
        "estimator": "difference in proportions (Wald 95% CI); "
                     "difference in means (Welch 95% CI)",
        "min_n_per_arm": MIN_N_PER_ARM,
        "assigned": assigned,
        "n_resolved": {"treatment": len(treated), "control": len(control)},
        "outcome_sources": sorted(s for s in sources if s is not None),
        "reportable": False,
        "refusal_reason": None,
    }

    unexpected = {s for s in sources if s not in SYNTHETIC_SOURCES and s is not None}
    if unexpected:
        report["refusal_reason"] = (
            f"population mixes outcome sources {sorted(unexpected)} with "
            f"{list(SYNTHETIC_SOURCES)}; one figure over both would present a "
            "synthetic draw and a confirmed outcome as the same kind of "
            "evidence")
        return report

    if len(treated) < MIN_N_PER_ARM or len(control) < MIN_N_PER_ARM:
        report["refusal_reason"] = (
            f"insufficient data: {len(treated)} treatment / {len(control)} "
            f"control resolved opportunities, below the required "
            f"{MIN_N_PER_ARM} per arm")
        return report

    k_t = sum(1 for r in treated if r["recovered_bool"])
    k_c = sum(1 for r in control if r["recovered_bool"])
    rate_diff, rate_lo, rate_hi, rate_se = difference_in_proportions(
        k_t, len(treated), k_c, len(control))

    amounts_t = [float(r["recovered_amount"]) for r in treated]
    amounts_c = [float(r["recovered_amount"]) for r in control]
    rs_diff, rs_lo, rs_hi, rs_se = difference_in_means(amounts_t, amounts_c)

    eiv_total, eiv_n = _predicted_eiv(conn, _cfg.TREATMENT_GROUP)

    report.update({
        "reportable": True,
        "recovered": {"treatment": k_t, "control": k_c},
        "recovery_rate": {"treatment": k_t / len(treated),
                          "control": k_c / len(control)},
        "incremental_rate": {"estimate": rate_diff, "ci_low": rate_lo,
                             "ci_high": rate_hi, "se": rate_se},
        "mean_recovered_rs": {"treatment": _mean(amounts_t),
                              "control": _mean(amounts_c)},
        "incremental_rs_per_opportunity": {
            "estimate": rs_diff, "ci_low": rs_lo, "ci_high": rs_hi,
            "se": rs_se},
        # Scaled to the treated population actually resolved. Deliberately not
        # extrapolated to the full assigned population or to any future
        # volume: that would be a projection, and this module reports a
        # measurement.
        "incremental_rs_total": {
            "estimate": rs_diff * len(treated),
            "ci_low": rs_lo * len(treated),
            "ci_high": rs_hi * len(treated),
            "scaled_over": len(treated)},
        # The diagnostic, NOT an agreement check. Predicted EIV is the
        # optimizer's own expectation for the candidates the rule engine
        # selected; observed incremental Rs is what the experiment measured.
        # They are different quantities computed from different sources and
        # are reported side by side precisely so a divergence is visible.
        "predicted_eiv": {"total": eiv_total, "n_selected": eiv_n,
                          "per_opportunity": (eiv_total / eiv_n) if eiv_n else None},
    })
    report["predicted_vs_observed"] = _diagnostic(report)
    return report


def _diagnostic(report):
    """Predicted EIV against observed incremental Rs, as a delta, unforced."""
    predicted = report["predicted_eiv"]["per_opportunity"]
    observed = report["incremental_rs_per_opportunity"]["estimate"]
    if predicted is None:
        return {"available": False,
                "note": "no selected candidates carry a predicted EIV"}
    ci = report["incremental_rs_per_opportunity"]
    return {
        "available": True,
        "predicted_per_opportunity": predicted,
        "observed_per_opportunity": observed,
        "delta": observed - predicted,
        "predicted_within_observed_ci": ci["ci_low"] <= predicted <= ci["ci_high"],
        "note": "Different quantities from different sources: predicted EIV is "
                "the optimizer's expectation for the selected candidate, "
                "observed is the measured arm difference. Divergence is "
                "reported, not reconciled.",
    }


def format_report(report: dict) -> str:
    lines = ["INCREMENTAL RECOVERY -- AGGREGATE (SYNTHETIC DATA)",
             "=" * 62,
             "  Source: outcomes drawn from the Data Factory's "
             "potential-outcome generator.",
             "          NOT observed from any real payment system. This is a "
             "synthetic-environment",
             "          result and must never be presented as production "
             "evidence.",
             f"  Scope : {report['scope']} only -- no segmentation "
             f"(disclosed, see PHASE7_NOTES.md)",
             f"  Estimator: {report['estimator']}",
             "",
             f"  assigned        : {report['assigned']}",
             f"  resolved        : {report['n_resolved']}",
             f"  outcome sources : {report['outcome_sources']}"]

    if not report["reportable"]:
        lines.append("")
        lines.append(f"  VERDICT: NOT REPORTABLE")
        lines.append(f"    {report['refusal_reason']}")
        return "\n".join(lines)

    rate = report["incremental_rate"]
    rs = report["incremental_rs_per_opportunity"]
    total = report["incremental_rs_total"]
    lines += [
        "",
        f"  recovery rate   : treatment {report['recovery_rate']['treatment']:.4f} "
        f"({report['recovered']['treatment']}/{report['n_resolved']['treatment']})"
        f"   control {report['recovery_rate']['control']:.4f} "
        f"({report['recovered']['control']}/{report['n_resolved']['control']})",
        "",
        f"  INCREMENTAL RECOVERY RATE : {rate['estimate']:+.4f}",
        f"    95% CI                  : [{rate['ci_low']:+.4f}, {rate['ci_high']:+.4f}]"
        f"   (se {rate['se']:.4f})",
        "",
        f"  mean recovered Rs         : treatment {report['mean_recovered_rs']['treatment']:,.2f}"
        f"   control {report['mean_recovered_rs']['control']:,.2f}",
        f"  INCREMENTAL Rs / OPPTY    : {rs['estimate']:+,.2f}",
        f"    95% CI                  : [{rs['ci_low']:+,.2f}, {rs['ci_high']:+,.2f}]"
        f"   (se {rs['se']:,.2f})",
        f"  INCREMENTAL Rs (total)    : {total['estimate']:+,.2f}"
        f"   over {total['scaled_over']} resolved treatment opportunities",
        f"    95% CI                  : [{total['ci_low']:+,.2f}, {total['ci_high']:+,.2f}]",
    ]

    diag = report["predicted_vs_observed"]
    lines.append("")
    lines.append("  PREDICTED vs OBSERVED (diagnostic, not an agreement check)")
    if diag["available"]:
        lines += [
            f"    predicted EIV / oppty : {diag['predicted_per_opportunity']:+,.2f}"
            f"   (n={report['predicted_eiv']['n_selected']} selected candidates)",
            f"    observed    / oppty   : {diag['observed_per_opportunity']:+,.2f}",
            f"    delta                 : {diag['delta']:+,.2f}",
            f"    predicted inside the observed 95% CI: "
            f"{diag['predicted_within_observed_ci']}",
        ]
    else:
        lines.append(f"    {diag['note']}")
    return "\n".join(lines)


def main():
    from backend.db.db import get_connection
    conn = get_connection()
    try:
        report = incremental_report(conn)
        print(format_report(report))
        print()
        print("  Every figure above is derived from these live queries:")
        for name, sql in sql_trace().items():
            print(f"\n  -- {name}\n{sql}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
