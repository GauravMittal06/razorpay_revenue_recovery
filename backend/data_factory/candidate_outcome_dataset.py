"""
Data Factory -- the one joint Candidate-Outcome dataset. Phase 2.

Orchestrates entities.py + bank_health_timeseries.py + candidate_generation.py
+ outcome_model.py into ONE dataset generation run. Per execution plan
Section 5/6: one canonical synthetic world, one joint dataset -- never
separate per-dimension (action-only, timing-only, ...) datasets, since
composing independently-trained marginal models later would produce
statistically invalid joint estimates.

For every case:
  1. Pick a persistent customer/merchant from the world (already
     generated once, in entities.py) and a simulated decision timestamp.
  2. Sample hidden state ONCE (sample_hidden_variables below), reused
     unchanged across every candidate generated for this case -- this is
     the single property that makes cross-candidate comparison causally
     meaningful, generalized from (not reinventing) the pattern already
     proven correct in ml/simulate_training_data.py.
  3. Generate the eligible candidate set via the SHARED
     candidate_generation.generate_candidates() -- the same function
     Phase 4's live optimizer will import.
  4. For every candidate (always including do_nothing), draw one
     potential outcome via the SHARED outcome_model.draw_outcome().
  5. Record exactly one real contact event against the customer's
     persistent history at this case's timestamp (see
     `_record_customer_touchpoint` docstring for the documented
     simplification this represents).

Produces two artifacts per run:
  - the training-facing joint dataset (no hidden-state columns, no
    analytic z/p -- exactly what Phase 3 is allowed to train on);
  - a ground-truth companion (hidden state + analytic z/p per candidate)
    used ONLY by validators.py's ground-truth-treatment-effect check,
    never merged into the training-facing file.
"""

from dataclasses import asdict
import numpy as np
import pandas as pd

from . import entities as ent
from . import bank_health_timeseries as bht
from . import candidate_generation as cg
from . import outcome_model as om

GENERATOR_VERSION = "data-factory-v1.0.0-phase2"

DEFAULT_N_CASES = 6000
DEFAULT_N_MERCHANTS = 8
DEFAULT_N_CUSTOMERS = 300
DEFAULT_HORIZON_HOURS = 24 * 120  # 120 simulated days

EVENT_TYPES = ["checkout_abandoned", "payment_failed", "invoice_overdue"]
EVENT_TYPE_WEIGHTS = [0.35, 0.45, 0.20]
ROOT_CAUSES = [
    "insufficient_funds", "payment_declined", "gateway_timeout",
    "authentication_failed", "expired_card", "network_error",
]
CONTACT_ACTION_LABELS = ["retry", "reminder", "escalate", "payment_link"]


def clip01(x):
    return float(np.clip(x, 0.0, 1.0))


def sample_hidden_variables(payment_history_score, past_recovery_rate, method, rng):
    """
    Generalized from (not reinvented relative to) legacy's
    sample_hidden_variables -- same six hidden variables, same
    distributional families and central tendencies, so a fresh Phase-2
    case and a legacy case produce statistically comparable hidden
    state given the same customer profile. The one behavioral difference
    (mean_base by method) is threaded through the same method_base table
    the legacy module used, unchanged.
    """
    liquidity_state = clip01(rng.normal(0.3 + 0.5 * payment_history_score, 0.22))
    issuer_availability = clip01(rng.beta(8, 2))

    method_base = {"card": 0.72, "netbanking": 0.68, "upi": 0.80, "wallet": 0.75}
    payment_method_health = clip01(rng.normal(method_base.get(method, 0.72), 0.15))

    responsiveness_base = 0.15 + 0.7 * om.sigmoid(6 * (past_recovery_rate - 0.5))
    customer_responsiveness = clip01(rng.normal(responsiveness_base, 0.20))
    bank_condition_temp = clip01(rng.beta(9, 1))

    recovery_willingness = clip01(
        0.5 * liquidity_state + 0.4 * customer_responsiveness
        + rng.normal(0.0, 0.18)
    )

    return {
        "liquidity_state": liquidity_state,
        "issuer_availability": issuer_availability,
        "payment_method_health": payment_method_health,
        "customer_responsiveness": customer_responsiveness,
        "bank_condition_temp": bank_condition_temp,
        "recovery_willingness": recovery_willingness,
    }


def _pick_bank_channel(rng, world, method):
    candidates = [c for c in world.bank_channels if c.method == method]
    if not candidates:
        candidates = world.bank_channels
    return candidates[int(rng.integers(0, len(candidates)))]


def _record_customer_touchpoint(customer, sim_hour, rng):
    """
    Documented simplification: fatigue accrues from the fact that a case
    (a real revenue-at-risk situation) occurred and some real contact was
    plausible around it, independent of which hypothetical candidate this
    generator later draws a potential outcome for -- the joint dataset
    itself must remain a set of counterfactual candidates for the SAME
    realized situation, so exactly one touchpoint per case is recorded
    against the persistent customer, not one per candidate (which would
    conflate "candidates we hypothetically scored" with "contacts that
    actually happened," corrupting the fatigue feature for every future
    case involving this customer).
    """
    customer.record_contact(sim_hour)


def generate_case_rows(case_idx, sim_hour, world, health_index, profile, rng):
    customer = world.random_customer(rng)
    merchant = world.merchant(customer.merchant_id)

    event_type = str(rng.choice(EVENT_TYPES, p=EVENT_TYPE_WEIGHTS))
    root_cause = str(rng.choice(ROOT_CAUSES)) if event_type == "payment_failed" else None
    current_method = customer.preferred_method if rng.random() < 0.6 else str(rng.choice(cg.METHODS))

    amount = int(rng.integers(500, 500_000))
    retry_count = int(rng.integers(0, 4))
    days_since_event = float(rng.uniform(0, 10))
    days_overdue = float(rng.uniform(0, 20)) if event_type == "invoice_overdue" else None

    already_escalated = bool(rng.random() < 0.03)
    already_stopped = bool(rng.random() < 0.02)

    prior_contacts = customer.contacts_in_window(sim_hour, profile.fatigue_window_hours)
    if prior_contacts == 0:
        last_action_type, hours_since_last_action = "none", None
    else:
        last_action_type = str(rng.choice(CONTACT_ACTION_LABELS))
        prior_times = [t for t in customer.contact_history if t < sim_hour]
        hours_since_last_action = float(sim_hour - max(prior_times)) if prior_times else None

    bank_channel = _pick_bank_channel(rng, world, current_method)
    health_obs = health_index.lookup(bank_channel.bank, bank_channel.method, bank_channel.psp, sim_hour)
    health_score = health_obs.health_score if health_obs else None

    hidden = sample_hidden_variables(
        customer.payment_history_score, customer.past_recovery_rate, current_method, rng
    )

    context = {
        "event_type": event_type,
        "root_cause": root_cause,
        "retry_count": retry_count,
        "current_method": current_method,
        "preferred_channel": customer.preferred_channel,
        "already_escalated": already_escalated,
        "already_stopped": already_stopped,
    }
    candidates = cg.generate_candidates(context)

    case_id = f"df_{case_idx:06d}"
    joint_rows = []
    truth_rows = []

    for cand in candidates:
        recovered, frac, ttr, p, z = om.draw_outcome(
            action_type=cand["action_type"],
            root_cause=root_cause,
            event_type=event_type,
            method_changed=cand.get("method_changed", False),
            retry_count=retry_count,
            timing_hours=cand["timing_hours"],
            days_since_event=days_since_event,
            days_overdue=days_overdue,
            amount=amount,
            prior_contacts_in_window=prior_contacts,
            health_score=health_score if cand["action_type"] in ("retry", "payment_link") else None,
            hidden=hidden,
            profile=profile,
            rng=rng,
        )
        recovered_amount = round(amount * frac, 2) if recovered else 0.0

        joint_rows.append({
            "case_id": case_id,
            "sim_hour": sim_hour,
            "merchant_id": merchant.merchant_id,
            "merchant_cohort": merchant.cohort,
            "customer_id": customer.customer_id,
            "event_type": event_type,
            "root_cause": root_cause,
            "amount": amount,
            "current_method": current_method,
            "retry_count": retry_count,
            "days_since_event": days_since_event,
            "days_overdue": days_overdue,
            "last_action_type": last_action_type,
            "hours_since_last_action": hours_since_last_action,
            "prior_contacts_in_window": prior_contacts,
            "bank": bank_channel.bank,
            "psp": bank_channel.psp,
            "network_health_score": health_score,
            "payment_history_score": customer.payment_history_score,
            "past_recovery_rate": customer.past_recovery_rate,
            "preferred_channel": customer.preferred_channel,
            "candidate_action": cand["action_type"],
            "candidate_timing": cand["timing"],
            "candidate_timing_hours": cand["timing_hours"],
            "candidate_method": cand["method"],
            "candidate_method_changed": cand.get("method_changed", False),
            "candidate_channel": cand["channel"],
            "recovered": int(recovered),
            "recovered_amount": recovered_amount,
            "time_to_recovery_hours": ttr,
            "calibration_profile": profile.name,
            "generator_version": GENERATOR_VERSION,
        })

        truth_rows.append({
            "case_id": case_id,
            "candidate_action": cand["action_type"],
            "candidate_timing": cand["timing"],
            "candidate_method": cand["method"],
            "candidate_channel": cand["channel"],
            "analytic_p": p,
            "analytic_z": z,
            "recovered": int(recovered),
            "recovered_amount": recovered_amount,
            **{f"hidden_{k}": v for k, v in hidden.items()},
        })

    _record_customer_touchpoint(customer, sim_hour, rng)

    return joint_rows, truth_rows


def generate_dataset(profile, seed, n_cases=DEFAULT_N_CASES,
                      n_merchants=DEFAULT_N_MERCHANTS, n_customers=DEFAULT_N_CUSTOMERS,
                      horizon_hours=DEFAULT_HORIZON_HOURS):
    """
    Returns (joint_df, truth_df, world, health_index) for one full
    generation run under one calibration profile and one seed.

    Reproducibility: this function's ONLY sources of randomness are the
    two np.random.default_rng(seed) streams below (one for world/health
    construction, one for case generation) -- everything else is a pure
    function of (profile, that state). Identical (seed, profile,
    generator_version) therefore produces an identical world, identical
    health series, and identical case-by-case output, verified directly
    in PHASE2_NOTES.md via a two-run diff, not merely asserted here.
    """
    world_rng = np.random.default_rng(seed)
    world = ent.SyntheticWorld(world_rng, profile, n_merchants=n_merchants, n_customers=n_customers)
    health_obs = bht.generate_bank_health_timeseries(world_rng, world, horizon_hours, profile)
    health_index = bht.HealthIndex(health_obs)

    case_rng = np.random.default_rng(seed + 1)  # distinct stream, still fully seed-derived
    sim_hours = np.sort(case_rng.uniform(0, horizon_hours, size=n_cases))

    joint_rows, truth_rows = [], []
    for i in range(n_cases):
        jr, tr = generate_case_rows(i, float(sim_hours[i]), world, health_index, profile, case_rng)
        joint_rows.extend(jr)
        truth_rows.extend(tr)

    joint_df = pd.DataFrame(joint_rows)
    truth_df = pd.DataFrame(truth_rows)
    return joint_df, truth_df, world, health_index, health_obs
