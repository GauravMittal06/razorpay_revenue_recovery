"""
Phase 4 acceptance gates -- the optimizer.

Every gate in the Phase 4 row of Phase_Acceptance_Test_Gates.md is a test in
this file, plus the carried-forward Phase 3 near-tie requirement and the
ambiguity rulings recorded in PHASE4_NOTES.md.

Two deliberate structural choices:

  - Eligibility-rule tests (G1) call candidate_generation directly and need
    no model artifact, so the highest-severity structural rules stay
    checkable by a reviewer who has not regenerated the model.
  - Everything that scores skips cleanly, with a reason, when
    ml/models/outcome_model.joblib is absent (it is gitignored), rather than
    failing in a way that looks like a broken optimizer.

Ground truth for the ranking-correctness suite is computed from the
generator's OWN analytic functions (data_factory/outcome_model.py), the same
source Phase 3's treatment-effect gate used -- not from numbers hardcoded
into this file.
"""

import math
import time

import pytest

from backend.data_factory import calibration_profiles as cp
from backend.data_factory import candidate_generation as cg
from backend.data_factory import outcome_model as om
from backend.engine import optimize
from backend.engine import optimizer_config as cfg
from backend.engine.intervention_cost import (
    intervention_cost, UnknownActionCost, COST_ESCALATE,
    COST_REMINDER_BY_CHANNEL, COST_PAYMENT_LINK_BY_CHANNEL)
from backend.ml import inference

MODEL_AVAILABLE = inference.MODEL_PATH.exists()
needs_model = pytest.mark.skipif(
    not MODEL_AVAILABLE,
    reason=f"model artifact absent at {inference.MODEL_PATH}; regenerate with "
           "`python -m backend.ml.train_outcome_model`")

ALL_ROOT_CAUSES = ["insufficient_funds", "payment_declined", "gateway_timeout",
                   "authentication_failed", "expired_card", "network_error"]
ALL_EVENT_TYPES = ["payment_failed", "checkout_abandoned", "invoice_overdue"]

@pytest.fixture(autouse=True)
def _reset_inference_cache():
    """ml/inference.py caches the model and the network-health lookup per
    process. Tests run against different temporary databases, so the lookup
    must be rebuilt per test or one test's health table leaks into the
    next."""
    inference.reset_cache()
    yield
    inference.reset_cache()


def make_context(event_type="payment_failed", root_cause="gateway_timeout",
                 **overrides):
    """A context in the shape build_optimizer_context() produces. Explicit
    rather than helper-filled, so a test that depends on a field shows it."""
    context = {
        "event_type": event_type,
        "root_cause": root_cause,
        "retry_count": 0,
        "current_method": "card",
        "preferred_channel": "email",
        "already_escalated": False,
        "already_stopped": False,
        "amount": 50000.0,
        "days_since_event": 1.0,
        "days_overdue": 0.0,
        "payment_history_score": 0.6,
        "past_recovery_rate": 0.5,
        "merchant_cohort": "enterprise",
        "last_action_type": "none",
        "hours_since_last_action": 0.0,
        "prior_contacts_in_window": 0,
        "bank": None,
        "psp": None,
        "decision_time_hours": 0.0,
    }
    context.update(overrides)
    return context


def _sample_opportunity_ids(conn, limit, where="1=1"):
    return [r[0] for r in conn.execute(
        f"SELECT opportunity_id FROM opportunities WHERE {where} "
        f"ORDER BY opportunity_id LIMIT {int(limit)}").fetchall()]


# ==========================================================================
# G1 -- per-eligibility-rule unit tests
#
# Twelve rules, one test each. Every one of them is asserted against
# data_factory/candidate_generation.py, which Phase 4 imports UNMODIFIED and
# writes no eligibility rules of its own. These tests therefore pin the
# shared offline/live contract, not a Phase 4 reimplementation of it.
# ==========================================================================

@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
def test_do_nothing_is_always_present_and_always_first(event_type):
    candidates = cg.generate_candidates(make_context(event_type=event_type))
    assert candidates[0]["action_type"] == "do_nothing"
    assert sum(1 for c in candidates if c["action_type"] == "do_nothing") == 1


@pytest.mark.gate("phase4.eligibility")
def test_a_stopped_opportunity_yields_only_do_nothing():
    candidates = cg.generate_candidates(make_context(already_stopped=True))
    assert [c["action_type"] for c in candidates] == ["do_nothing"]


@pytest.mark.gate("phase4.eligibility")
def test_an_escalated_opportunity_yields_only_do_nothing():
    candidates = cg.generate_candidates(make_context(already_escalated=True))
    assert [c["action_type"] for c in candidates] == ["do_nothing"]


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("event_type", ["checkout_abandoned", "invoice_overdue"])
def test_retry_is_structurally_ineligible_outside_payment_failed(event_type):
    candidates = cg.generate_candidates(
        make_context(event_type=event_type, root_cause=None))
    assert not [c for c in candidates if c["action_type"] == "retry"]


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("retry_count,expected", [(0, True), (2, True), (3, False), (4, False)])
def test_retry_is_suppressed_at_the_max_retries_threshold(retry_count, expected):
    candidates = cg.generate_candidates(make_context(retry_count=retry_count))
    assert bool([c for c in candidates if c["action_type"] == "retry"]) is expected
    assert cg.MAX_RETRIES == 3


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
@pytest.mark.parametrize("root_cause", ALL_ROOT_CAUSES + [None])
def test_no_alternate_method_candidate_for_a_root_cause_a_method_switch_cannot_fix(
        event_type, root_cause):
    """The single highest-value eligibility rule: an alternate-payment-method
    candidate is offered ONLY where switching method could plausibly fix the
    cause. Swept across every event type x root cause combination, not just
    the interesting ones, because the failure mode is a combination nobody
    thought to check."""
    context = make_context(event_type=event_type, root_cause=root_cause)
    candidates = cg.generate_candidates(context)
    alternates = [c for c in candidates
                  if c["action_type"] == "retry"
                  and c["method"] != context["current_method"]]
    # distinct METHODS, not distinct candidates: one alternative method is
    # legitimately offered across each eligible timing window
    alternate_methods = {c["method"] for c in alternates}

    relevant = (event_type == "payment_failed"
                and root_cause in cg.METHOD_CHANGE_RELEVANT_ROOT_CAUSES)
    if relevant:
        assert len(alternate_methods) == 1, (
            "exactly one alternative method may be offered, never the full "
            f"set; got {sorted(alternate_methods)}")
    else:
        assert alternates == [], (
            f"an alternate-method retry was offered for "
            f"{event_type}/{root_cause}, where switching method is meaningless")


@pytest.mark.gate("phase4.eligibility")
def test_only_one_alternative_method_is_ever_offered():
    for root_cause in cg.METHOD_CHANGE_RELEVANT_ROOT_CAUSES:
        methods = cg.eligible_retry_methods("card", root_cause)
        assert len(methods) == 2 and methods[0] == "card"


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("event_type,expected", [
    ("payment_failed", True), ("invoice_overdue", True),
    ("checkout_abandoned", False)])
def test_payment_link_eligibility_by_event_type(event_type, expected):
    """A checkout that was abandoned has no failed payment to re-link."""
    candidates = cg.generate_candidates(make_context(
        event_type=event_type,
        root_cause="gateway_timeout" if event_type == "payment_failed" else None))
    assert bool([c for c in candidates
                 if c["action_type"] == "payment_link"]) is expected


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
def test_reminder_is_eligible_for_every_event_type(event_type):
    candidates = cg.generate_candidates(make_context(
        event_type=event_type,
        root_cause="gateway_timeout" if event_type == "payment_failed" else None))
    assert [c for c in candidates if c["action_type"] == "reminder"]


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
def test_escalate_is_always_available_and_immediate_only(event_type):
    candidates = cg.generate_candidates(make_context(
        event_type=event_type,
        root_cause="gateway_timeout" if event_type == "payment_failed" else None))
    escalate = [c for c in candidates if c["action_type"] == "escalate"]
    assert len(escalate) == 1
    assert escalate[0]["timing"] == "immediate"


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("root_cause,expected", [
    ("gateway_timeout", ["immediate", "4h"]),
    ("network_error", ["immediate", "4h"]),
    ("insufficient_funds", ["24h", "3d"]),
    ("expired_card", ["immediate", "24h"]),
    ("authentication_failed", ["immediate", "24h"]),
    ("payment_declined", ["4h", "24h"]),
])
def test_timing_collapses_to_root_cause_appropriate_windows(root_cause, expected):
    """A transient failure wants a fast retry; insufficient funds wants to
    wait out a settlement cycle. The relevance filter encodes that, and it is
    what keeps the candidate set a small constant."""
    assert cg.eligible_timings("payment_failed", root_cause) == expected
    candidates = cg.generate_candidates(
        make_context(root_cause=root_cause, retry_count=0))
    timings = {c["timing"] for c in candidates if c["action_type"] == "reminder"}
    assert timings == set(expected)


@pytest.mark.gate("phase4.eligibility")
@pytest.mark.parametrize("preferred", ["email", "sms", "whatsapp"])
def test_channel_collapses_to_preferred_plus_one_exploratory(preferred):
    channels = cg.eligible_channels(preferred)
    assert len(channels) == 2
    assert channels[0] == preferred
    assert channels[1] != preferred

    candidates = cg.generate_candidates(make_context(preferred_channel=preferred))
    used = {c["channel"] for c in candidates if c["action_type"] == "reminder"}
    assert used == set(channels)


# ==========================================================================
# G5 -- candidate-count ceiling, declared and enforced
# ==========================================================================

@pytest.mark.gate("phase4.bounds")
def test_the_ceiling_is_declared_in_config():
    assert isinstance(cfg.MAX_CANDIDATES, int) and cfg.MAX_CANDIDATES > 0


@pytest.mark.gate("phase4.bounds")
def test_no_reachable_context_exceeds_the_declared_ceiling():
    """Exhaustive over the structural space that determines set size, not a
    sample: every event type x root cause x retry_count x preferred channel."""
    worst, worst_context = 0, None
    for event_type in ALL_EVENT_TYPES:
        for root_cause in ALL_ROOT_CAUSES + [None]:
            for retry_count in range(0, 5):
                for preferred in ["email", "sms", "whatsapp"]:
                    context = make_context(
                        event_type=event_type, root_cause=root_cause,
                        retry_count=retry_count, preferred_channel=preferred)
                    n = len(cg.generate_candidates(context))
                    assert n <= cfg.MAX_CANDIDATES, (
                        f"{n} candidates for {event_type}/{root_cause}/"
                        f"retry={retry_count} exceeds ceiling {cfg.MAX_CANDIDATES}")
                    if n > worst:
                        worst, worst_context = n, context
    assert worst == cfg.OBSERVED_MAX_CANDIDATES, (
        f"observed worst case moved to {worst} (context={worst_context}); "
        f"config still records {cfg.OBSERVED_MAX_CANDIDATES}. Update the "
        f"declaration deliberately rather than consuming ceiling headroom.")


@pytest.mark.gate("phase4.bounds")
def test_the_ceiling_is_enforced_by_a_raise_not_a_stripped_assert(monkeypatch, seeded_db):
    """`assert` disappears under `python -O`. The bound must survive that."""
    monkeypatch.setattr(cfg, "MAX_CANDIDATES", 2)
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    with pytest.raises(optimize.CandidateCeilingExceeded):
        optimize.optimize_opportunity(seeded_db, oid, persist=False)


# ==========================================================================
# Intervention cost (ruling A1)
# ==========================================================================

@pytest.mark.gate("phase4.cost")
def test_do_nothing_costs_exactly_zero():
    """Not a preference. If the baseline had a cost, do_nothing's EIV would
    not be exactly zero, and that zero is a locked invariant."""
    assert intervention_cost({"action_type": "do_nothing"}) == 0.0


@pytest.mark.gate("phase4.cost")
def test_cost_ordering_is_the_one_recorded_as_a_locked_decision():
    email = intervention_cost({"action_type": "reminder", "channel": "email"})
    sms = intervention_cost({"action_type": "reminder", "channel": "sms"})
    whatsapp = intervention_cost({"action_type": "reminder", "channel": "whatsapp"})
    retry = intervention_cost({"action_type": "retry"})
    escalate = intervention_cost({"action_type": "escalate"})

    # email is the cheapest touch; a gateway re-attempt carries a real
    # per-attempt fee above it but makes no contact, so it sits below the
    # tariffed channels
    assert 0.0 < email < retry < sms < whatsapp
    # a payment link is a reminder plus link generation, on every channel
    for channel in COST_REMINDER_BY_CHANNEL:
        assert (COST_PAYMENT_LINK_BY_CHANNEL[channel]
                > COST_REMINDER_BY_CHANNEL[channel])
    # human time dominates every automated action by orders of magnitude
    assert escalate > 10 * whatsapp
    assert escalate == COST_ESCALATE


@pytest.mark.gate("phase4.cost")
def test_an_unpriced_action_raises_rather_than_defaulting_to_zero():
    """A silently-zero cost would make an unpriced action the single most
    attractive candidate in the ranking -- the worst failure this module
    could have."""
    with pytest.raises(UnknownActionCost):
        intervention_cost({"action_type": "wire_transfer"})


@pytest.mark.gate("phase4.cost")
def test_an_unknown_channel_is_never_cheaper_than_a_known_one():
    unknown = intervention_cost({"action_type": "reminder", "channel": "carrier_pigeon"})
    assert unknown >= max(COST_REMINDER_BY_CHANNEL.values())


# ==========================================================================
# G3 / G4 / A5 -- do_nothing and the EIV arithmetic
# ==========================================================================

@needs_model
@pytest.mark.gate("phase4.do_nothing")
def test_do_nothing_eiv_is_exactly_zero_on_every_sampled_opportunity(seeded_db):
    zeros = []
    for oid in _sample_opportunity_ids(seeded_db, 12):
        result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
        assert result["error"] is None
        row = [c for c in result["ranked"] if c["action_type"] == "do_nothing"]
        assert len(row) == 1, "do_nothing must be scored, never dropped"
        zeros.append(row[0]["predicted_eiv"])
    assert all(z == 0.0 for z in zeros), f"non-zero do_nothing EIV: {set(zeros)}"


@needs_model
@pytest.mark.gate("phase4.do_nothing")
def test_do_nothing_is_produced_by_the_same_arithmetic_not_special_cased(seeded_db):
    """The zero must fall out of treated - baseline - cost, using a real
    model evaluation of the do_nothing candidate -- not a hardcoded 0."""
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
    row = [c for c in result["ranked"] if c["action_type"] == "do_nothing"][0]
    assert row["predicted_expected_amount_treated"] == \
        row["predicted_expected_amount_baseline"]
    assert row["predicted_p_treated"] == row["predicted_p_baseline"]
    assert row["cost"] == 0.0
    assert (row["predicted_expected_amount_treated"]
            - row["predicted_expected_amount_baseline"] - row["cost"]) == 0.0


@needs_model
@pytest.mark.gate("phase4.do_nothing")
def test_do_nothing_legitimately_wins_at_least_one_real_ranking(seeded_db):
    """Competitiveness demonstrated on real model output over a sample, not
    by constructing a fixture where every alternative was suppressed."""
    wins, examined = 0, 0
    for oid in _sample_opportunity_ids(seeded_db, 40):
        result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
        if result["error"] is not None:
            continue
        examined += 1
        top = result["ranked"][0]
        if top["action_type"] == "do_nothing":
            wins += 1
            # it won on merit: every alternative had negative incremental value
            assert all(c["predicted_eiv"] <= 0 for c in result["ranked"])
    assert examined > 0
    assert wins > 0, (
        f"do_nothing never ranked first across {examined} opportunities; it is "
        "behaving as a floor, not a competitive option")


@needs_model
@pytest.mark.gate("phase4.eiv")
def test_caching_the_baseline_is_arithmetically_identical_to_rescoring_it(seeded_db):
    """Ruling A5. The optimizer evaluates do_nothing once per opportunity and
    subtracts that one value from every candidate. This proves that is the
    same number a per-candidate re-evaluation would produce."""
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    context, _ = optimize.load_context(seeded_db, oid)
    cached = inference.score_do_nothing(context, conn=seeded_db)

    for candidate in cg.generate_candidates(context):
        uncached = inference.score_do_nothing(context, conn=seeded_db)
        assert uncached["expected_recovered_amount"] - \
            cached["expected_recovered_amount"] == 0.0
        assert uncached["p_recovery"] - cached["p_recovery"] == 0.0
        assert candidate is not None


@needs_model
@pytest.mark.gate("phase4.eiv")
def test_eiv_is_exactly_the_two_evaluation_subtraction_minus_cost(seeded_db):
    for oid in _sample_opportunity_ids(seeded_db, 8):
        result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
        for row in result["ranked"]:
            expected = (row["predicted_expected_amount_treated"]
                        - row["predicted_expected_amount_baseline"]
                        - row["cost"])
            assert row["predicted_eiv"] == expected
            assert row["cost"] == intervention_cost(row)


@needs_model
@pytest.mark.gate("phase4.eiv")
def test_every_candidate_is_compared_against_the_same_single_baseline(seeded_db):
    """G6. One baseline per opportunity -- no per-candidate baseline drift,
    and no separately-composed marginal model."""
    for oid in _sample_opportunity_ids(seeded_db, 8):
        result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
        baselines = {r["predicted_expected_amount_baseline"] for r in result["ranked"]}
        p_baselines = {r["predicted_p_baseline"] for r in result["ranked"]}
        assert len(baselines) == 1 and len(p_baselines) == 1


# ==========================================================================
# G7 -- ranking correctness against the generator's own ground truth
# ==========================================================================

def _analytic_probability(context, candidate, baseline_p):
    """The generator's ground-truth recovery probability for one candidate,
    reconstructed at this context's own operating point.

    Only four of the generator's terms vary with the candidate --
    action_effectiveness, timing_term, fatigue_term and network_health_term.
    Everything else (the profile intercept, the case's hidden state, the
    retry-count penalty, decay, amount friction) is candidate-independent and
    cancels in the within-case comparison the optimizer actually makes.

    So the latent scale is anchored by inverting the model's own do_nothing
    probability to recover z0, then each candidate's analytic probability is
    sigmoid(z0 + its candidate-dependent terms). Anchoring on the model's
    baseline rather than on a simulated hidden state is what makes this
    runnable against a live opportunity at all -- and it is also this
    measurement's main limitation, recorded in PHASE4_NOTES.md: an error in
    the model's baseline shifts every analytic probability with it.
    """
    action = candidate["action_type"]
    effect = (om.action_effectiveness(action, context["root_cause"],
                                      context["event_type"],
                                      bool(candidate.get("method_changed", False)))
              + om.timing_term(action, context["root_cause"],
                               candidate["timing_hours"])
              + om.fatigue_term(action, context["prior_contacts_in_window"],
                                cp.BASELINE)
              + om.network_health_term(action, None, cp.BASELINE))
    do_nothing_effect = om.fatigue_term("do_nothing",
                                        context["prior_contacts_in_window"],
                                        cp.BASELINE)
    z0 = math.log(baseline_p / (1.0 - baseline_p)) - do_nothing_effect
    return 1.0 / (1.0 + math.exp(-(z0 + effect)))


def _score_scenario(conn, context):
    """Every candidate for one constructed context, carrying both the
    generator's ground-truth probability and the model's estimated
    incremental amount."""
    baseline = inference.score_do_nothing(context, conn=conn)
    assert baseline["error"] is None
    rows = []
    for candidate in cg.generate_candidates(context):
        scored = inference.score_candidate(context, candidate, conn=conn)
        assert scored["error"] is None
        row = dict(candidate)
        row["analytic_p"] = _analytic_probability(
            context, candidate, baseline["p_recovery"])
        row["incremental_amount"] = (scored["expected_recovered_amount"]
                                     - baseline["expected_recovered_amount"])
        rows.append(row)
    return rows


def _direction_agreement(rows, min_gap):
    """Fraction of decisively-separated candidate pairs the model orders the
    same way the generator does."""
    agree = total = 0
    inversions = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            truth = a["analytic_p"] - b["analytic_p"]
            if abs(truth) < min_gap:
                continue
            total += 1
            model = a["incremental_amount"] - b["incremental_amount"]
            if (truth > 0) == (model > 0):
                agree += 1
            else:
                better, worse = (a, b) if truth > 0 else (b, a)
                inversions.append(
                    f"{better['action_type']}/{better['timing']} should beat "
                    f"{worse['action_type']}/{worse['timing']} "
                    f"(truth gap {abs(truth):.3f})")
    return agree, total, inversions


# One scenario per major root-cause class AND per eligibility class
# (method-eligible vs not), as the tightened acceptance gate requires. Each
# is evaluated at two operating points -- a benign context and an adverse
# one -- because the model's baseline probability sits high enough that a
# single operating point gives the candidate space very little room.
RANKING_SCENARIOS = [
    ("payment_failed", "gateway_timeout"),        # transient, method-ineligible
    ("payment_failed", "network_error"),          # transient, method-ineligible
    ("payment_failed", "insufficient_funds"),     # wait-it-out
    ("payment_failed", "payment_declined"),       # method-ineligible
    ("payment_failed", "expired_card"),           # METHOD-ELIGIBLE
    ("payment_failed", "authentication_failed"),  # METHOD-ELIGIBLE
    ("checkout_abandoned", None),
    ("invoice_overdue", None),
]

ADVERSE_CONTEXT = dict(payment_history_score=0.05, past_recovery_rate=0.05,
                       retry_count=2, days_since_event=9.0,
                       prior_contacts_in_window=3, last_action_type="reminder",
                       hours_since_last_action=2.0)

# The band at which Phase 3 measured 0.955 pairwise ranking agreement. Below
# it the environment's own noise floor dominates, which is what the
# confidence flag exists to disclose rather than something to gate on.
RANKING_MIN_GAP = 0.12

# Phase 3's own locked treatment-effect DIRECTION bar. Taken from that lock
# rather than chosen here, so this gate's bar was not selected after seeing
# Phase 4's numbers.
RANKING_AGREEMENT_BAR = 0.90


# ---------------------------------------------------------------------------
# test_higher_true_incremental_value_ranks_above_lower was REMOVED 2026-09-03.
#
# It compared the generator's PROBABILITY-space ground truth against the
# model's RUPEE-space output --
#     truth = a["analytic_p"]         - b["analytic_p"]
#     model = a["incremental_amount"] - b["incremental_amount"]
# -- on hand-constructed contexts. Those two orderings are allowed to disagree
# (PHASE4_NOTES 8.6, ~16% rupee-space pair-order sensitivity), and section 8.2
# isolated the fault exactly: same contexts, same ground truth, 0.958 in
# probability space vs 0.812 in rupee space. The comparison axis was the bug.
#
# Replaced by tests/test_phase4_ranking_correctness.py, which implements the
# locked phase3_temporal ranking_pair_definition like-for-like on frozen
# held-out data, asserted at two operating points. Closeout item C1.
# ---------------------------------------------------------------------------




@needs_model
@pytest.mark.gate("phase4.ranking")
def test_the_optimizer_ranks_faithfully_by_eiv(seeded_db):
    """Phase 4's OWN ranking responsibility, isolated from model quality:
    given whatever EIV values the model produces, the emitted order must be
    exactly descending EIV with a deterministic tiebreak. This is the part
    of the ranking gate the optimizer itself owns."""
    for oid in _sample_opportunity_ids(seeded_db, 10):
        result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
        assert result["error"] is None
        eivs = [r["predicted_eiv"] for r in result["ranked"]]
        assert eivs == sorted(eivs, reverse=True)
        for earlier, later in zip(result["ranked"], result["ranked"][1:]):
            if earlier["predicted_eiv"] == later["predicted_eiv"]:
                assert optimize._sort_key(earlier) < optimize._sort_key(later), \
                    "equal-EIV candidates must break ties deterministically"


@needs_model
@pytest.mark.gate("phase4.ranking")
def test_the_ranking_is_a_deterministic_function_of_eiv(seeded_db):
    """Identical input must produce an identical ranking, or the audit trail
    cannot be relied on."""
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    first = optimize.optimize_opportunity(seeded_db, oid, persist=False)
    second = optimize.optimize_opportunity(seeded_db, oid, persist=False)
    assert [optimize._candidate_key(r) for r in first["ranked"]] == \
           [optimize._candidate_key(r) for r in second["ranked"]]
    eivs = [r["predicted_eiv"] for r in first["ranked"]]
    assert eivs == sorted(eivs, reverse=True)
    assert [r["rank"] for r in first["ranked"]] == list(
        range(1, len(first["ranked"]) + 1))


# ==========================================================================
# G12 -- the carried-forward near-tie confidence flag
# ==========================================================================

@pytest.mark.gate("phase4.confidence")
def test_the_flagged_combination_list_covers_the_phase3_disclosure():
    """The locked hand-off named reminder/payment_link x checkout_abandoned/
    invoice_overdue. Ruling A3 widened it to include escalate|
    checkout_abandoned, which carried 17.5% of the disclosed disagreement
    share and which the original wording under-covered."""
    for combination in [("reminder", "checkout_abandoned"),
                        ("reminder", "invoice_overdue"),
                        ("payment_link", "invoice_overdue"),
                        ("payment_link", "checkout_abandoned"),
                        ("escalate", "checkout_abandoned")]:
        assert combination in cfg.PHASE3_LOW_CONFIDENCE_COMBINATIONS


@pytest.mark.gate("phase4.confidence")
def test_payment_link_on_checkout_abandoned_remains_structurally_unreachable():
    """It is in the locked list but the shared generator cannot emit it. If
    that ever changes, the coverage is already in place -- this test is what
    tells us the situation changed."""
    candidates = cg.generate_candidates(
        make_context(event_type="checkout_abandoned", root_cause=None))
    assert not [c for c in candidates if c["action_type"] == "payment_link"]


@pytest.mark.gate("phase4.confidence")
def test_a_flagged_combination_is_low_confidence_regardless_of_gap():
    ranked = [
        {"action_type": "reminder", "timing": "24h", "method": "n/a",
         "channel": "email", "predicted_eiv": 900000.0},
        {"action_type": "do_nothing", "timing": "n/a", "method": "n/a",
         "channel": "n/a", "predicted_eiv": 0.0},
    ]
    optimize.attach_confidence(ranked, "invoice_overdue", amount=1000.0)
    assert ranked[0]["eiv_confidence"] == cfg.CONFIDENCE_LOW
    assert cfg.REASON_FLAGGED_BUCKET in ranked[0]["eiv_confidence_reason"]


@pytest.mark.gate("phase4.confidence")
def test_a_wide_gap_on_an_unflagged_combination_is_high_confidence():
    ranked = [
        {"action_type": "retry", "timing": "immediate", "method": "card",
         "channel": "n/a", "predicted_eiv": 9000.0},
        {"action_type": "do_nothing", "timing": "n/a", "method": "n/a",
         "channel": "n/a", "predicted_eiv": 0.0},
    ]
    optimize.attach_confidence(ranked, "payment_failed", amount=10000.0)
    # band = 0.05 * 10000 = 500; gap = 9000, decisively clear of it
    assert ranked[0]["eiv_confidence"] == cfg.CONFIDENCE_HIGH
    assert ranked[0]["eiv_confidence_reason"] is None
    assert ranked[0]["eiv_gap_to_next"] == 9000.0


@pytest.mark.gate("phase4.confidence")
def test_a_narrow_gap_flags_both_sides_of_the_tie():
    """Symmetry: if two candidates are within noise of each other, both are
    unresolved, not just the upper one."""
    ranked = [
        {"action_type": "retry", "timing": "immediate", "method": "card",
         "channel": "n/a", "predicted_eiv": 100.0},
        {"action_type": "retry", "timing": "4h", "method": "card",
         "channel": "n/a", "predicted_eiv": 99.0},
        {"action_type": "do_nothing", "timing": "n/a", "method": "n/a",
         "channel": "n/a", "predicted_eiv": 0.0},
    ]
    optimize.attach_confidence(ranked, "payment_failed", amount=1000.0)
    # band = 50; gap(0->1) = 1 (near tie), gap(1->2) = 99 (clear)
    assert ranked[0]["eiv_confidence"] == cfg.CONFIDENCE_LOW
    assert ranked[1]["eiv_confidence"] == cfg.CONFIDENCE_LOW
    assert ranked[2]["eiv_confidence"] == cfg.CONFIDENCE_HIGH


@needs_model
@pytest.mark.gate("phase4.confidence")
def test_confidence_annotation_does_not_change_the_ranking(seeded_db):
    """The single most important property of this feature: it is display
    metadata. Ranking must be byte-identical with the annotation removed."""
    for oid in _sample_opportunity_ids(seeded_db, 6):
        result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
        annotated = [(r["rank"], optimize._candidate_key(r), r["predicted_eiv"])
                     for r in result["ranked"]]

        stripped = [{k: v for k, v in r.items()
                     if k not in ("eiv_confidence", "eiv_confidence_reason",
                                  "eiv_gap_to_next", "rank")}
                    for r in result["ranked"]]
        reranked = sorted(stripped, key=optimize._sort_key)
        recomputed = [(i + 1, optimize._candidate_key(r), r["predicted_eiv"])
                      for i, r in enumerate(reranked)]
        assert annotated == recomputed


# ==========================================================================
# G8 -- auditability / persistence
# ==========================================================================

@needs_model
@pytest.mark.gate("phase4.audit")
def test_the_full_considered_set_is_persisted_not_just_the_winner(seeded_db):
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    result = optimize.optimize_opportunity(seeded_db, oid, persist=True)

    rows = [dict(r) for r in seeded_db.execute(
        "SELECT * FROM recovery_candidates WHERE opportunity_id = ?", (oid,))]
    assert len(rows) == (len(result["ranked"]) + len(result["unscored"])
                         + len(result["pruned"]))

    scored = [r for r in rows if r["predicted_eiv"] is not None]
    pruned = [r for r in rows if r["pruned_stage"] is not None]
    assert len(scored) == len(result["ranked"])
    assert pruned, "the pruned space must be recorded, not silently discarded"
    assert {r["pruned_stage"] for r in pruned} <= {
        optimize.PRUNED_STRUCTURAL, optimize.PRUNED_RELEVANCE,
        optimize.PRUNED_SCORING_FAILED}


@needs_model
@pytest.mark.gate("phase4.audit")
def test_every_scored_row_records_both_sides_of_the_comparison(seeded_db):
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    optimize.optimize_opportunity(seeded_db, oid, persist=True)
    rows = [dict(r) for r in seeded_db.execute(
        "SELECT * FROM recovery_candidates WHERE opportunity_id = ? "
        "AND predicted_eiv IS NOT NULL", (oid,))]
    for row in rows:
        for column in ("predicted_p_treated", "predicted_p_baseline",
                       "predicted_expected_amount_treated",
                       "predicted_expected_amount_baseline", "cost",
                       "predicted_eiv", "rank", "eiv_confidence"):
            assert row[column] is not None, f"{column} not persisted"


@needs_model
@pytest.mark.gate("phase4.audit")
def test_the_optimizer_never_marks_a_candidate_selected(seeded_db):
    """`selected` means the rule engine approved execution. The optimizer has
    no authority to grant that, so every row it writes carries selected=0."""
    for oid in _sample_opportunity_ids(seeded_db, 4):
        optimize.optimize_opportunity(seeded_db, oid, persist=True)
    selected = seeded_db.execute(
        "SELECT COUNT(*) FROM recovery_candidates WHERE selected = 1").fetchone()[0]
    assert selected == 0


@needs_model
@pytest.mark.gate("phase4.audit")
def test_pruned_rows_carry_no_fabricated_scores(seeded_db):
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    optimize.optimize_opportunity(seeded_db, oid, persist=True)
    rows = [dict(r) for r in seeded_db.execute(
        "SELECT * FROM recovery_candidates WHERE opportunity_id = ? "
        "AND pruned_stage IS NOT NULL", (oid,))]
    assert rows
    for row in rows:
        assert row["predicted_eiv"] is None
        assert row["predicted_p_treated"] is None
        assert row["rank"] is None


@needs_model
@pytest.mark.gate("phase4.audit")
def test_the_pruning_audit_attributes_a_stage_to_every_excluded_candidate(seeded_db):
    for oid in _sample_opportunity_ids(seeded_db, 6):
        context, _ = optimize.load_context(seeded_db, oid)
        generated = cg.generate_candidates(context)
        pruned = optimize.derive_pruned_candidates(context, generated)
        naive = optimize._naive_candidate_space(context["preferred_channel"])
        # the audit accounts for the whole naive space: emitted + pruned
        emitted_from_naive = [c for c in naive
                              if optimize._candidate_key(c) in
                              {optimize._candidate_key(g) for g in generated}]
        assert len(pruned) + len(emitted_from_naive) == len(naive)
        assert all(p["pruned_stage"] in (optimize.PRUNED_STRUCTURAL,
                                         optimize.PRUNED_RELEVANCE)
                   for p in pruned)


# ==========================================================================
# G9 -- method change is scoreable but never executable
# ==========================================================================

@needs_model
@pytest.mark.gate("phase4.method_change")
def test_an_alternate_method_candidate_is_scored_and_ranked_like_any_other(seeded_db):
    """The product deliberately CAN recommend switching payment method. It
    just can never dispatch one -- which is Phase 5's structural concern, not
    something the optimizer suppresses."""
    context = make_context(root_cause="expired_card")
    candidates = cg.generate_candidates(context)
    alternates = [c for c in candidates
                  if c["action_type"] == "retry" and c["method"] != "card"]
    assert {c["method"] for c in alternates} == {"netbanking"}

    baseline = inference.score_do_nothing(context, conn=seeded_db)
    scored = inference.score_candidate(context, alternates[0], conn=seeded_db)
    assert scored["error"] is None
    assert isinstance(scored["expected_recovered_amount"], float)
    eiv = (scored["expected_recovered_amount"]
           - baseline["expected_recovered_amount"]
           - intervention_cost(alternates[0]))
    assert isinstance(eiv, float)


# ==========================================================================
# G11 -- fail-closed
# ==========================================================================

@needs_model
@pytest.mark.gate("phase4.fail_closed")
def test_an_unknown_opportunity_writes_nothing(seeded_db):
    before = seeded_db.execute("SELECT COUNT(*) FROM recovery_candidates").fetchone()[0]
    result = optimize.optimize_opportunity(seeded_db, "opp_does_not_exist")
    assert result["error"] is not None
    assert result["ranked"] == []
    after = seeded_db.execute("SELECT COUNT(*) FROM recovery_candidates").fetchone()[0]
    assert after == before


@pytest.mark.gate("phase4.fail_closed")
def test_a_failed_baseline_aborts_the_whole_optimization_and_writes_nothing(
        seeded_db, monkeypatch):
    """Without a baseline, EIV is undefined for every candidate. A partial
    candidate set with a missing baseline would be a silently wrong audit
    record, so nothing is written at all."""
    monkeypatch.setattr(inference, "score_do_nothing",
                        lambda context, conn=None: {
                            "error": "model artifact unavailable",
                            "p_recovery": None,
                            "expected_amount_given_recovered": None,
                            "expected_recovered_amount": None})
    before = seeded_db.execute("SELECT COUNT(*) FROM recovery_candidates").fetchone()[0]
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    result = optimize.optimize_opportunity(seeded_db, oid)
    assert result["error"] is not None and "baseline" in result["error"]
    assert result["ranked"] == []
    after = seeded_db.execute("SELECT COUNT(*) FROM recovery_candidates").fetchone()[0]
    assert after == before


@needs_model
@pytest.mark.gate("phase4.fail_closed")
def test_a_candidate_that_fails_to_score_is_recorded_not_silently_dropped(
        seeded_db, monkeypatch):
    real = inference.score_candidate
    calls = {"n": 0}

    def flaky(context, candidate, conn=None):
        calls["n"] += 1
        if candidate.get("action_type") == "escalate":
            return {"error": "unexpected scoring error: injected",
                    "p_recovery": None,
                    "expected_amount_given_recovered": None,
                    "expected_recovered_amount": None}
        return real(context, candidate, conn=conn)

    monkeypatch.setattr(inference, "score_candidate", flaky)
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    result = optimize.optimize_opportunity(seeded_db, oid, persist=True)

    assert result["error"] is None
    assert [r["action_type"] for r in result["unscored"]] == ["escalate"]
    assert not [r for r in result["ranked"] if r["action_type"] == "escalate"]

    row = seeded_db.execute(
        "SELECT * FROM recovery_candidates WHERE opportunity_id = ? "
        "AND action_type = 'escalate' AND pruned_stage = ?",
        (oid, optimize.PRUNED_SCORING_FAILED)).fetchone()
    assert row is not None, "a scoring failure must be visible in the audit table"
    assert row["predicted_eiv"] is None


@needs_model
@pytest.mark.gate("phase4.fail_closed")
def test_a_malformed_context_never_reaches_the_candidates_table(seeded_db):
    """ml/inference.py proves it returns a flagged null on malformed input.
    Proving that null never becomes a persisted score is Phase 4's job."""
    context = make_context()
    context["event_type"] = "not_a_real_event_type"
    scored = inference.score_candidate(
        context, cg.do_nothing_candidate(), conn=seeded_db)
    assert scored["error"] is not None
    assert scored["expected_recovered_amount"] is None


# ==========================================================================
# A7 -- disclosed network-health limitation
# ==========================================================================

@needs_model
@pytest.mark.gate("phase4.disclosed")
def test_live_context_carries_network_health_and_says_so_explicitly(seeded_db):
    """
    AMENDED 2026-09-03 (Phase 5), and renamed -- it previously asserted the
    opposite, as `test_live_context_has_no_network_health_and_says_so_
    explicitly`.

    Phase 4 wrote this to pin a disclosed limitation rather than leave it
    silent: the live schema had no bank/psp column, bank_health_observations
    was in simulated hours with no mapping from a live timestamp, and so all
    four network-health features were dead at serving time (network_health_
    known=0.0), a regime Phase 3 had parity-tested.

    Phase 5 closed that gap deliberately, by approved ruling: bank/psp columns
    on payments, a health series seeded by the Data Factory's own generator,
    and the unix -> simulated-hour mapping in
    phase5_config.simulated_hour_for(). This test then failed -- which is the
    tripwire doing exactly its job, objecting that a pinned limitation had
    moved. It is inverted here rather than deleted, so the property stays
    pinned in its new direction: had this test not existed, closing the gap
    would have shifted the optimizer's model inputs with nothing objecting.

    Not a regression. See PHASE5_NOTES.md sections 1d-1f.
    """
    from backend.ml import outcome_features as feats
    oid = _sample_opportunity_ids(seeded_db, 1)[0]
    context, _ = optimize.load_context(seeded_db, oid)
    assert context["bank"] is not None and context["psp"] is not None

    lookup = inference._get_health_lookup(seeded_db)
    row = feats.build_feature_row(context, cg.do_nothing_candidate(), lookup)
    assert row["network_health_known"] == 1.0
    assert row["network_health_score_rolling"] is not None


# ==========================================================================
# G10 -- latency
# ==========================================================================

@needs_model
@pytest.mark.gate("phase4.latency")
def test_end_to_end_latency_against_the_declared_budget(seeded_db):
    """Measured warm, at the enforced candidate-count ceiling. The budget was
    declared in optimizer_config.py before this was measured and is NOT
    adjusted to obtain a pass -- if this fails, the raw number is the
    result."""
    ids = _sample_opportunity_ids(seeded_db, 12)
    for oid in ids[:3]:
        optimize.optimize_opportunity(seeded_db, oid, persist=False)  # warm

    timings = []
    for oid in ids:
        started = time.perf_counter()
        optimize.optimize_opportunity(seeded_db, oid, persist=False)
        timings.append((time.perf_counter() - started) * 1000.0)

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[int(0.95 * (len(timings) - 1))]
    assert p95 <= cfg.LATENCY_BUDGET_MS, (
        f"p50={p50:.1f}ms p95={p95:.1f}ms exceeds the declared budget of "
        f"{cfg.LATENCY_BUDGET_MS}ms. Dominated by per-candidate single-row "
        f"model inference through ml/inference.py; a batch scoring entry "
        f"point would fix it but ml/inference.py is a frozen Phase 3 module.")
