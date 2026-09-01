"""
Shared candidate-generation module. Phase 2 (imported by the Data Factory)
and Phase 4 (imported, unmodified, by the live optimizer -- see execution
plan Section 5, Phase 2: "Candidate generation ... must be ONE shared
module usable both by this generator and by the live optimizer in
Phase 4 -- do not write it twice.").

Two cheap stages, exactly as Section 7 (Modeling and Optimization Design)
specifies, run before any model scoring happens:
  1. structural eligibility -- which action/timing/method/channel
     combinations are even meaningful given event_type, root_cause, and
     current compliance-relevant state (retry_count, already_escalated,
     already_stopped);
  2. a relevance pre-filter -- collapsing timing to root-cause-appropriate
     windows, collapsing channel to the customer's preferred option plus
     at most one exploratory alternative -- bounding the candidate set to
     a small constant.

This module has NO execution authority and must never gain any: it only
enumerates candidates. It does not decide, does not score, does not
write anything. Nothing in this file imports engine.execute_action,
engine.decide_action, or anything with write access to recovery_decisions
/ recovery_executions -- this is mechanically checkable (see
data_factory/validators.static_no_execution_authority) and is the same
authority boundary Phase 4/Phase 9 will enforce on the live optimizer.

`do_nothing` is always included, unconditionally, as the first candidate,
matching the Section 9 permanent invariant that "do nothing" is always a
scored candidate, never an implicit fallback.
"""

ACTION_TYPES = ["do_nothing", "retry", "reminder", "payment_link", "escalate"]
TIMING_HOURS = {"immediate": 0.0, "4h": 4.0, "24h": 24.0, "3d": 72.0}
METHODS = ["card", "netbanking", "upi", "wallet"]
CHANNELS = ["email", "sms", "whatsapp"]

MAX_RETRIES = 3  # matches the eligibility threshold already proven correct
                  # in ml/simulate_training_data.py's eligible_candidate_actions
                  # (retry_count < 3), preserved here rather than redefined.

# Root causes for which a payment-method change is a structurally
# meaningful candidate (mirrors the existing distinction the execution
# plan calls out in Section 1.6 / Phase 4). A method change is never
# offered for root causes it can't plausibly fix.
METHOD_CHANGE_RELEVANT_ROOT_CAUSES = {"expired_card", "authentication_failed"}

# Root-cause-appropriate timing windows -- collapses the naive 4-value
# cross product down to what's actually plausible per SoT Section 2:
# transient/technical failures want a fast retry; insufficient_funds
# benefits from waiting out a plausible salary/settlement cycle; a card/
# auth problem needs a method change more than a timing change.
TIMING_BY_ROOT_CAUSE = {
    "gateway_timeout": ["immediate", "4h"],
    "network_error": ["immediate", "4h"],
    "insufficient_funds": ["24h", "3d"],
    "expired_card": ["immediate", "24h"],
    "authentication_failed": ["immediate", "24h"],
    "payment_declined": ["4h", "24h"],
    None: ["4h", "24h"],  # non-payment_failed event types
}

EVENT_TYPE_DEFAULT_TIMING = {
    "checkout_abandoned": ["immediate", "4h", "24h"],
    "invoice_overdue": ["24h", "3d"],
}


def do_nothing_candidate():
    return {
        "action_type": "do_nothing",
        "timing": "n/a",
        "timing_hours": 0.0,
        "method": "n/a",
        "channel": "n/a",
    }


def eligible_timings(event_type, root_cause):
    if event_type == "payment_failed":
        return TIMING_BY_ROOT_CAUSE.get(root_cause, TIMING_BY_ROOT_CAUSE[None])
    return EVENT_TYPE_DEFAULT_TIMING.get(event_type, ["immediate", "24h"])


def eligible_channels(preferred_channel):
    """Bounded relevance filter: the customer's own preferred channel plus
    at most one deterministic exploratory alternative -- never the full
    channel list, per Section 7's 'bounds the candidate set to a small
    constant before any model scoring happens.'"""
    alternatives = [c for c in CHANNELS if c != preferred_channel]
    exploratory = alternatives[0] if alternatives else preferred_channel
    return [preferred_channel, exploratory]


def eligible_retry_methods(current_method, root_cause):
    """Same-method retry is always eligible (when retry itself is
    eligible). A method-change retry candidate is additionally offered
    only for root causes where changing method is structurally
    meaningful -- exactly one alternative method, not all of them, to
    keep this bounded."""
    methods = [current_method]
    if root_cause in METHOD_CHANGE_RELEVANT_ROOT_CAUSES:
        alternatives = [m for m in METHODS if m != current_method]
        if alternatives:
            methods.append(alternatives[0])
    return methods


def generate_candidates(context: dict) -> list:
    """
    context keys (all required except where noted):
      event_type: 'checkout_abandoned' | 'payment_failed' | 'invoice_overdue'
      root_cause: str or None (meaningful only for payment_failed)
      retry_count: int
      current_method: str
      preferred_channel: str
      already_escalated: bool (default False)
      already_stopped: bool (default False)

    Returns a list of candidate dicts, always including exactly one
    do_nothing candidate first. If already_stopped or already_escalated,
    only do_nothing is returned -- there is nothing structurally eligible
    to propose for a terminal opportunity; this is a relevance judgement
    made here, not a compliance override of the rule engine, which still
    independently re-checks and is the only component with authority to
    block.
    """
    event_type = context["event_type"]
    root_cause = context.get("root_cause")
    retry_count = context.get("retry_count", 0)
    current_method = context.get("current_method", "card")
    preferred_channel = context.get("preferred_channel", "email")
    already_escalated = context.get("already_escalated", False)
    already_stopped = context.get("already_stopped", False)

    candidates = [do_nothing_candidate()]

    if already_escalated or already_stopped:
        return candidates

    timings = eligible_timings(event_type, root_cause)
    channels = eligible_channels(preferred_channel)

    # retry -- only structurally eligible for payment_failed, under the
    # max-retries threshold.
    if event_type == "payment_failed" and retry_count < MAX_RETRIES:
        for method in eligible_retry_methods(current_method, root_cause):
            for timing in timings:
                candidates.append({
                    "action_type": "retry",
                    "timing": timing,
                    "timing_hours": TIMING_HOURS[timing],
                    "method": method,
                    "channel": "n/a",
                    "method_changed": method != current_method,
                })

    # reminder -- eligible for every event type.
    for timing in timings:
        for channel in channels:
            candidates.append({
                "action_type": "reminder",
                "timing": timing,
                "timing_hours": TIMING_HOURS[timing],
                "method": "n/a",
                "channel": channel,
            })

    # payment_link -- eligible for payment_failed and invoice_overdue only
    # (a checkout_abandoned case has no failed payment to re-link).
    if event_type in ("payment_failed", "invoice_overdue"):
        for timing in timings:
            for channel in channels:
                candidates.append({
                    "action_type": "payment_link",
                    "timing": timing,
                    "timing_hours": TIMING_HOURS[timing],
                    "method": "n/a",
                    "channel": channel,
                })

    # escalate -- always structurally available as a fallback candidate,
    # immediate timing only (escalation isn't scheduled).
    candidates.append({
        "action_type": "escalate",
        "timing": "immediate",
        "timing_hours": 0.0,
        "method": "n/a",
        "channel": preferred_channel,
    })

    return candidates
