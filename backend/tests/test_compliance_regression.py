"""
Compliance branch baseline -- the frozen behaviour Phase 5 must not change.

Phase 5 introduces the optimizer, which is advisory-only: it may reorder or
score candidates, but `decide_action()` remains the sole authority that can
set `allowed: True`. "Advisory-only" is unfalsifiable without a recorded
baseline, so this file is that record: one test per reachable branch of
decide_action(), asserting the exact (action_type, allowed, outcome) triple.

Two deliberate design choices:

* **Synthetic fixtures, not seed data.** decide_action() compares
  `opportunity["created_at"]` against the real `time.time()`, while the seed
  generator's clock is pinned to FIXED_NOW. Which branch a seeded row lands
  in is therefore a function of how long ago FIXED_NOW was -- a property of
  the calendar, not of the code. Every fixture here sets `created_at`
  explicitly relative to the current clock.

* **Assert the triple, not just the outcome.** `blocked_cooldown` with
  action_type None and `blocked_cooldown` with action_type "retry" are
  different contracts for the audit log, and only one of them is current
  behaviour.
"""

import time

import pytest

from backend.tests.conftest import (insert_decision, make_opportunity,
                                    outside_window_ts, recent_in_window_ts)

DAY = 86400
HOUR = 3600


def _decide(conn, opportunity, **kwargs):
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action

    classification = classify(opportunity["event_type"],
                              opportunity.get("root_cause"))
    return decide_action(opportunity, classification, conn, **kwargs)


def _triple(decision):
    return (decision["action_type"], decision["allowed"], decision["outcome"])


# --------------------------------------------------------------------------
# Branch 0: the pass-through. Everything else is a deviation from this.
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.compliance_baseline")
def test_clean_payment_failed_case_retries(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_clean_0001")
    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("retry", True, "executed"), decision
    assert decision["triggered_by"] == "rule"
    assert decision["reasoning"], "no action may be taken without a reason"


@pytest.mark.gate("phase1.compliance_baseline")
def test_default_action_is_event_type_dependent(seeded_db):
    """
    Only payment_failed defaults to `retry`; there is nothing to re-attempt
    on an abandoned checkout, so it gets a reminder.
    """
    failed = make_opportunity(seeded_db, opportunity_id="opp_cb_def_pf_0001",
                              event_type="payment_failed")
    abandoned = make_opportunity(seeded_db, opportunity_id="opp_cb_def_ca_0001",
                                 event_type="checkout_abandoned",
                                 root_cause=None)

    assert _decide(seeded_db, failed)["action_type"] == "retry"
    assert _decide(seeded_db, abandoned)["action_type"] == "reminder"


@pytest.mark.gate("phase1.compliance_baseline")
def test_deeply_overdue_invoice_escalates_instead_of_being_chased(seeded_db):
    """
    `days_overdue > 14` promotes default_action from `reminder` to
    `escalate`. Reaching that promotion requires a customer reply on file,
    because the auto-escalate branch (`days_overdue >= 7`) sits above it and
    would otherwise return `escalate` first for any value over 14 -- the two
    paths are distinguishable only by their reasoning string.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_inv_esc_0001",
                           event_type="invoice_overdue", root_cause=None,
                           days_overdue=15)
    seeded_db.execute(
        "INSERT INTO messages (opportunity_id, sender, content, timestamp) "
        "VALUES (?, 'customer', 'will pay next week', ?)",
        (opp["opportunity_id"], recent_in_window_ts()))
    seeded_db.commit()

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("escalate", True, "executed"), decision
    assert "No customer response" not in decision["reasoning"], (
        "reached escalate via the auto-escalate branch despite a customer "
        f"reply being on file: {decision['reasoning']!r}")



@pytest.mark.gate("phase1.compliance_baseline")
@pytest.mark.parametrize("days_overdue,expected_action", [
    (0, "reminder"),    # inside every threshold
    (6, "reminder"),    # one day short of AUTO_STOP_DAYS
    (7, "escalate"),    # AUTO_STOP_DAYS reached -> auto-escalate branch
    (14, "escalate"),
    (15, "escalate"),   # default_action promotion
])
def test_invoice_overdue_thresholds_are_pinned(seeded_db, days_overdue,
                                               expected_action):
    """
    invoice_overdue is the one event type whose timing comes from a column
    rather than from `created_at`, and it crosses two different thresholds
    (>=7 auto-escalate, >14 default promotion). Both produce `escalate`, so
    an off-by-one between them is invisible in the action alone -- which is
    exactly why the boundaries get pinned by value here.
    """
    opp = make_opportunity(seeded_db,
                           opportunity_id=f"opp_cb_inv_{days_overdue:02d}",
                           event_type="invoice_overdue", root_cause=None,
                           days_overdue=days_overdue)
    decision = _decide(seeded_db, opp)
    assert decision["action_type"] == expected_action, (
        f"days_overdue={days_overdue}: {decision}")
    assert decision["outcome"] == "executed"


# --------------------------------------------------------------------------
# Branch: terminal prior states
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.compliance_baseline")
def test_a_stopped_case_stays_stopped(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_stopped_0001")
    insert_decision(seeded_db, opp["opportunity_id"], "stop", outcome="executed")

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("stop", False, "blocked_already_stopped"), decision


@pytest.mark.gate("phase1.compliance_baseline")
def test_an_escalated_case_suspends_automation(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_escalated_0001")
    insert_decision(seeded_db, opp["opportunity_id"], "escalate", outcome="executed")

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("escalate", False, "blocked_already_escalated"), decision


@pytest.mark.gate("phase1.compliance_baseline")
def test_a_blocked_prior_attempt_is_not_a_terminal_state(seeded_db):
    """
    Counter-test. History is filtered on `outcome == 'executed'`; a *blocked*
    stop or escalate must not latch the case shut, or a single cooldown block
    would permanently freeze an otherwise live opportunity.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_notterm_0001")
    insert_decision(seeded_db, opp["opportunity_id"], "stop",
                    outcome="blocked_cooldown")
    insert_decision(seeded_db, opp["opportunity_id"], "escalate",
                    outcome="blocked_contact_hours")

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("retry", True, "executed"), decision


# --------------------------------------------------------------------------
# Branch: LLM intent pre-gate (advisory inputs, never action-selecting)
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.compliance_baseline")
def test_a_dispute_hard_stops_before_any_action_is_chosen(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_dispute_0001")
    decision = _decide(seeded_db, opp, dispute_flag=True)

    assert _triple(decision) == (None, False, "flagged_manual_review"), decision
    assert decision["flag_type"] == "dispute_flag"


@pytest.mark.gate("phase1.compliance_baseline")
@pytest.mark.parametrize("confidence,should_flag", [
    (0.0, True), (0.59, True), (0.6, False), (0.99, False),
])
def test_low_intent_confidence_routes_to_manual_review(seeded_db, confidence,
                                                       should_flag):
    """
    The 0.6 threshold is exclusive: exactly 0.6 proceeds. Pinned by value
    because a `<=` slip here silently widens or narrows the manual-review
    queue without changing any other observable.
    """
    opp = make_opportunity(seeded_db,
                           opportunity_id=f"opp_cb_conf_{int(confidence * 100):03d}")
    decision = _decide(seeded_db, opp, intent_confidence=confidence)

    if should_flag:
        assert _triple(decision) == (None, False, "flagged_manual_review"), decision
        assert decision["flag_type"] is None, (
            "a low-confidence flag must not be attributed to a mismatch it "
            f"never checked: {decision['flag_type']!r}")
    else:
        assert decision["outcome"] == "executed", decision


@pytest.mark.gate("phase1.compliance_baseline")
def test_intent_conflicting_with_root_cause_blocks(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_mismatch_0001",
                           root_cause="gateway_timeout")
    decision = _decide(seeded_db, opp, extracted_intent="will_pay_later",
                       mentioned_reason="insufficient_funds")

    assert _triple(decision) == (None, False, "flagged_manual_review"), decision
    assert decision["flag_type"] == "mismatch"


@pytest.mark.gate("phase1.compliance_baseline")
def test_a_method_update_is_a_log_only_flag_not_a_conflict(seeded_db):
    """
    The non-obvious half of the intent contract: `payment_method_updated`
    against a method-class root cause is a legitimate correction, so it
    annotates the decision rather than blocking it. A naive equality check
    would block here, and blocking would strand recoverable cases in the
    manual queue.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_methodupd_0001",
                           root_cause="gateway_timeout")
    decision = _decide(seeded_db, opp,
                       extracted_intent="payment_method_updated",
                       mentioned_reason="expired_card")

    assert _triple(decision) == ("retry", True, "executed"), decision
    assert decision["flag_type"] == "root_cause_update_candidate", decision


# --------------------------------------------------------------------------
# Branch: auto-escalate after 7 days of silence
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.compliance_baseline")
def test_seven_days_of_silence_auto_escalates(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_stale_0001",
                           created_at=int(time.time()) - 8 * DAY)
    decision = _decide(seeded_db, opp)

    assert _triple(decision) == ("escalate", True, "executed"), decision
    assert "No customer response" in decision["reasoning"]


@pytest.mark.gate("phase1.compliance_baseline")
def test_a_customer_reply_suppresses_auto_escalation(seeded_db):
    """
    The branch is conjunctive -- stale AND silent. Age alone must not
    escalate, or every long-running conversation would be handed to a human
    while it is still progressing.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_stale_reply_0001",
                           created_at=int(time.time()) - 8 * DAY)
    seeded_db.execute(
        "INSERT INTO messages (opportunity_id, sender, content, timestamp) "
        "VALUES (?, 'customer', 'sorry, paying today', ?)",
        (opp["opportunity_id"], int(time.time()) - HOUR))
    seeded_db.commit()

    decision = _decide(seeded_db, opp)
    assert decision["outcome"] != "executed" or decision["action_type"] != "escalate", (
        f"escalated despite a customer reply on file: {decision}")


@pytest.mark.gate("phase1.compliance_baseline")
def test_an_agent_message_is_not_a_customer_reply(seeded_db):
    """
    `_has_customer_reply` filters on sender='customer'. If it did not, every
    outbound reminder the engine itself sent would suppress the escalation
    that silence is supposed to trigger -- the case would go quiet forever.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_stale_agent_0001",
                           created_at=int(time.time()) - 8 * DAY)
    seeded_db.execute(
        "INSERT INTO messages (opportunity_id, sender, content, timestamp) "
        "VALUES (?, 'agent', 'reminder text', ?)",
        (opp["opportunity_id"], int(time.time()) - HOUR))
    seeded_db.commit()

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("escalate", True, "executed"), decision


# --------------------------------------------------------------------------
# Branch: attempt ceiling, cooldown, contact window
# --------------------------------------------------------------------------

def _seed_contacts(conn, opportunity_id, n, oldest_hours_ago=100,
                   spacing_hours=25, action_type="retry"):
    """
    n executed contacts, spaced wider than the 24h cooldown so the ceiling
    branch can be asserted without the cooldown branch confounding it. All
    timestamps stay in the past for every n this file uses.
    """
    now = int(time.time())
    for i in range(n):
        insert_decision(conn, opportunity_id, action_type, outcome="executed",
                        timestamp=now - (oldest_hours_ago - i * spacing_hours) * HOUR)


@pytest.mark.gate("phase1.compliance_baseline")
@pytest.mark.parametrize("prior_contacts,expected", [
    (0, ("retry", True, "executed")),
    (1, ("retry", True, "executed")),
    (2, ("retry", True, "executed")),
    (3, ("stop", True, "executed")),
    (4, ("stop", True, "executed")),
])
def test_the_three_contact_ceiling_is_pinned_by_value(seeded_db, prior_contacts,
                                                      expected):
    """
    MAX_RETRIES = 3 is a locked, non-negotiable compliance rule, so the
    boundary is asserted on both sides rather than at one point. Note the
    ceiling produces outcome `executed` with action `stop`: hitting the limit
    is not a blocked decision, it is the decision to stop.
    """
    opp = make_opportunity(seeded_db,
                           opportunity_id=f"opp_cb_ceiling_{prior_contacts}",
                           created_at=recent_in_window_ts(days_ago=5))
    _seed_contacts(seeded_db, opp["opportunity_id"], prior_contacts)

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == expected, (
        f"{prior_contacts} prior contacts: {decision}")


@pytest.mark.gate("phase1.compliance_baseline")
def test_reminders_count_toward_the_same_ceiling_as_retries(seeded_db):
    """
    contact_count combines retry and reminder. Counting them separately would
    permit six customer contacts under a rule that allows three.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_mixed_0001",
                           created_at=recent_in_window_ts(days_ago=4))
    now = int(time.time())
    insert_decision(seeded_db, opp["opportunity_id"], "retry",
                    outcome="executed", timestamp=now - 72 * HOUR)
    insert_decision(seeded_db, opp["opportunity_id"], "reminder",
                    outcome="executed", timestamp=now - 48 * HOUR)
    insert_decision(seeded_db, opp["opportunity_id"], "reminder",
                    outcome="executed", timestamp=now - 26 * HOUR)

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("stop", True, "executed"), decision


@pytest.mark.gate("phase1.compliance_baseline")
def test_blocked_contacts_do_not_consume_the_attempt_budget(seeded_db):
    """
    Counter-test to the ceiling. A contact the engine was *not permitted* to
    make cannot count against the customer's contact budget, or three
    consecutive out-of-hours blocks would close a case that was never
    actually contacted.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_blockedbudget_0001")
    for _ in range(4):
        insert_decision(seeded_db, opp["opportunity_id"], "retry",
                        outcome="blocked_contact_hours")

    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("retry", True, "executed"), decision


@pytest.mark.gate("phase1.compliance_baseline")
@pytest.mark.parametrize("hours_since_contact,expect_blocked", [
    (1, True), (23, True), (25, False),
])
def test_the_twenty_four_hour_cooldown_is_pinned_by_value(seeded_db,
                                                          hours_since_contact,
                                                          expect_blocked):
    opp = make_opportunity(seeded_db,
                           opportunity_id=f"opp_cb_cool_{hours_since_contact:02d}",
                           created_at=recent_in_window_ts(days_ago=3))
    insert_decision(seeded_db, opp["opportunity_id"], "retry", outcome="executed",
                    timestamp=int(time.time()) - hours_since_contact * HOUR)

    decision = _decide(seeded_db, opp)
    if expect_blocked:
        assert _triple(decision) == ("retry", False, "blocked_cooldown"), decision
        assert "remaining" in decision["reasoning"], (
            "a cooldown block must say how long remains, or the operator "
            f"cannot tell a short wait from a stuck case: {decision['reasoning']!r}")
    else:
        assert _triple(decision) == ("retry", True, "executed"), decision


@pytest.mark.gate("phase1.compliance_baseline")
def test_contact_outside_the_permitted_window_is_blocked(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_hours_0001",
                           created_at=outside_window_ts(days_ago=1, hour=3))
    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("retry", False, "blocked_contact_hours"), decision


@pytest.mark.gate("phase1.compliance_baseline")
@pytest.mark.parametrize("hour,expect_blocked", [
    (8, True), (9, False), (19, False), (20, True), (23, True),
])
def test_the_contact_window_boundaries_are_pinned(seeded_db, hour,
                                                  expect_blocked):
    """
    9am-8pm, start inclusive, end exclusive. 20:00 itself is outside.
    """
    opp = make_opportunity(seeded_db, opportunity_id=f"opp_cb_win_{hour:02d}",
                           created_at=recent_in_window_ts(days_ago=1, hour=hour))
    decision = _decide(seeded_db, opp)
    assert (decision["outcome"] == "blocked_contact_hours") is expect_blocked, (
        f"hour={hour}: {decision}")


@pytest.mark.gate("phase1.compliance_baseline")
def test_escalation_is_internal_routing_and_ignores_contact_hours(seeded_db):
    """
    The window protects the *customer* from being contacted at 3am. Handing a
    case to an internal human queue is not customer contact, so it must not
    be deferred -- otherwise a 3am dispute waits until 9am for triage.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cb_esc_hours_0001",
                           event_type="invoice_overdue", root_cause=None,
                           days_overdue=20,
                           created_at=outside_window_ts(days_ago=1, hour=3))
    decision = _decide(seeded_db, opp)
    assert _triple(decision) == ("escalate", True, "executed"), decision


# --------------------------------------------------------------------------
# Cross-branch invariants. These are the assertions Phase 5's optimizer must
# still satisfy, stated once rather than per branch.
# --------------------------------------------------------------------------

def _every_branch(conn):
    """
    One representative call per reachable branch, as (label, decision). Kept
    as a single generator so the invariants below cannot silently stop
    covering a branch that was added later.
    """
    now = int(time.time())
    cases = []

    def add(label, opportunity_id, kwargs=None, **overrides):
        opp = make_opportunity(conn, opportunity_id=opportunity_id, **overrides)
        cases.append((label, opp, kwargs or {}))
        return opp

    add("pass_through", "opp_inv_pass_0001")
    add("auto_escalate", "opp_inv_esc_0001", created_at=now - 8 * DAY)
    add("dispute", "opp_inv_disp_0001", kwargs={"dispute_flag": True})
    add("low_confidence", "opp_inv_conf_0001", kwargs={"intent_confidence": 0.1})
    add("mismatch", "opp_inv_mism_0001",
        kwargs={"extracted_intent": "will_pay_later",
                "mentioned_reason": "insufficient_funds"})
    add("contact_hours", "opp_inv_hours_0001",
        created_at=outside_window_ts(days_ago=1, hour=3))

    stopped = add("already_stopped", "opp_inv_stopped_0001")
    insert_decision(conn, stopped["opportunity_id"], "stop", outcome="executed")

    escalated = add("already_escalated", "opp_inv_escd_0001")
    insert_decision(conn, escalated["opportunity_id"], "escalate", outcome="executed")

    ceiling = add("attempt_ceiling", "opp_inv_ceil_0001",
                  created_at=recent_in_window_ts(days_ago=5))
    _seed_contacts(conn, ceiling["opportunity_id"], 3)

    cooldown = add("cooldown", "opp_inv_cool_0001",
                   created_at=recent_in_window_ts(days_ago=3))
    insert_decision(conn, cooldown["opportunity_id"], "retry", outcome="executed",
                    timestamp=now - HOUR)

    return [(label, _decide(conn, opp, **kwargs)) for label, opp, kwargs in cases]


@pytest.mark.gate("phase1.compliance_baseline")
def test_every_branch_is_reachable_and_distinct(seeded_db):
    """
    Guards the generator above. If a fixture drifts so that two labels land in
    the same branch, the invariant tests would still pass while silently
    covering less -- the classic way a regression suite rots.
    """
    branches = _every_branch(seeded_db)
    assert len(branches) == 10, f"branch inventory changed: {len(branches)}"

    seen = {}
    for label, decision in branches:
        seen.setdefault(_triple(decision), []).append(label)
    collisions = {k: v for k, v in seen.items() if len(v) > 1}
    # pass_through and the intent-flag branches legitimately share a triple
    # only if a fixture is wrong; the ceiling/auto-escalate pair does not.
    assert not collisions, f"fixtures collapsed onto the same branch: {collisions}"


@pytest.mark.gate("phase1.compliance_baseline")
def test_no_decision_is_ever_silent(seeded_db):
    """
    "Every action logged with a reason -- no silent actions" is a locked SoT
    rule. A branch that returns an empty reasoning string satisfies the
    schema and defeats the audit.
    """
    offenders = [(label, d) for label, d in _every_branch(seeded_db)
                 if not (d.get("reasoning") or "").strip()]
    assert not offenders, f"branches returning no reason: {offenders}"


@pytest.mark.gate("phase1.compliance_baseline")
def test_every_outcome_is_in_the_closed_vocabulary(seeded_db):
    from backend.db.db import DECISION_OUTCOMES

    for label, decision in _every_branch(seeded_db):
        assert decision["outcome"] in DECISION_OUTCOMES, \
            f"{label} produced outcome {decision['outcome']!r}"
        assert decision["triggered_by"] == "rule", \
            f"{label} attributed the decision to {decision['triggered_by']!r}"


@pytest.mark.gate("phase1.compliance_baseline")
def test_only_executed_outcomes_carry_allowed_true(seeded_db):
    """
    The authority invariant, stated behaviourally rather than by source scan:
    `allowed` and `outcome == "executed"` must agree on every branch. If they
    can diverge, "the optimizer never grants permission" becomes unverifiable
    because there would be two competing answers to what was permitted.
    """
    divergent = [(label, _triple(d)) for label, d in _every_branch(seeded_db)
                 if d["allowed"] != (d["outcome"] == "executed")]
    assert not divergent, \
        f"allowed and outcome disagree on: {divergent}"


@pytest.mark.gate("phase1.compliance_baseline")
def test_no_branch_permits_an_action_outside_the_executor_vocabulary(seeded_db):
    from backend.engine.execute_action import STATUS_MAP

    for label, decision in _every_branch(seeded_db):
        if decision["allowed"]:
            assert decision["action_type"] in STATUS_MAP, (
                f"{label} permitted {decision['action_type']!r}, which the "
                "executor has no status transition for")


@pytest.mark.gate("phase1.compliance_baseline")
def test_method_change_is_never_selected_by_any_branch(seeded_db):
    """
    Behavioural companion to the static scan in test_permanent_gates.py.
    `method_change` is the action the SoT forbids from having a reachable
    executor path; this asserts the decider never names it either.
    """
    named = [label for label, d in _every_branch(seeded_db)
             if d["action_type"] == "method_change"]
    assert not named, f"branches selecting method_change: {named}"


# PLACEHOLDER_APPEND
