"""
How large must the assigned population be for the balance gate to mean
anything?

This module exists because the gate failed at n=240 and the failure turned out
to say nothing about the randomizer. Under a KNOWN-CORRECT assigner -- the
real locked hash, over ids minted the way `trigger_event` mints them -- the
gate failed 99% of the time at that size. `MAX_ABS_SMD = 0.10` and
`MIN_ASSIGNED_N = 200` were both locked at X0 and were mutually incompatible:
SE(SMD) is roughly 2/sqrt(n), so at n=240 each level's SMD has SD ~= 0.13 and
the gate takes the maximum over ten such quantities against a 0.10 bound.

TWO CURVES, BECAUSE ONE IS NOT ENOUGH
    `power_curve()` measures the FALSE-FAILURE rate: how often the gate fails
    when the randomizer is perfect. That is what sets the floor.

    `detection_curve()` measures the opposite and is the negative control. A
    threshold chosen only to avoid false alarms can be satisfied by a gate
    that never fires at all -- so the same n must also catch an assigner that
    genuinely is biased. Reporting the first without the second would be
    choosing a sample size that makes the gate quiet rather than correct.

Neither function decides anything. Both report rates; the floor is a ruling,
recorded in phase6_config and locked_thresholds.json.
"""

import argparse
import math
import random
import statistics

from backend.analytics import randomization_balance as rb
from backend.data.generate_experiment_volume import AMOUNT_RANGE, EVENT_WEIGHTS
from backend.engine import phase6_config as cfg
from backend.engine.trigger_event import VALID_ROOT_CAUSES

RC = sorted(VALID_ROOT_CAUSES)
EVENTS = [e for e, _ in EVENT_WEIGHTS]
WEIGHTS = [w for _, w in EVENT_WEIGHTS]

DEFAULT_NS = (500, 1000, 1500, 2000, 2500, 3000, 3500, 4000)
DEFAULT_TRIALS = 500

# How far a deliberately-biased assigner departs from the correct one, for the
# detection curve. `bias` is the amount by which the control probability is
# shifted for above-midpoint-amount opportunities: 0.0 is the correct
# assigner, 0.20 means high-amount cases go to control 20 percentage points
# more often. Amount is the covariate chosen because it is the one an
# incremental-Rs claim is most sensitive to.
#
# READ THE DETECTION TABLE IN THE `induced |SMD|` COLUMN, NOT THE BIAS COLUMN.
# The bias knob is in units of probability and the gate is in units of SMD,
# and they are not the same scale: a 0.05 bias induces only ~0.042 SMD and a
# 0.10 bias ~0.086, both BELOW the 0.10 bound the gate is told to enforce. A
# gate that fired on those would be violating its own declared threshold, so
# low detection there is correct behaviour and not a blind spot.
#
# An earlier reading of this curve missed that and reported the gate as
# insensitive; the levels below are chosen to straddle the bound so the table
# cannot be misread the same way again.
DEFAULT_BIASES = (0.0, 0.10, 0.15, 0.20, 0.30)

AMOUNT_MIDPOINT = (AMOUNT_RANGE[0] + AMOUNT_RANGE[1]) / 2


def _synthetic_id(rng):
    """
    An opportunity id of exactly the shape trigger_event mints, drawn from a
    SEEDED generator.

    `uuid.uuid4()` reads os.urandom and ignores `random.seed`, so an earlier
    version of this module was not reproducible: two runs at the same seed
    disagreed on the pass rate at n=3500 (98.0% and 96.0%), which is enough to
    move the chosen floor. A threshold justified by a measurement nobody can
    reproduce is not justified.

    `uuid4().hex[:12]` is 48 uniformly random bits, and so is this. The
    assignment hash sees an input of identical shape and distribution; the
    only thing that changes is that the sequence can be replayed.
    """
    return "opp_" + f"{rng.getrandbits(48):012x}"


def _draw_rows(n, rng, bias=0.0):
    """
    n opportunities with covariates from the same event mix the volume
    generator uses, assigned by the real locked hash.

    With bias > 0 the assignment is deliberately corrupted: an above-midpoint
    amount is pushed toward control. bias = 0 is the true-random null and uses
    the hash alone.
    """
    rows = []
    for _ in range(n):
        et = rng.choices(EVENTS, weights=WEIGHTS)[0]
        rc = rng.choice(RC) if et == "payment_failed" else None
        amount = rng.randint(*AMOUNT_RANGE)
        oid = _synthetic_id(rng)
        arm = cfg.assigned_group(oid)
        if bias and amount > AMOUNT_MIDPOINT and rng.random() < bias:
            arm = cfg.CONTROL_GROUP
        rows.append({"event_type": et, "root_cause": rc,
                     "amount_at_risk": amount, "arm": arm})
    return rows


def max_abs_smd(rows):
    """
    The gate's own statistic, computed exactly as randomization_balance does:
    the maximum |SMD| over the continuous covariate and every gate-eligible
    categorical level.
    """
    t = [r for r in rows if r["arm"] == cfg.TREATMENT_GROUP]
    c = [r for r in rows if r["arm"] == cfg.CONTROL_GROUP]
    if not t or not c:
        return float("inf")
    worst = abs(rb.continuous_smd([r["amount_at_risk"] for r in t],
                                  [r["amount_at_risk"] for r in c]))
    smaller = min(cfg.HOLDOUT_FRACTION, 1 - cfg.HOLDOUT_FRACTION)
    for cov, levels in cfg.CATEGORICAL_COVARIATES.items():
        value_of = rb.COVARIATE_VALUE[cov]
        tv = [value_of(r) for r in t]
        cv = [value_of(r) for r in c]
        for level in levels:
            n_t, n_c = tv.count(level), cv.count(level)
            if smaller * (n_t + n_c) < cfg.MIN_EXPECTED_ARM_COUNT:
                continue
            worst = max(worst,
                        abs(rb.categorical_smd(n_t / len(t), n_c / len(c))))
    return worst


def _wilson_lower(successes, trials, z=1.96):
    """
    Lower bound of the Wilson score interval.

    The floor is chosen on this rather than on the point estimate: a Monte
    Carlo pass rate is itself an estimate, and picking the smallest n whose
    POINT estimate clears the criterion would clear it by sampling luck about
    half the time.
    """
    if trials == 0:
        return 0.0
    p = successes / trials
    denom = 1 + z ** 2 / trials
    centre = p + z ** 2 / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z ** 2 / (4 * trials ** 2))
    return (centre - margin) / denom


def power_curve(ns=DEFAULT_NS, trials=DEFAULT_TRIALS, seed=7):
    """False-failure rate under the true-random null, per n."""
    rng = random.Random(seed)
    out = []
    for n in ns:
        worst = sorted(max_abs_smd(_draw_rows(n, rng)) for _ in range(trials))
        passed = sum(1 for w in worst if w < cfg.MAX_ABS_SMD)
        out.append({
            "n": n, "trials": trials, "passed": passed,
            "pass_rate": passed / trials,
            "pass_rate_lower95": _wilson_lower(passed, trials),
            "median_max_smd": statistics.median(worst),
            "p90_max_smd": worst[int(0.9 * len(worst))],
        })
    return out


def induced_smd(bias, n=20000, trials=15, seed=3):
    """
    The true imbalance a given bias actually creates, in the gate's own units.

    Measured at a large n where sampling noise (~0.014) is small next to the
    effect, so the figure is the bias's systematic contribution rather than a
    draw. This is what makes the detection curve interpretable: without it the
    bias knob is in probability units and the gate is in SMD units, and the
    two get compared as though they were the same scale.
    """
    rng = random.Random(seed)
    values = []
    for _ in range(trials):
        rows = _draw_rows(n, rng, bias=bias)
        t = [r for r in rows if r["arm"] == cfg.TREATMENT_GROUP]
        c = [r for r in rows if r["arm"] == cfg.CONTROL_GROUP]
        values.append(abs(rb.continuous_smd(
            [r["amount_at_risk"] for r in t],
            [r["amount_at_risk"] for r in c])))
    return statistics.mean(values)


def detection_curve(n, biases=DEFAULT_BIASES, trials=DEFAULT_TRIALS, seed=11,
                    with_induced=True):
    """
    How often the gate FAILS when the assigner is deliberately biased.

    The negative control for the floor: at the chosen n the gate must be quiet
    under the null AND loud under a real defect. A high pass rate on its own
    is equally consistent with a gate that cannot fire at all.

    Each row carries the imbalance the bias actually induces, because the
    detection rate is only meaningful relative to the gate's bound. Detection
    is EXPECTED to be near the null rate for an induced |SMD| below
    MAX_ABS_SMD -- a gate that fired there would be enforcing a tighter
    threshold than the one declared and locked.
    """
    rng = random.Random(seed)
    out = []
    for bias in biases:
        caught = sum(1 for _ in range(trials)
                     if max_abs_smd(_draw_rows(n, rng, bias=bias))
                     >= cfg.MAX_ABS_SMD)
        row = {"bias": bias, "trials": trials, "caught": caught,
               "detection_rate": caught / trials}
        if with_induced:
            row["induced_smd"] = induced_smd(bias)
            row["above_bound"] = row["induced_smd"] >= cfg.MAX_ABS_SMD
        out.append(row)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--criterion", type=float, default=0.95)
    parser.add_argument("--detect-at", type=int, default=None,
                        help="n to run the detection curve at "
                             "(default: the chosen floor)")
    args = parser.parse_args()

    print(f"BALANCE GATE POWER -- true-random null, {args.trials} trials per n")
    print(f"bound |SMD| < {cfg.MAX_ABS_SMD}; randomizer is the real locked "
          f"hash over uuid4-shaped ids\n")
    print(f"{'n':>6} {'passed':>8} {'pass rate':>10} {'95% lower':>10} "
          f"{'median':>9} {'p90':>9}")
    rows = power_curve(trials=args.trials)
    for r in rows:
        print(f"{r['n']:>6} {r['passed']:>4}/{r['trials']:<3} "
              f"{r['pass_rate']:>10.1%} {r['pass_rate_lower95']:>10.1%} "
              f"{r['median_max_smd']:>9.4f} {r['p90_max_smd']:>9.4f}")

    clearing = [r for r in rows if r["pass_rate_lower95"] >= args.criterion]
    floor = clearing[0]["n"] if clearing else None
    print(f"\ncriterion: 95% lower bound of the pass rate >= "
          f"{args.criterion:.0%}")
    print(f"smallest n clearing it: {floor if floor else 'none in range'}")

    detect_at = args.detect_at or floor
    if detect_at:
        print(f"\nDETECTION -- how often the gate FAILS on a biased assigner, "
              f"n={detect_at}")
        print(f"{'bias':>6} {'induced |SMD|':>14} {'vs bound':>10} "
              f"{'caught':>11} {'detection':>11}")
        for d in detection_curve(detect_at, trials=args.trials):
            label = "null" if d["bias"] == 0 else f"+{d['bias']:.2f}"
            side = "ABOVE" if d["above_bound"] else "below"
            print(f"{label:>6} {d['induced_smd']:>14.4f} {side:>10} "
                  f"{d['caught']:>5}/{d['trials']:<5} "
                  f"{d['detection_rate']:>10.1%}")
        print(f"\nbias = control probability added for above-midpoint amounts; "
              f"0.00 is the correct assigner.")
        print(f"Read the induced |SMD| column, not the bias column: the gate's "
              f"bound is {cfg.MAX_ABS_SMD}, so near-null detection BELOW that "
              f"line\nis correct behaviour -- firing there would enforce a "
              f"tighter threshold than the one locked.")


if __name__ == "__main__":
    main()
