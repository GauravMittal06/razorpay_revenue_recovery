"""
The randomization-balance report.

EXECUTION_PLAN Phase 6 makes this a HARD gate, and says why: "A holdout
percentage that is too small, or an assignment process that is not genuinely
random, silently invalidates every incremental number computed afterward --
the randomization-balance test is a hard gate specifically because this
failure mode is invisible unless checked directly."

--------------------------------------------------------------------------
This module computes numbers. It does not certify anything.
--------------------------------------------------------------------------
`balance_report()` returns per-arm counts, means, standard deviations, the
full level x arm contingency table, and every covariate's signed standardized
mean difference. `verdict()` derives PASS / FAIL / NOT_EVALUABLE from that
report against the bounds locked in phase6_config.

They are separate functions on purpose. The project's standing rule is that a
gate reports raw numbers rather than a verdict, so the figures can be read and
disputed independently of the judgement drawn from them. Nothing here decides
what the bounds are -- every threshold is imported from phase6_config, which
was committed at X0 before a single opportunity had been assigned.

--------------------------------------------------------------------------
What is measured, and when
--------------------------------------------------------------------------
Every covariate is fixed at opportunity creation and never mutates, so
"balance at assignment time" and "balance at creation time" are the same
measurement. This matters: a balance check that drifted into comparing
post-hoc values would be measuring the intervention's effect rather than the
quality of the randomization, and would fail precisely when the system was
working.
"""

import math

from backend.engine import phase6_config as _cfg


def _diagnosis(row) -> str:
    """root_cause when the event is a payment failure, else the event type."""
    if row["event_type"] == "payment_failed":
        return row["root_cause"]
    return row["event_type"]


def _is_payment_failed(row) -> str:
    return "yes" if row["event_type"] == "payment_failed" else "no"


COVARIATE_VALUE = {
    "diagnosis": _diagnosis,
    "is_payment_failed": _is_payment_failed,
}


def _mean(values):
    return sum(values) / len(values) if values else float("nan")


def _sd(values):
    """Sample standard deviation, ddof=1, as the locked formula specifies."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _smd(numerator, denominator):
    """
    The shared degenerate-case convention, in one place.

    The denominator vanishes only when both arms are entirely at one extreme.
    Returning nan here would compare False against every bound and silently
    pass, which is the failure mode this convention exists to prevent.
    """
    if denominator == 0:
        return (_cfg.DEGENERATE_SMD_BALANCED if numerator == 0
                else _cfg.DEGENERATE_SMD_IMBALANCED)
    return numerator / denominator


def continuous_smd(treated, control):
    """(mean_t - mean_c) / sqrt((s_t^2 + s_c^2) / 2), s with ddof=1."""
    s_t, s_c = _sd(treated), _sd(control)
    return _smd(_mean(treated) - _mean(control),
                math.sqrt((s_t ** 2 + s_c ** 2) / 2))


def categorical_smd(p_t, p_c):
    """(p_t - p_c) / sqrt((p_t(1-p_t) + p_c(1-p_c)) / 2)."""
    return _smd(p_t - p_c,
                math.sqrt((p_t * (1 - p_t) + p_c * (1 - p_c)) / 2))


def load_assigned(conn):
    """
    Every opportunity in the experiment, with the covariates as they were at
    creation.

    An INNER JOIN on experiment_assignment, so an opportunity with no
    assignment row is excluded -- it is not in the experiment (ruling
    2026-09-04) and including it would silently enrol a never-randomized
    population into the comparison.
    """
    rows = conn.execute(
        """
        SELECT o.opportunity_id, o.event_type, o.root_cause, o.amount_at_risk,
               o.created_at, a."group" AS arm, a.assigned_at
        FROM opportunities o
        JOIN experiment_assignment a
          ON a.opportunity_id = o.opportunity_id
        ORDER BY o.opportunity_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def balance_report(conn) -> dict:
    """
    The raw figures. No verdict, no thresholds applied.
    """
    rows = load_assigned(conn)
    treated = [r for r in rows if r["arm"] == _cfg.TREATMENT_GROUP]
    control = [r for r in rows if r["arm"] == _cfg.CONTROL_GROUP]

    report = {
        "n_total": len(rows),
        "n_treatment": len(treated),
        "n_control": len(control),
        "holdout_observed": (len(control) / len(rows)) if rows else float("nan"),
        "holdout_declared": _cfg.HOLDOUT_FRACTION,
        "continuous": {},
        "categorical": {},
    }

    for name in _cfg.CONTINUOUS_COVARIATES:
        t = [r[name] for r in treated if r[name] is not None]
        c = [r[name] for r in control if r[name] is not None]
        report["continuous"][name] = {
            "treatment": {"n": len(t), "mean": _mean(t), "sd": _sd(t),
                          "median": _median(t), "min": min(t) if t else None,
                          "max": max(t) if t else None},
            "control": {"n": len(c), "mean": _mean(c), "sd": _sd(c),
                        "median": _median(c), "min": min(c) if c else None,
                        "max": max(c) if c else None},
            "smd": continuous_smd(t, c),
        }

    # The eligibility floor is holdout-aware: a level enters the gate only when
    # the SMALLER arm is expected to hold at least MIN_EXPECTED_ARM_COUNT of
    # it, since that arm is what drives the estimate's noise.
    smaller_share = min(_cfg.HOLDOUT_FRACTION, 1.0 - _cfg.HOLDOUT_FRACTION)

    for covariate, levels in _cfg.CATEGORICAL_COVARIATES.items():
        value_of = COVARIATE_VALUE[covariate]
        t_values = [value_of(r) for r in treated]
        c_values = [value_of(r) for r in control]
        per_level = {}
        for level in levels:
            n_t = t_values.count(level)
            n_c = c_values.count(level)
            n_level = n_t + n_c
            p_t = n_t / len(treated) if treated else 0.0
            p_c = n_c / len(control) if control else 0.0
            eligible = (smaller_share * n_level) >= _cfg.MIN_EXPECTED_ARM_COUNT
            per_level[level] = {
                "n_treatment": n_t, "n_control": n_c, "n_total": n_level,
                "p_treatment": p_t, "p_control": p_c,
                "smd": categorical_smd(p_t, p_c),
                "eligible": eligible,
                "status": "GATED" if eligible else "EXCLUDED_UNDERPOWERED",
            }
        excluded_n = sum(v["n_total"] for v in per_level.values()
                         if not v["eligible"])
        report["categorical"][covariate] = {
            "levels": per_level,
            "excluded_coverage": (excluded_n / len(rows)) if rows else 0.0,
            # Levels the data contains but the declared list does not. A
            # declared list can fall behind the vocabulary the entry point
            # accepts, and the gate would then silently stop covering a real
            # root cause.
            "undeclared_levels": sorted(
                (set(t_values) | set(c_values)) - set(levels)),
        }

    return report


def _median(values):
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def verdict(report: dict) -> dict:
    """
    PASS / FAIL / NOT_EVALUABLE, derived from the report against the locked
    bounds. Kept separate from balance_report() so the figures can be read
    independently of the judgement.
    """
    reasons = []
    if report["n_total"] < _cfg.MIN_ASSIGNED_N:
        reasons.append(
            f"n={report['n_total']} is below the locked MIN_ASSIGNED_N of "
            f"{_cfg.MIN_ASSIGNED_N}; balance cannot be certified on a sample "
            "too small to detect imbalance")
    if not report["n_treatment"] or not report["n_control"]:
        reasons.append("one arm is empty")
    for covariate, block in report["categorical"].items():
        if block["excluded_coverage"] > _cfg.MAX_EXCLUDED_COVERAGE:
            reasons.append(
                f"{covariate}: underpowered levels cover "
                f"{block['excluded_coverage']:.1%} of the population, above "
                f"the locked {_cfg.MAX_EXCLUDED_COVERAGE:.0%} ceiling")
        if block["undeclared_levels"]:
            reasons.append(
                f"{covariate}: data contains levels absent from the declared "
                f"list {block['undeclared_levels']}")
    if reasons:
        return {"verdict": "NOT_EVALUABLE", "reasons": reasons,
                "max_abs_smd": None}

    breaches = []
    worst = 0.0
    for name, block in report["continuous"].items():
        worst = max(worst, abs(block["smd"]))
        if abs(block["smd"]) >= _cfg.MAX_ABS_SMD:
            breaches.append(f"{name}: |SMD|={abs(block['smd']):.4f}")
    for covariate, block in report["categorical"].items():
        for level, stats in block["levels"].items():
            if not stats["eligible"]:
                continue
            worst = max(worst, abs(stats["smd"]))
            if abs(stats["smd"]) >= _cfg.MAX_ABS_SMD:
                breaches.append(
                    f"{covariate}={level}: |SMD|={abs(stats['smd']):.4f}")

    return {"verdict": "FAIL" if breaches else "PASS",
            "reasons": breaches, "max_abs_smd": worst}


def format_report(report: dict, decision: dict) -> str:
    """
    The raw numbers, as text, for the gate's evidence output.

    Everything a reader needs to recompute the verdict themselves: per-arm n,
    the distribution summary, the full contingency table, and every level's
    signed SMD including the excluded ones.
    """
    lines = []
    lines.append("RANDOMIZATION BALANCE -- RAW FIGURES")
    lines.append(f"  assigned total   : {report['n_total']}")
    lines.append(f"  treatment / control : {report['n_treatment']} / "
                 f"{report['n_control']}")
    lines.append(f"  observed holdout : {report['holdout_observed']:.4f} "
                 f"(declared {report['holdout_declared']})")

    for name, block in report["continuous"].items():
        lines.append(f"\n  {name} (continuous)")
        lines.append(f"    {'arm':<10} {'n':>5} {'mean':>12} {'sd':>12} "
                     f"{'median':>10} {'min':>8} {'max':>8}")
        for arm in ("treatment", "control"):
            s = block[arm]
            lines.append(f"    {arm:<10} {s['n']:>5} {s['mean']:>12.2f} "
                         f"{s['sd']:>12.2f} {str(s['median']):>10} "
                         f"{str(s['min']):>8} {str(s['max']):>8}")
        lines.append(f"    SMD = {block['smd']:+.4f}")

    for covariate, block in report["categorical"].items():
        lines.append(f"\n  {covariate} (categorical)")
        lines.append(f"    {'level':<22} {'n_t':>5} {'n_c':>5} {'p_t':>8} "
                     f"{'p_c':>8} {'SMD':>9}  status")
        for level, s in block["levels"].items():
            lines.append(
                f"    {level:<22} {s['n_treatment']:>5} {s['n_control']:>5} "
                f"{s['p_treatment']:>8.4f} {s['p_control']:>8.4f} "
                f"{s['smd']:>+9.4f}  {s['status']}")
        lines.append(f"    excluded coverage = {block['excluded_coverage']:.4f} "
                     f"(ceiling {_cfg.MAX_EXCLUDED_COVERAGE})")

    lines.append(f"\n  bound: |SMD| < {_cfg.MAX_ABS_SMD}   "
                 f"max observed |SMD| = "
                 f"{decision['max_abs_smd'] if decision['max_abs_smd'] is not None else 'n/a'}")
    lines.append(f"  VERDICT: {decision['verdict']}")
    for reason in decision["reasons"]:
        lines.append(f"    - {reason}")
    return "\n".join(lines)
