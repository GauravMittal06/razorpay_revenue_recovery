"""
Phase 4 -- the optimizer.

Generates a bounded, relevant candidate set for a real opportunity and ranks
it by Expected Incremental Value. It PROPOSES. It has no authority to act.

--------------------------------------------------------------------------
Authority boundary (permanent, do not weaken)
--------------------------------------------------------------------------
  - This module imports NOTHING with execution authority: not
    engine.decide_action, not engine.execute_action, not api.actions, not
    mark_opportunity_recovered. It is checked mechanically by
    tests/test_permanent_gates.py, not by code-review convention.
  - Its ONLY write is `INSERT INTO recovery_candidates`, an audit/proposal
    table. It never writes recovery_decisions, recovery_executions,
    opportunities, payments or experiment_assignment.
  - It never sets the `allowed` permission bit. Only the rule engine does.
  - It never marks a candidate `selected`. "Selected" means the rule engine
    approved it for execution; every row this module writes carries
    selected=0. Phase 5 sets it after decide_action() adjudicates.
  - Eligibility here is a RELEVANCE question ("is this candidate worth
    scoring at all"), never a COMPLIANCE question ("is this permitted to
    fire right now"). The rule engine re-checks everything independently
    and remains the only component that can block or approve. That overlap
    is deliberate and is what prevents authority drift.

--------------------------------------------------------------------------
Reuse discipline
--------------------------------------------------------------------------
  - Candidate generation is data_factory/candidate_generation.py, imported
    UNMODIFIED. This module writes zero eligibility rules of its own. That
    module is why the offline training candidate set and the live candidate
    set cannot silently diverge.
  - Scoring is ml/inference.py, imported UNMODIFIED. This module never
    loads a model artifact, never builds a feature row, never calls
    predict_proba. There is exactly one scoring path in the system.

--------------------------------------------------------------------------
Expected Incremental Value
--------------------------------------------------------------------------
    EIV = expected_recovered_amount(candidate)
        - expected_recovered_amount(do_nothing)
        - intervention_cost(candidate)

Two model evaluations, subtracted, net of cost. EIV is derived arithmetic,
never a direct model output, and never a trained target.

The do_nothing evaluation depends only on the context, which is identical
for every candidate of one opportunity, so it is evaluated ONCE per
opportunity and that identical value is subtracted from every candidate.
This is arithmetically identical to re-evaluating it per candidate; the
test suite asserts the cached and uncached forms agree to exactly 0.0.

do_nothing is itself scored as an ordinary candidate. Its EIV is therefore
x - x - 0 = exactly 0.0. That zero falls out of the same arithmetic every
other candidate goes through -- it is not special-cased, not hardcoded, and
not a fallback.

--------------------------------------------------------------------------
Disclosed limitation (Phase 6/7 closure item)
--------------------------------------------------------------------------
The live schema has no `bank` and no `psp` column on payments, and
bank_health_observations stores its windows in SIMULATED hours with no
defined mapping from a live unix timestamp. So the four network-health
features are unavailable at serving time: this module passes bank=None,
psp=None, decision_time_hours=0.0 and every live scoring lands in the
network_health_known=0 regime. That is a path Phase 3 explicitly
parity-tested, so it is safe, but it is a real capability gap and the test
suite asserts it holds rather than letting it pass silently. See
PHASE4_NOTES.md.
"""

import time

from backend.data_factory import candidate_generation as cg
from backend.engine import optimizer_config as cfg
from backend.engine.intervention_cost import intervention_cost
from backend.ml import inference

# The trailing window for prior-contact counting. 168h / 7 days -- the same
# constant the Data Factory used as fatigue_window_hours when it generated
# the training rows (calibration_profiles.py), so the live feature carries
# the same meaning the model was trained on rather than a second convention.
FATIGUE_WINDOW_HOURS = 168.0
FATIGUE_WINDOW_SECONDS = int(FATIGUE_WINDOW_HOURS * 3600)

# Which logged decisions count as a customer contact for the prior-contact
# feature. Mirrors the Data Factory's CONTACT_ACTION_LABELS, which is what
# last_action_type was drawn from at training time.
CONTACT_ACTION_LABELS = ("retry", "reminder", "escalate", "payment_link")

PRUNED_STRUCTURAL = "structural_eligibility"
PRUNED_RELEVANCE = "relevance_filter"
PRUNED_SCORING_FAILED = "scoring_failed"

# Defaults for customer attributes, matching the values decide_action.py
# already uses for the same fields, so the two components do not disagree
# about what an absent customer record means.
DEFAULT_HISTORY_SCORE = 0.5
DEFAULT_RECOVERY_RATE = 0.5
DEFAULT_PREFERRED_CHANNEL = "email"


class CandidateCeilingExceeded(RuntimeError):
    """The generated candidate set exceeded the declared ceiling.

    Raised, not `assert`ed, deliberately: `assert` is stripped under
    `python -O`, and a bound that silently disappears in an optimised
    interpreter is not a bound. This is the enforcement the Phase 4
    acceptance gate requires be mechanical rather than merely measured.
    """


# --------------------------------------------------------------------------
# Read side. Every query here is SELECT-only.
# --------------------------------------------------------------------------

def _read_opportunity(conn, opportunity_id):
    row = conn.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()
    return dict(row) if row else None


def _read_latest_payment(conn, opportunity_id):
    row = conn.execute(
        "SELECT * FROM payments WHERE opportunity_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (opportunity_id,)
    ).fetchone()
    return dict(row) if row else None


def _read_customer(conn, customer_id):
    if not customer_id:
        return {}
    row = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    return dict(row) if row else {}


def _read_merchant(conn, merchant_id):
    if not merchant_id:
        return {}
    row = conn.execute(
        "SELECT * FROM merchants WHERE merchant_id = ?", (merchant_id,)
    ).fetchone()
    return dict(row) if row else {}


def _read_decision_history(conn, opportunity_id):
    rows = conn.execute(
        "SELECT * FROM recovery_decisions WHERE opportunity_id = ? "
        "ORDER BY timestamp ASC", (opportunity_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------

def build_optimizer_context(opportunity, latest_payment, customer, merchant,
                            history, now):
    """
    One dict serving BOTH consumers: candidate_generation.generate_candidates()
    and ml/outcome_features.build_feature_row() (via ml/inference.py). Both
    ignore keys they do not need, so a single superset dict guarantees the
    candidate set and the feature row are derived from exactly the same view
    of the opportunity -- they cannot drift apart.

    `history` is the recovery_decisions log, read-only. It is used for
    feature derivation (how recently was this customer contacted, how often)
    -- never to decide whether an action is permitted, which is the rule
    engine's exclusive concern.
    """
    executed = [h for h in history if h.get("outcome") == "executed"]
    executed_contacts = [h for h in executed
                         if h.get("action_type") in CONTACT_ACTION_LABELS]

    last = executed[-1] if executed else None
    if last is None:
        last_action_type, hours_since_last_action = "none", 0.0
    else:
        last_action_type = last.get("action_type") or "none"
        hours_since_last_action = max(
            0.0, (now - (last.get("timestamp") or now)) / 3600.0)

    prior_contacts_in_window = len([
        h for h in executed_contacts
        if (now - (h.get("timestamp") or 0)) <= FATIGUE_WINDOW_SECONDS
    ])

    # Actual retry attempts only -- distinct from the combined retry+reminder
    # contact count. This is the same distinction decide_action.py draws.
    retry_count = len([h for h in executed if h.get("action_type") == "retry"])

    already_escalated = any(h.get("action_type") == "escalate" for h in executed)
    already_stopped = any(h.get("action_type") == "stop" for h in executed)

    # The method of the most recent transactional attempt. Left as None when
    # the opportunity has no payment behind it (checkout_abandoned has none),
    # rather than fabricating a plausible default: an invented "card" would
    # be an input the model treats as real. None reaches the encoder as an
    # unseen category and is ignored, which is the honest representation of
    # "we do not know". Retry -- the only action whose candidates carry a
    # method -- is not structurally eligible for those event types anyway.
    current_method = (latest_payment or {}).get("method")

    created_at = opportunity.get("created_at") or now
    days_since_event = max(0.0, (now - created_at) / 86400.0)

    return {
        # --- consumed by candidate_generation.generate_candidates() ---
        "event_type": opportunity.get("event_type"),
        "root_cause": opportunity.get("root_cause"),
        "retry_count": retry_count,
        "current_method": current_method,
        "preferred_channel": customer.get("preferred_channel",
                                          DEFAULT_PREFERRED_CHANNEL),
        "already_escalated": already_escalated,
        "already_stopped": already_stopped,

        # --- consumed by outcome_features.build_feature_row() ---
        "amount": float(opportunity.get("amount_at_risk") or 0.0),
        "days_since_event": days_since_event,
        "days_overdue": float(opportunity.get("days_overdue") or 0.0),
        "payment_history_score": float(
            customer.get("payment_history_score") or DEFAULT_HISTORY_SCORE),
        "past_recovery_rate": float(
            customer.get("past_recovery_rate") or DEFAULT_RECOVERY_RATE),
        "merchant_cohort": merchant.get("cohort"),
        "last_action_type": last_action_type,
        "hours_since_last_action": hours_since_last_action,
        "prior_contacts_in_window": prior_contacts_in_window,

        # --- disclosed unavailable at serving time (see module docstring) ---
        "bank": None,
        "psp": None,
        "decision_time_hours": 0.0,
    }


def load_context(conn, opportunity_id, now=None):
    """Read every row the optimizer needs and fold it into one context.
    Returns (context, opportunity) or (None, None) if the opportunity does
    not exist."""
    now = int(time.time()) if now is None else int(now)
    opportunity = _read_opportunity(conn, opportunity_id)
    if opportunity is None:
        return None, None
    customer = _read_customer(conn, opportunity.get("customer_id"))
    merchant = _read_merchant(conn, opportunity.get("merchant_id"))
    latest_payment = _read_latest_payment(conn, opportunity_id)
    history = _read_decision_history(conn, opportunity_id)
    context = build_optimizer_context(
        opportunity, latest_payment, customer, merchant, history, now)
    return context, opportunity


# --------------------------------------------------------------------------
# Pruning audit
# --------------------------------------------------------------------------
# The shared candidate generator does not REJECT candidates; it simply never
# emits them, so it cannot report what it dropped without being modified --
# and it is frozen. To satisfy the auditability requirement without touching
# it, the excluded set is derived by diffing the generated set against the
# naive space built from that module's OWN exported constants, and each
# exclusion is attributed using that module's OWN public read-only helpers.
#
# No eligibility rule is restated here. Anything the helpers cannot explain
# falls to structural_eligibility by elimination, which is how action-level
# suppression (retry on a non-payment_failed event, payment_link on a
# checkout, a terminal opportunity) is attributed without this file needing
# to know those rules exist.

def _candidate_key(candidate):
    return (candidate.get("action_type"), candidate.get("timing"),
            candidate.get("method"), candidate.get("channel"))


def _naive_candidate_space(preferred_channel):
    """The unpruned space, in the same per-action SHAPE the generator emits
    (a retry carries a method and no channel; a reminder the reverse), but
    over the module's full constant ranges instead of its filtered ones."""
    space = []
    for timing in cg.TIMING_HOURS:
        for method in cg.METHODS:
            space.append({"action_type": "retry", "timing": timing,
                          "method": method, "channel": "n/a"})
    for action in ("reminder", "payment_link"):
        for timing in cg.TIMING_HOURS:
            for channel in cg.CHANNELS:
                space.append({"action_type": action, "timing": timing,
                              "method": "n/a", "channel": channel})
    space.append({"action_type": "escalate", "timing": "immediate",
                  "method": "n/a", "channel": preferred_channel})
    return space


def derive_pruned_candidates(context, generated):
    """Every candidate in the naive space that the shared generator did not
    emit, each tagged with the stage that explains it."""
    generated_keys = {_candidate_key(c) for c in generated}
    emitted_actions = {c.get("action_type") for c in generated}

    eligible_timings = cg.eligible_timings(
        context.get("event_type"), context.get("root_cause"))
    eligible_channels = cg.eligible_channels(context.get("preferred_channel"))
    eligible_methods = cg.eligible_retry_methods(
        context.get("current_method"), context.get("root_cause"))

    pruned = []
    for candidate in _naive_candidate_space(context.get("preferred_channel")):
        if _candidate_key(candidate) in generated_keys:
            continue

        action = candidate["action_type"]
        if action not in emitted_actions:
            # The action itself was never offered for this opportunity. The
            # rule that suppressed it lives in the frozen module; this file
            # deliberately does not restate it.
            stage = PRUNED_STRUCTURAL
        elif candidate["timing"] not in eligible_timings:
            stage = PRUNED_RELEVANCE
        elif action in ("reminder", "payment_link") and \
                candidate["channel"] not in eligible_channels:
            stage = PRUNED_RELEVANCE
        elif action == "retry" and candidate["method"] not in eligible_methods:
            stage = PRUNED_STRUCTURAL
        else:
            stage = PRUNED_STRUCTURAL

        entry = dict(candidate)
        entry["pruned_stage"] = stage
        pruned.append(entry)
    return pruned


# --------------------------------------------------------------------------
# Confidence attachment (carried-forward Phase 3 requirement)
# --------------------------------------------------------------------------
# Runs AFTER ranking is complete and consumes the ranking read-only. It
# writes display metadata. It does not reorder anything, does not create a
# second decision system, and does not touch rule-engine authority. The test
# suite asserts the ranking is byte-identical with this step removed.

def _is_flagged_combination(action_type, event_type):
    return (action_type, event_type) in cfg.PHASE3_LOW_CONFIDENCE_COMBINATIONS


def attach_confidence(ranked, event_type, amount):
    """Annotate each ranked row with its gap to the next-ranked candidate and
    a high/low confidence label.

    A candidate is a near-tie when it sits within the band of an ADJACENT
    candidate -- above or below. Symmetry matters: if two candidates are
    within noise of each other, both are unreliable, not just the upper one.
    """
    band = cfg.NEAR_TIE_BAND_FRACTION * float(amount or 0.0)

    gaps = []
    for i, row in enumerate(ranked):
        if i + 1 < len(ranked):
            gaps.append(row["predicted_eiv"] - ranked[i + 1]["predicted_eiv"])
        else:
            gaps.append(None)

    for i, row in enumerate(ranked):
        gap_next = gaps[i]
        gap_prev = gaps[i - 1] if i > 0 else None

        adjacent = [g for g in (gap_next, gap_prev) if g is not None]
        near_tie = bool(adjacent) and min(adjacent) < band

        reasons = []
        if near_tie:
            reasons.append(cfg.REASON_NEAR_TIE)
        if _is_flagged_combination(row["action_type"], event_type):
            reasons.append(cfg.REASON_FLAGGED_BUCKET)

        row["eiv_gap_to_next"] = gap_next
        # OR, not AND: either signal alone is enough to withhold the claim
        # that this ordering is resolved (ruling A2).
        row["eiv_confidence"] = (cfg.CONFIDENCE_LOW if reasons
                                 else cfg.CONFIDENCE_HIGH)
        row["eiv_confidence_reason"] = "+".join(reasons) if reasons else None
    return ranked


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def _sort_key(row):
    """Descending EIV, then a total order over the candidate tuple so that
    identical input always produces an identical ranking. Without the
    tiebreak, two economically identical candidates could swap places
    between runs and make a stable audit trail impossible."""
    return (-row["predicted_eiv"], str(row["action_type"]), str(row["timing"]),
            str(row["method"]), str(row["channel"]))


# --------------------------------------------------------------------------
# The optimizer
# --------------------------------------------------------------------------

def optimize_opportunity(conn, opportunity_id, now=None, persist=True):
    """
    Rank the candidate set for one opportunity by Expected Incremental Value.

    Returns:
      {"error": None|str, "opportunity_id": str,
       "ranked": [ {...candidate, predicted_*, cost, predicted_eiv, rank,
                    eiv_confidence, eiv_confidence_reason, eiv_gap_to_next} ],
       "unscored": [...], "pruned": [...],
       "candidate_count": int, "latency_ms": float}

    Fail-closed. If the do_nothing baseline cannot be scored, EIV is
    undefined for every candidate, so nothing is ranked and NOTHING is
    written -- a partial candidate set with a missing baseline would be a
    silently wrong audit record. If an individual candidate fails to score,
    that candidate is excluded from the ranking and persisted with a NULL
    EIV and pruned_stage='scoring_failed', so the failure is visible rather
    than looking like a candidate that was never considered.
    """
    started = time.perf_counter()
    now = int(time.time()) if now is None else int(now)

    context, opportunity = load_context(conn, opportunity_id, now=now)
    if context is None:
        return {"error": f"unknown opportunity_id: {opportunity_id!r}",
                "opportunity_id": opportunity_id, "ranked": [], "unscored": [],
                "pruned": [], "candidate_count": 0,
                "latency_ms": (time.perf_counter() - started) * 1000.0}

    candidates = cg.generate_candidates(context)

    if len(candidates) > cfg.MAX_CANDIDATES:
        raise CandidateCeilingExceeded(
            f"{len(candidates)} candidates generated for {opportunity_id!r}, "
            f"declared ceiling is {cfg.MAX_CANDIDATES}")

    # ONE baseline evaluation per opportunity. It depends only on the
    # context, which every candidate shares, so re-evaluating it per
    # candidate would recompute an identical number.
    baseline = inference.score_do_nothing(context, conn=conn)
    if baseline["error"] is not None:
        return {"error": f"baseline scoring failed: {baseline['error']}",
                "opportunity_id": opportunity_id, "ranked": [], "unscored": [],
                "pruned": [], "candidate_count": len(candidates),
                "latency_ms": (time.perf_counter() - started) * 1000.0}

    baseline_amount = baseline["expected_recovered_amount"]

    scored, unscored = [], []
    for candidate in candidates:
        # do_nothing is scored here like any other candidate, not aliased to
        # the baseline, so that its EIV of exactly zero is produced by the
        # same arithmetic every other candidate goes through.
        treated = inference.score_candidate(context, candidate, conn=conn)
        row = dict(candidate)
        if treated["error"] is not None:
            row.update({"pruned_stage": PRUNED_SCORING_FAILED,
                        "scoring_error": treated["error"],
                        "predicted_eiv": None})
            unscored.append(row)
            continue

        cost = intervention_cost(candidate)
        row.update({
            "predicted_p_treated": treated["p_recovery"],
            "predicted_p_baseline": baseline["p_recovery"],
            "predicted_expected_amount_treated": treated["expected_recovered_amount"],
            "predicted_expected_amount_baseline": baseline_amount,
            "cost": cost,
            "predicted_eiv": treated["expected_recovered_amount"] - baseline_amount - cost,
            "pruned_stage": None,
        })
        scored.append(row)

    ranked = sorted(scored, key=_sort_key)
    for position, row in enumerate(ranked, start=1):
        row["rank"] = position

    attach_confidence(ranked, context.get("event_type"), context.get("amount"))

    pruned = derive_pruned_candidates(context, candidates)

    result = {
        "error": None,
        "opportunity_id": opportunity_id,
        "ranked": ranked,
        "unscored": unscored,
        "pruned": pruned,
        "candidate_count": len(candidates),
        "latency_ms": None,
    }

    if persist:
        persist_candidates(conn, opportunity_id, ranked, unscored, pruned, now)

    result["latency_ms"] = (time.perf_counter() - started) * 1000.0
    return result


# --------------------------------------------------------------------------
# Write side -- recovery_candidates ONLY
# --------------------------------------------------------------------------

_INSERT = """
INSERT INTO recovery_candidates (
    opportunity_id, action_type, timing, method, channel,
    predicted_p_treated, predicted_p_baseline,
    predicted_expected_amount_treated, predicted_expected_amount_baseline,
    cost, predicted_eiv, "rank", pruned_stage, selected, created_at,
    eiv_confidence, eiv_confidence_reason, eiv_gap_to_next
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _insert_row(conn, opportunity_id, row, created_at):
    conn.execute(_INSERT, (
        opportunity_id,
        row.get("action_type"), row.get("timing"),
        row.get("method"), row.get("channel"),
        row.get("predicted_p_treated"), row.get("predicted_p_baseline"),
        row.get("predicted_expected_amount_treated"),
        row.get("predicted_expected_amount_baseline"),
        row.get("cost"), row.get("predicted_eiv"), row.get("rank"),
        row.get("pruned_stage"),
        # Always 0. "Selected" means the rule engine approved execution, and
        # this module has no authority to grant that. Phase 5 sets it.
        0,
        created_at,
        row.get("eiv_confidence"), row.get("eiv_confidence_reason"),
        row.get("eiv_gap_to_next"),
    ))


def persist_candidates(conn, opportunity_id, ranked, unscored, pruned, created_at):
    """Write the full considered set -- ranked survivors, candidates that
    failed to score, and the pruned space -- to recovery_candidates. This is
    the only write this module performs, and recovery_candidates is an
    audit/proposal table: nothing reads it as a trigger to act."""
    for row in ranked:
        _insert_row(conn, opportunity_id, row, created_at)
    for row in unscored:
        _insert_row(conn, opportunity_id, row, created_at)
    for row in pruned:
        _insert_row(conn, opportunity_id, row, created_at)
    conn.commit()
