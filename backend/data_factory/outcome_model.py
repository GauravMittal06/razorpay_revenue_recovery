"""
Data Factory -- the single shared generative outcome function. Phase 2.

Execution plan Section 5, Phase 2 is explicit: "For every candidate, draw
one stochastic potential outcome from a single shared generative function
taking the full candidate tuple as input -- not from separate
per-dimension functions." This module is that one function. Every
candidate for every case -- including do_nothing -- is scored by calling
`draw_outcome()` once, with the case's already-sampled hidden state
passed in unchanged. No other module in this package computes an outcome.

Generalizes (does not replace) the pre-Phase-2 generative structure in
data_factory/legacy/simulate_training_data_frozen.py: the same hidden-state
term, the same qualitative action-effectiveness-by-root-cause shape, and
the same qualitative retry-count-penalty shape are preserved, extended
with timing, method-change, channel, network-health, and fatigue terms
that the legacy module never had. See PHASE2_NOTES.md, section
"semantic equivalence to legacy", for the direct comparison this claim is
checked against.
"""

import numpy as np


def clip01(x):
    return float(np.clip(x, 0.0, 1.0))


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def hidden_state_term(hidden: dict) -> float:
    """Unchanged in shape from the legacy module's hidden_term -- same six
    hidden variables, same relative weights."""
    return (
        0.9 * hidden["liquidity_state"]
        + 0.6 * hidden["issuer_availability"]
        + 0.5 * hidden["payment_method_health"]
        + 0.9 * hidden["customer_responsiveness"]
        + 0.4 * hidden["bank_condition_temp"]
        + 1.0 * hidden["recovery_willingness"]
    )


def action_effectiveness(action_type, root_cause, event_type, method_changed):
    """Generalizes legacy's action_effectiveness(). Same qualitative
    shape (transient causes favor retry, needs_action causes penalize a
    same-method retry and favor reminder/escalate) plus: a method-changed
    retry recovers most of the penalty a needs_action root cause would
    otherwise apply to retry, since that's precisely the case a method
    change is structurally offered for (see candidate_generation.py's
    METHOD_CHANGE_RELEVANT_ROOT_CAUSES)."""
    if action_type == "do_nothing":
        # Natural/organic recovery baseline: customer may still resolve
        # the situation on their own, with no exogenous action term at
        # all -- this is what makes do_nothing a real, non-degenerate
        # baseline rather than a guaranteed-zero placeholder.
        return 0.0

    if event_type == "payment_failed":
        transient = {"gateway_timeout", "network_error"}
        needs_action = {"authentication_failed", "expired_card"}
        if action_type == "retry":
            if root_cause in transient:
                return 1.1
            if root_cause == "insufficient_funds":
                return -0.6
            if root_cause in needs_action:
                return -0.9 + (1.35 if method_changed else 0.0)
            return 0.0
        if action_type in ("reminder", "payment_link"):
            base = 0.7 if action_type == "reminder" else 0.55
            if root_cause in needs_action:
                return base
            if root_cause == "insufficient_funds":
                return base - 0.2
            return base - 0.5
        if action_type == "escalate":
            return 0.35

    if event_type == "checkout_abandoned":
        return {"reminder": 0.8, "retry": -0.3, "payment_link": 0.6, "escalate": 0.25}[action_type]

    if event_type == "invoice_overdue":
        return {"reminder": 0.6, "retry": -0.4, "payment_link": 0.55, "escalate": 0.4}[action_type]

    return 0.0


def retry_count_penalty(retry_count):
    """Unchanged in shape from legacy's retry_count_penalty()."""
    base_penalty = -0.10 * max(0, retry_count - 1)
    boundary_penalty = -0.15 if retry_count >= 3 else 0.0
    return base_penalty + boundary_penalty


def timing_term(action_type, root_cause, timing_hours):
    """New in Phase 2 -- legacy had no timing dimension at all. Encodes
    the SoT's directional claim: transient technical failures decay fast
    (delay hurts), insufficient_funds benefits from *some* wait (an
    inverted-U, since waiting too long is not free either), method-
    dependent causes are roughly timing-indifferent."""
    if action_type in ("do_nothing", "escalate"):
        return 0.0

    if root_cause in {"gateway_timeout", "network_error"}:
        return -0.02 * timing_hours  # fast is better, monotonically
    if root_cause == "insufficient_funds":
        # mild inverted-U centered near 24h -- some wait helps, too much doesn't
        return 0.25 - 0.0003 * (timing_hours - 24.0) ** 2
    return -0.004 * timing_hours  # mild generic decay for everything else


def decay_term(event_type, days_since_event, days_overdue):
    """Unchanged in shape from legacy's decay_term()."""
    if event_type == "invoice_overdue":
        return -0.09 * (days_overdue or 0)
    return -0.05 * (days_since_event or 0)


def amount_friction(event_type, amount):
    """Unchanged in shape from legacy's amount_friction()."""
    return -0.000004 * amount if event_type == "checkout_abandoned" else 0.0


def fatigue_term(action_type, prior_contacts_in_window, profile):
    """New in Phase 2, generalized across every contact-type candidate
    (reminder, payment_link, escalate, retry-with-contact) rather than
    scoped narrowly to retry, per Section 5 Phase 2's explicit
    requirement. do_nothing never incurs a fatigue penalty -- there is
    no contact to be fatigued by."""
    if action_type == "do_nothing":
        return 0.0
    return -profile.fatigue_penalty_per_contact * prior_contacts_in_window


def network_health_term(action_type, health_score, profile):
    """New in Phase 2. Only bites for actions that depend on a real
    payment attempt going through (retry, payment_link) -- a reminder or
    escalation doesn't touch the payment rails directly."""
    if action_type not in ("retry", "payment_link"):
        return 0.0
    if health_score is None:
        return 0.0
    return profile.network_health_weight * (health_score - 0.5)


def draw_outcome(action_type, root_cause, event_type, method_changed,
                  retry_count, timing_hours, days_since_event, days_overdue,
                  amount, prior_contacts_in_window, health_score,
                  hidden, profile, rng):
    """
    The single shared generative function. Called once per candidate,
    with the SAME `hidden` dict (the case's one hidden-state draw) and
    the SAME `rng` stream continuing forward -- never a fresh RNG per
    candidate, since that would silently break reproducibility ordering.

    Returns (recovered: bool, recovered_fraction: float in [0,1] of
    `amount`, time_to_recovery_hours: float or None, p: float, z: float).
    z and p are returned for the ground-truth check in validators.py --
    they are analytic facts about this draw, not model predictions, and
    are only ever consumed by validation code, never fed back in as a
    training feature (that would be leaking the generative mechanism's
    own logit into the dataset it produces).
    """
    z = (
        profile.global_intercept_shift
        + hidden_state_term(hidden)
        + action_effectiveness(action_type, root_cause, event_type, method_changed)
        + retry_count_penalty(retry_count)
        + timing_term(action_type, root_cause, timing_hours)
        + decay_term(event_type, days_since_event, days_overdue)
        + amount_friction(event_type, amount)
        + fatigue_term(action_type, prior_contacts_in_window, profile)
        + network_health_term(action_type, health_score, profile)
        + rng.normal(0, profile.outcome_noise_sigma)
    )
    p = sigmoid(z)
    recovered = bool(rng.random() < p)

    if not recovered:
        return False, 0.0, None, p, z

    if rng.random() < profile.partial_recovery_probability:
        frac = clip01(rng.beta(*profile.partial_recovery_fraction_beta))
    else:
        frac = 1.0

    # Faster recovery for higher p (a confidently-recoverable case tends
    # to resolve sooner once it resolves at all) plus timing offset (the
    # candidate's own timing floor) plus noise.
    ttr = max(0.5, timing_hours + rng.gamma(shape=2.0, scale=(1.0 - p) * 20.0 + 2.0))

    return True, frac, float(ttr), p, z
