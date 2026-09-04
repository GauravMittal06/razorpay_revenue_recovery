"""
Generate live experiment volume, through the real entry point.

The two Phase 6 hard gates need a population that was ACTUALLY assigned by the
system, not one constructed to look like it was. So this walks a spec list
through `trigger_event()` -- the one creation point -- and lets assignment,
suppression, decision, execution and delivery all happen exactly as they do in
production. Nothing here writes `experiment_assignment` itself.

WHY NOT BACKFILL THE SEEDED WORLD
    The 150 seeded opportunities are deliberately left unassigned. 51 of them
    are already terminal, so assigning them now would put an arm label on
    outcomes that were decided before the experiment existed -- which is not a
    randomized comparison, it is a relabelling of history. The remaining 99
    already carry decision history for the same reason. An opportunity enters
    this experiment at creation or not at all.

REPRODUCIBILITY, HONESTLY STATED
    The event MIX is fully determined by `seed` -- the same seed produces the
    same sequence of (event_type, amount, root_cause, days_overdue). The ARM
    each opportunity lands in is not reproducible, because `trigger_event`
    mints a uuid4 opportunity id and the assignment is a hash of it. That is a
    property of genuine randomization, not a defect: a balance gate whose
    result could be reproduced by re-running would be testing a fixed
    partition rather than the randomizer.
"""

import argparse
import random

from backend.db.db import get_connection
from backend.engine.trigger_event import (VALID_EVENT_TYPES,
                                          VALID_ROOT_CAUSES, trigger_event)

# The event mix. Weighted to resemble the seeded world's composition rather
# than a uniform draw, so the balance gate runs against a realistic level
# distribution -- a uniform mix would give every level the same generous
# count and make the eligibility floor trivially easy to clear.
EVENT_WEIGHTS = (
    ("payment_failed", 0.45),
    ("checkout_abandoned", 0.35),
    ("invoice_overdue", 0.20),
)

AMOUNT_RANGE = (500, 250_000)
DAYS_OVERDUE_RANGE = (1, 30)

DEFAULT_SEED = 20260904
DEFAULT_COUNT = 240


def _spec(rng):
    event_type = rng.choices([e for e, _ in EVENT_WEIGHTS],
                             weights=[w for _, w in EVENT_WEIGHTS])[0]
    amount = rng.randint(*AMOUNT_RANGE)
    root_cause = (rng.choice(sorted(VALID_ROOT_CAUSES))
                  if event_type == "payment_failed" else None)
    days_overdue = (rng.randint(*DAYS_OVERDUE_RANGE)
                    if event_type == "invoice_overdue" else None)
    return event_type, amount, root_cause, days_overdue


def generate(count=DEFAULT_COUNT, seed=DEFAULT_SEED, conn=None):
    """
    Create `count` opportunities through trigger_event(). Returns a summary.

    Every one runs the full production path, so a control opportunity is
    genuinely suppressed here rather than skipped by this generator.
    """
    owned = conn is None
    conn = conn or get_connection()
    rng = random.Random(seed)

    summary = {"created": 0, "failed": 0, "by_arm": {}, "by_outcome": {}}
    try:
        for _ in range(count):
            event_type, amount, root_cause, days_overdue = _spec(rng)
            result = trigger_event(event_type, amount, conn,
                                   root_cause=root_cause,
                                   days_overdue=days_overdue)
            if result.get("status") != "ok":
                summary["failed"] += 1
                continue
            summary["created"] += 1
            arm = result["assignment"]["group"]
            outcome = result["decision"]["outcome"]
            summary["by_arm"][arm] = summary["by_arm"].get(arm, 0) + 1
            summary["by_outcome"][outcome] = \
                summary["by_outcome"].get(outcome, 0) + 1
    finally:
        if owned:
            conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    summary = generate(count=args.count, seed=args.seed)
    print(f"event mix seed={args.seed} count={args.count}")
    print(f"  created {summary['created']}, failed {summary['failed']}")
    print(f"  by arm     : {summary['by_arm']}")
    print(f"  by outcome : {summary['by_outcome']}")
    print("  NOTE: the event mix is seed-reproducible; the arm assignment is "
          "not, by design.")


if __name__ == "__main__":
    main()
