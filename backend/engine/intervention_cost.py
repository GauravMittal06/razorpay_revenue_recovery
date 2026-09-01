"""
Phase 4 intervention-cost table.

SYNTHETIC PLACEHOLDER VALUES. Every number below is declared configuration
invented for this system, not a measured or sourced tariff. No Razorpay
price list, no telecom rate card, and no agent-cost study backs them. They
are stated in rupees per action so that Expected Incremental Value has a
consistent unit, and they must be replaced with real figures before any
non-synthetic claim is made from an EIV number.

This lives in its own module, separate from optimize.py, for one reason:
cost is the only term in the EIV expression that is NOT model output.
Keeping it physically separate means a reader auditing "where did this
number come from" can see at a glance which part of EIV was predicted and
which part was asserted by configuration.

    EIV = predicted_expected_recovered_amount(candidate)
        - predicted_expected_recovered_amount(do_nothing)
        - intervention_cost(candidate)

The first two terms come from ml/inference.py. The third comes from here.

--------------------------------------------------------------------------
Relative ordering, and why it is what it is
--------------------------------------------------------------------------
The absolute magnitudes matter far less than the ordering, because the
ordering is what makes "do nothing" a genuinely competitive option and what
suppresses marginal contact. The ordering is:

  1. do_nothing = 0, definitionally. The baseline must cost nothing, or the
     zero-point of EIV moves and do_nothing stops evaluating to exactly
     zero -- which is a locked invariant, not a convenience.

  2. An email reminder is the cheapest non-zero touch (2.0). Email is
     effectively free at volume and carries the lowest fatigue weight of
     any customer contact.

  3. A gateway retry (3.0) sits just above it. It makes no customer contact
     and so carries no fatigue component at all, but a payment-gateway
     re-attempt does carry a real per-attempt fee, which an email does not.
     It therefore lands between the cheapest and the more intrusive
     channels rather than below everything.

     (An earlier draft of this file described retry as the cheapest
     non-zero action, which contradicted the table below. The table is what
     was intended and this text was corrected to match it -- recorded in
     PHASE4_NOTES.md rather than silently amended.)

  4. Channel ordering is email < sms < whatsapp, on both actions that use
     a channel. Email is effectively free at volume; SMS carries a
     per-message telecom charge; WhatsApp Business messaging is charged per
     conversation at a rate above SMS. This ordering is directionally
     correct for the Indian payments market even though the magnitudes are
     invented.

  5. payment_link costs a flat +2.0 over reminder on the same channel. It
     is a reminder plus link generation and hosting, and it carries a
     higher fatigue weight because it asks the customer for money directly
     rather than merely informing them.

  6. escalate is two orders of magnitude above every automated action. It
     consumes human agent time, which is the scarcest resource in the
     system. This is deliberate: it means escalation must clear a large
     incremental-recovery bar before it can outrank an automated option,
     which is the correct economics and is also what stops escalate from
     winning ties by default.

Magnitude relative to EIV, stated plainly: amounts at risk in this system
run to thousands of rupees, so automated-action costs in the single digits
do not dominate the ranking. That is intended. Cost is here to break
near-ties and to make marginal contact unprofitable -- not to be the
deciding term. escalate's cost is the one figure large enough to change an
ordering on its own, which is exactly where a human-time cost should bite.
"""

# Rupees per action. Synthetic -- see module docstring.
COST_DO_NOTHING = 0.0

# Gateway re-attempt fee proxy. No human time, no customer contact.
COST_RETRY = 3.0

# Per-channel outreach cost: telecom/messaging charge plus a fatigue
# weighting that rises with how intrusive the channel is.
COST_REMINDER_BY_CHANNEL = {
    "email": 2.0,
    "sms": 5.0,
    "whatsapp": 7.0,
}

# A payment link is a reminder plus link generation/hosting, and asks for
# money directly -- a flat premium over the same channel's reminder.
PAYMENT_LINK_PREMIUM = 2.0

COST_PAYMENT_LINK_BY_CHANNEL = {
    channel: cost + PAYMENT_LINK_PREMIUM
    for channel, cost in COST_REMINDER_BY_CHANNEL.items()
}

# Human agent time. Two orders of magnitude above any automated action.
COST_ESCALATE = 250.0

# Used when an action carries a channel this table has no entry for. Set to
# the most expensive known channel rather than to zero or to an average:
# an unknown channel must never look CHEAPER than a known one, because that
# would make an unrecognised channel artificially attractive to the ranking.
UNKNOWN_CHANNEL_COST = max(COST_REMINDER_BY_CHANNEL.values()) + PAYMENT_LINK_PREMIUM


class UnknownActionCost(ValueError):
    """Raised when a candidate carries an action_type this table does not
    price. Deliberately raised rather than defaulted: a silently-zero cost
    would make an unpriced action the most attractive candidate in the
    ranking, which is the worst possible failure mode for this module."""


def intervention_cost(candidate: dict) -> float:
    """
    The cost term of EIV for one candidate, in rupees.

    Raises UnknownActionCost for an unpriced action_type. Callers do not
    catch this -- an unpriced action is a configuration bug that must
    surface, not a candidate to silently drop.
    """
    action = candidate.get("action_type")

    if action == "do_nothing":
        return COST_DO_NOTHING
    if action == "retry":
        return COST_RETRY
    if action == "escalate":
        return COST_ESCALATE
    if action == "reminder":
        return COST_REMINDER_BY_CHANNEL.get(
            candidate.get("channel"), UNKNOWN_CHANNEL_COST)
    if action == "payment_link":
        return COST_PAYMENT_LINK_BY_CHANNEL.get(
            candidate.get("channel"), UNKNOWN_CHANNEL_COST)

    raise UnknownActionCost(f"no cost defined for action_type={action!r}")
