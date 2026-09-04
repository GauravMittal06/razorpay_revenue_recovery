"""
Realize a synthetic business outcome for every opportunity in the live
experiment, through the one ingestion path.

WHAT THIS IS, STATED PLAINLY
    These outcomes are SYNTHETIC. They are drawn from the Data Factory's own
    potential-outcome generator -- `outcome_model.draw_outcome()`, the same
    function that produced the training corpus -- and not observed from any
    real payment system, because none exists for this project. Every row it
    writes carries `outcome_source='synthetic_potential_outcome'`, distinct
    from `manual_confirmation` and `executor_stop`, so nothing downstream can
    mistake a generated outcome for a confirmed one. Any figure computed from
    these rows is a synthetic-environment result and must be labelled as such
    wherever it surfaces.

THE POTENTIAL-OUTCOME STRUCTURE, AND WHY IT IS THE WHOLE POINT
    Each opportunity has one hidden state -- the customer's true underlying
    situation -- and a family of outcomes, one per action the system might
    have taken. That is the counterfactual structure an incremental claim
    rests on, and it is why the arms are realized differently:

        treatment -> the outcome for the candidate the RULE ENGINE ACTUALLY
                     SELECTED (recovery_candidates.selected = 1)
        control   -> the outcome for `do_nothing`

    The hidden state is sampled ONCE per opportunity and used for whichever
    branch that opportunity lands in -- the permanent invariant that makes
    cross-candidate comparison meaningful at all. It is seeded from the
    opportunity_id, so the same opportunity always draws the same hidden
    state no matter when or in what order this runs, and the treatment and
    control arms are not systematically different in their hidden state.

    Critically, the arm does NOT influence the hidden state: the draw depends
    only on the opportunity_id and the customer profile. If it depended on the
    arm, the measured difference between arms would include a difference this
    generator invented, and the incremental number would be circular.

NOTHING HERE WRITES AN OUTCOME COLUMN
    Every outcome goes through `observe_outcome()`, which remains the single
    ingestion path. This module decides WHAT was observed; it has no ability
    to record it any other way.
"""

import argparse
import hashlib

import numpy as np

from backend.data_factory import outcome_model as om
from backend.data_factory.calibration_profiles import get_profile
from backend.data_factory.candidate_outcome_dataset import \
    sample_hidden_variables
from backend.db.db import get_connection
from backend.engine import phase6_config as cfg
from backend.engine.observe_outcome import observe_outcome

SOURCE = "synthetic_potential_outcome"
DEFAULT_PROFILE = "baseline"

# The action realized for a control opportunity, and for any treatment
# opportunity whose decision selected nothing executable (a block, a manual
# review, an exhausted fallthrough). "No automated intervention happened" is
# the same counterfactual in both cases, so it draws the same outcome.
DO_NOTHING = {"action_type": "do_nothing", "timing_hours": 0.0,
              "method_changed": False}

SECONDS_PER_DAY = 86400


def _seed_for(opportunity_id: str, salt: str) -> int:
    """
    A per-opportunity seed derived from its id.

    Deterministic so the same opportunity always draws the same hidden state,
    and independent of iteration order so a partial run followed by a resume
    produces exactly what one full run would have.
    """
    digest = hashlib.blake2b((salt + ":" + opportunity_id).encode("utf-8"),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _selected_candidate(conn, opportunity_id):
    """
    The candidate the rule engine approved for this opportunity, or None.

    `selected = 1` is set by execute_action() and by nothing else, so this is
    the action that actually happened rather than the one the optimizer
    preferred.
    """
    row = conn.execute(
        """
        SELECT action_type, timing, method, channel, predicted_eiv,
               predicted_p_treated, predicted_p_baseline,
               predicted_expected_amount_treated,
               predicted_expected_amount_baseline, cost
        FROM recovery_candidates
        WHERE opportunity_id = ? AND selected = 1
        """,
        (opportunity_id,),
    ).fetchone()
    return dict(row) if row else None


def _timing_hours(timing):
    from backend.data_factory.candidate_generation import TIMING_HOURS
    return TIMING_HOURS.get(timing, 0.0)


def _context(conn, opportunity_id):
    """Everything draw_outcome() needs, read from the live tables."""
    opp = conn.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,)).fetchone()
    if opp is None:
        return None
    opp = dict(opp)

    payment = conn.execute(
        "SELECT * FROM payments WHERE opportunity_id = ? "
        "ORDER BY created_at DESC LIMIT 1", (opportunity_id,)).fetchone()
    payment = dict(payment) if payment else {}

    customer = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?",
        (opp.get("customer_id"),)).fetchone()
    customer = dict(customer) if customer else {}

    # Contacts already delivered on this opportunity, for the fatigue term.
    prior_contacts = conn.execute(
        "SELECT COUNT(*) FROM recovery_decisions WHERE opportunity_id = ? "
        "AND action_type IN ('retry', 'reminder', 'payment_link') "
        "AND outcome = 'executed'", (opportunity_id,)).fetchone()[0]

    retry_count = conn.execute(
        "SELECT COUNT(*) FROM recovery_decisions WHERE opportunity_id = ? "
        "AND action_type = 'retry' AND outcome = 'executed'",
        (opportunity_id,)).fetchone()[0]

    health = None
    if payment.get("bank") and payment.get("psp"):
        row = conn.execute(
            "SELECT health_score FROM bank_health_observations "
            "WHERE bank = ? AND psp = ? ORDER BY window_end DESC LIMIT 1",
            (payment["bank"], payment["psp"])).fetchone()
        health = row["health_score"] if row else None

    return {
        "opportunity": opp, "payment": payment, "customer": customer,
        "prior_contacts": prior_contacts, "retry_count": retry_count,
        "health_score": health,
    }


def realize_outcome(conn, opportunity_id, arm, profile, salt, now=None):
    """
    Draw and record one opportunity's outcome. Returns a summary dict.

    A `recovered` draw becomes `resolution='recovered'` with the drawn
    fraction of amount_at_risk; a non-recovery becomes `resolution='lost'` --
    the value added in Phase 6 for exactly this, an observed fact that the
    money did not come back, as distinct from `stopped`, which is the engine
    closing a case by policy.
    """
    ctx = _context(conn, opportunity_id)
    if ctx is None:
        return {"opportunity_id": opportunity_id, "result": "not_found"}

    opp = ctx["opportunity"]
    rng = np.random.default_rng(_seed_for(opportunity_id, salt))

    # ONE hidden-state draw per opportunity, independent of the arm.
    hidden = sample_hidden_variables(
        ctx["customer"].get("payment_history_score", 0.5),
        ctx["customer"].get("past_recovery_rate", 0.3),
        ctx["payment"].get("method") or "card",
        rng,
    )

    if arm == cfg.TREATMENT_GROUP:
        selected = _selected_candidate(conn, opportunity_id)
    else:
        selected = None

    if selected is None:
        action, timing_hours, method_changed = (
            DO_NOTHING["action_type"], DO_NOTHING["timing_hours"], False)
    else:
        action = selected["action_type"]
        timing_hours = _timing_hours(selected.get("timing"))
        method_changed = bool(
            selected.get("method")
            and selected["method"] != ctx["payment"].get("method"))

    created_at = opp.get("created_at") or 0
    now_ts = int(now) if now is not None else created_at
    days_since_event = max(0.0, (now_ts - created_at) / SECONDS_PER_DAY)

    recovered, frac, ttr, p, z = om.draw_outcome(
        action_type=action,
        root_cause=opp.get("root_cause"),
        event_type=opp["event_type"],
        method_changed=method_changed,
        retry_count=ctx["retry_count"],
        timing_hours=timing_hours,
        days_since_event=days_since_event,
        days_overdue=opp.get("days_overdue") or 0,
        amount=opp["amount_at_risk"],
        prior_contacts_in_window=ctx["prior_contacts"],
        health_score=(ctx["health_score"]
                      if action in ("retry", "payment_link") else None),
        hidden=hidden,
        profile=profile,
        rng=rng,
    )

    amount = opp["amount_at_risk"] or 0
    if recovered:
        result = observe_outcome(
            opportunity_id, conn, resolution="recovered", source=SOURCE,
            partial_recovery_amount=int(round(frac * amount)))
    else:
        result = observe_outcome(
            opportunity_id, conn, resolution="lost", source=SOURCE)

    return {
        "opportunity_id": opportunity_id, "arm": arm,
        "realized_action": action, "recovered": recovered,
        "recovered_fraction": frac, "analytic_p": p,
        "result": result["result"],
    }


def generate(conn=None, profile_name=DEFAULT_PROFILE, salt="phase7-outcomes",
             limit=None):
    """
    Realize outcomes for every assigned opportunity that has none yet.

    Only opportunities in `experiment_assignment` are touched. The 150 seeded
    opportunities are not in the experiment and are left exactly as they are.
    """
    owned = conn is None
    conn = conn or get_connection()
    profile = get_profile(profile_name)

    try:
        rows = conn.execute(
            """
            SELECT a.opportunity_id, a."group" AS arm
            FROM experiment_assignment a
            JOIN opportunities o ON o.opportunity_id = a.opportunity_id
            WHERE o.resolution_type IS NULL
            ORDER BY a.opportunity_id
            """
        ).fetchall()
        if limit:
            rows = rows[:limit]

        summary = {"realized": 0, "skipped": 0, "by_arm": {},
                   "recovered_by_arm": {}, "actions": {},
                   "profile": profile_name, "source": SOURCE}
        for row in rows:
            out = realize_outcome(conn, row["opportunity_id"], row["arm"],
                                  profile, salt)
            if out["result"] != "observed":
                summary["skipped"] += 1
                continue
            arm = out["arm"]
            summary["realized"] += 1
            summary["by_arm"][arm] = summary["by_arm"].get(arm, 0) + 1
            if out["recovered"]:
                summary["recovered_by_arm"][arm] = \
                    summary["recovered_by_arm"].get(arm, 0) + 1
            summary["actions"][out["realized_action"]] = \
                summary["actions"].get(out["realized_action"], 0) + 1
        return summary
    finally:
        if owned:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--salt", default="phase7-outcomes")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    summary = generate(profile_name=args.profile, salt=args.salt,
                       limit=args.limit)
    print(f"SYNTHETIC outcomes realized through observe_outcome()")
    print(f"  source        : {summary['source']}")
    print(f"  profile       : {summary['profile']}")
    print(f"  realized      : {summary['realized']}  (skipped {summary['skipped']})")
    print(f"  by arm        : {summary['by_arm']}")
    print(f"  recovered     : {summary['recovered_by_arm']}")
    print(f"  actions drawn : {summary['actions']}")
    print("  NOTE: these outcomes are SYNTHETIC, drawn from the Data Factory's "
          "potential-outcome\n        generator. They are not observations of "
          "any real payment system.")


if __name__ == "__main__":
    main()
