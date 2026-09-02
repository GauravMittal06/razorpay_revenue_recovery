"""
Phase 5 declared constants for the rule engine and bounded executor.

Every value here is a **declared engineering bound or a recorded ruling**, not
a measured property. Each one records how it was derived and, where it came
from a ruling, the date it was fixed. The project's standing rule is that any
threshold or tolerance must be committed before the evaluation it is checked
against runs -- so this module is written and committed at W2, before W3
touches `decide_action()` and before any Phase 5 gate is evaluated.

Nothing in this file may be loosened to obtain a pass. A bound may only be
amended for a diagnosed reason independent of the failing result, and the
amendment must be disclosed as such.

Deliberately separate from `optimizer_config.py`, which holds Phase 4's
declared bounds and is a frozen input to Phase 5. Phase 5 adds no numbers to
that file and edits none of its values. Where Phase 5 needs a Phase 4 bound it
imports it (see LATENCY_BUDGET_MS below) rather than restating it, so the two
can never drift apart.

All rulings recorded here were fixed on 2026-09-02.
"""

from backend.engine import optimizer_config as _phase4


# --------------------------------------------------------------------------
# Feature flag -- the optimizer-driven pathway
# --------------------------------------------------------------------------
# EXECUTION_PLAN Phase 5: "When no candidate list is supplied, behavior is
# unchanged from the existing hardcoded logic -- this preserves full backward
# compatibility and allows the new pathway to be feature-flagged on or off
# without a code revert."
#
# Default OFF. The backward-compatible path is the one that must hold when
# nothing has been deliberately switched on, so that a partially-configured
# deployment degrades to the proven behaviour rather than the new one.
#
# The acceptance gate additionally requires the disable path be "exercised by
# an actual test that flips the flag mid-run, not just documented as
# possible", so this is a module-level value read at call time -- never
# captured into a default argument or a module-import-time constant in the
# caller, both of which would make a mid-run flip silently ineffective.
OPTIMIZER_ENABLED_DEFAULT = False

# Per-entry-point enablement.
#
# Derived from the Phase 4 latency finding, not from preference. Optimizing
# one opportunity costs ~640ms (p50) / ~725-750ms (p95) against a declared
# 250ms budget, 99.7% of it single-row model inference through the frozen
# ml/inference.py. Two of the three entry points are request-synchronous
# behind api/server.py, so wiring the optimizer into them would put a
# ~0.75s model call on a user-facing request path while that budget is
# knowingly breached.
#
# `batch` (core_loop) and `dispatch` (the Phase 5 sweep) are asynchronous and
# can absorb it. The other two are wired but disabled: enabling them is a
# config change, not a code change, so the latency work can land later
# without touching the pipeline.
#
# Note this does NOT create a second pipeline. All three entry points call the
# same shared function; only this value differs per caller, which is what lets
# the "shared pipeline" gate and the latency constraint both hold.
OPTIMIZER_ENABLED_BY_ENTRY_POINT = {
    "batch": OPTIMIZER_ENABLED_DEFAULT,
    "dispatch": OPTIMIZER_ENABLED_DEFAULT,
    "trigger_event": False,      # request-synchronous
    "customer_reply": False,     # request-synchronous
}

ENTRY_POINTS = tuple(OPTIMIZER_ENABLED_BY_ENTRY_POINT)


# --------------------------------------------------------------------------
# Latency -- imported, never redeclared
# --------------------------------------------------------------------------
# Phase 4 declared 250ms and did not meet it (p95 ~858ms there; 747.9 / 737.5
# / 724.3ms across three Phase 5 baseline runs). Phase 5 deliberately does NOT
# declare its own, more generous budget: inventing a number that the current
# implementation happens to satisfy is exactly the loosening the standing
# rules forbid. The Phase 4 bound is imported so there is one budget in the
# system, still unmet, still reported as unmet.
LATENCY_BUDGET_MS = _phase4.LATENCY_BUDGET_MS


# --------------------------------------------------------------------------
# Executable action vocabulary
# --------------------------------------------------------------------------
# Ruling of 2026-09-02, confirmed against EXECUTION_PLAN.md:206, which names
# the executable set verbatim: "retry, reminder (with a channel attribute),
# payment link, escalate, stop".
#
# `payment_link` is new to the executor in Phase 5. It has been a first-class
# optimizer candidate since Phase 4 with its own cost term
# (intervention_cost.py) and eligibility rules, but had no executor support,
# so the optimizer's top pick could be structurally undispatchable. Adding it
# requires amending the permanent gate
# test_executor_action_set_matches_the_decider, which pinned the pre-Phase-5
# four-element set; that amendment is dated and evidence-backed rather than a
# silent widening.
#
# `do_nothing` is deliberately NOT here. It is a ranked optimizer candidate
# but not an executable action -- selecting it means the rule engine decided
# to act by not acting, which produces a decision row and no execution row.
#
# There is no method-change member, and there is no action type that carries a
# payment method. See EXECUTABLE_METHOD_POLICY below.
EXECUTABLE_ACTIONS = ("retry", "reminder", "payment_link", "escalate", "stop")

# Candidate action types the optimizer may rank but the executor may never
# dispatch. Kept explicit so the asymmetry is a declared property with a
# recorded reason, rather than an accident of two lists drifting.
EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS = ("do_nothing",)


# --------------------------------------------------------------------------
# The method-change boundary
# --------------------------------------------------------------------------
# EXECUTION_PLAN.md:206 and the permanent invariant at :301 both require that
# no code path anywhere in the executor can dispatch an autonomous
# payment-method change.
#
# There is no `method_change` action type to exclude. A method change is
# `action_type="retry"` carrying a `method` different from the opportunity's
# current one (data_factory/candidate_generation.py:154 flags it as
# `method_changed`). So the boundary is a property of the (action, method)
# pair, not of an action token -- which is why the pre-existing gate test that
# substring-searches source for "method_change" cannot prove it.
#
# True means: a candidate whose method differs from the opportunity's current
# method is never executable, regardless of rank, and the rule engine falls
# through to the next executable-and-compliant candidate. This is a permanent
# structural boundary, not a tunable -- it is declared here so a test can
# assert on it, and it is never read as a condition that could be switched
# off.
METHOD_CHANGE_IS_EXECUTABLE = False

# Where a candidate is rejected as non-executable and no compliant executable
# candidate remains. NOT `stop`.
#
# `stop` currently has exactly one producer -- the max-retries branch -- so
# `action_type='stop' AND outcome='executed'` is today an unambiguous query
# for "terminated by the retry ceiling". Routing exhaustion to `stop` would
# give it a second meaning and silently corrupt that query. `flagged_manual_
# review` is already in the closed compliance vocabulary and already means
# "the engine declined to auto-act; a human decides". See PHASE5_NOTES.md
# section 1.3.
EXHAUSTION_OUTCOME = "flagged_manual_review"


# --------------------------------------------------------------------------
# Fallthrough ceiling
# --------------------------------------------------------------------------
# The rule engine walks the optimizer's ranked list in the order given and
# never re-sorts it -- ranking authority belongs to the optimizer exclusively
# (EXECUTION_PLAN.md:83). This ceiling bounds how far it may walk.
#
# Set equal to Phase 4's declared candidate ceiling rather than to a fresh
# number, because the ranked list cannot exceed what the optimizer is allowed
# to produce; a list longer than this means the optimizer's own bound was
# breached, which should surface here as a hard failure rather than be
# silently truncated. Enforced by an explicit raise, not `assert` -- `assert`
# is stripped under `python -O`, and a bound that disappears under
# optimisation is not a bound (STATE_AND_DECISIONS.md:409).
MAX_FALLTHROUGH_CANDIDATES = _phase4.MAX_CANDIDATES


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------
# Ruling of 2026-09-02: the optimizer's existing `timing` dimension drives the
# execution lifecycle. A candidate's `timing_hours`
# (data_factory/candidate_generation.py:33, {immediate: 0, 4h: 4, 24h: 24,
# 3d: 72}) becomes `recovery_executions.scheduled_for = now + timing_hours *
# 3600`. Recorded as a ruling because no document states it -- it was inferred
# from the fact that `timing` is already a scored candidate attribute and
# `scheduled_for` already exists on the table.
SECONDS_PER_HOUR = 3600

# `immediate` (timing_hours == 0) dispatches inline rather than being written
# as a scheduled row the sweep must pick up on a later pass. Anything greater
# is scheduled.
IMMEDIATE_TIMING_HOURS = 0.0

# Longest horizon the executor will accept, derived from the largest value in
# the shared generator's TIMING_HOURS ("3d"). Not padded: unlike the candidate
# ceiling, where headroom lets a future rule change surface as a deliberate
# config edit, a scheduling horizon beyond the generator's own maximum can
# only arise from a bug, so an exact bound is the useful one.
MAX_SCHEDULE_HORIZON_HOURS = 72.0

# An execution is due when scheduled_for <= now. Zero grace: a grace window
# would mean the dispatcher fires actions before their scheduled time, which
# for a contact-hours-constrained action could place a message outside the
# permitted window.
DISPATCH_DUE_GRACE_SECONDS = 0

# Re-validate compliance at dispatch time by calling back into
# decide_action(), never by re-implementing the checks in the dispatcher.
#
# Necessary because the contact-window check reads the local hour of the
# opportunity's created_at, so an action scheduled 3 days out would otherwise
# inherit the original hour and could fire outside 9am-8pm. Implemented as a
# callback specifically to avoid creating a second compliance authority --
# the dispatcher decides *when* an approved action fires, never *whether* it
# may (EXECUTION_PLAN.md:83).
DISPATCH_REVALIDATES_VIA_DECIDE_ACTION = True


# --------------------------------------------------------------------------
# Tolerances for Phase 5's own gates
# --------------------------------------------------------------------------
# Both are determinism properties, not statistical ones -- there is no
# distribution to set a confidence level against, so the only defensible
# tolerance is zero. Stated as values so the gate tests read them from here
# rather than hardcoding, and so loosening one would be a visible diff to this
# file.

# Backward compatibility: every field of every decision dict, including which
# keys are present, must match the W1 golden corpus exactly.
REGRESSION_FIELD_TOLERANCE = 0

# Idempotent dispatch: running the sweep twice over the same due execution
# produces exactly one execution row and one customer-visible action.
DISPATCH_IDEMPOTENCY_EXPECTED_ROWS = 1


# --------------------------------------------------------------------------
# Self-consistency checks
# --------------------------------------------------------------------------
# Explicit raises rather than asserts, for the `python -O` reason above. These
# run at import so a contradictory edit to this file fails immediately and
# loudly, instead of at whatever later point the inconsistency happens to
# matter.

def _check() -> None:
    overlap = set(EXECUTABLE_ACTIONS) & set(EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS)
    if overlap:
        raise ValueError(
            f"an action cannot be both executable and not: {sorted(overlap)}")

    if METHOD_CHANGE_IS_EXECUTABLE:
        raise ValueError(
            "METHOD_CHANGE_IS_EXECUTABLE must remain False. It is a permanent "
            "structural boundary (EXECUTION_PLAN.md:301), not a tunable.")

    if EXHAUSTION_OUTCOME == "stop":
        raise ValueError(
            "routing candidate exhaustion to `stop` would give that action a "
            "second meaning; see PHASE5_NOTES.md section 1.3")

    from backend.db.db import DECISION_OUTCOMES
    if EXHAUSTION_OUTCOME not in DECISION_OUTCOMES:
        raise ValueError(
            f"EXHAUSTION_OUTCOME {EXHAUSTION_OUTCOME!r} is not in the closed "
            "compliance vocabulary")

    if MAX_SCHEDULE_HORIZON_HOURS <= IMMEDIATE_TIMING_HOURS:
        raise ValueError("scheduling horizon must exceed the immediate timing")

    if DISPATCH_DUE_GRACE_SECONDS != 0:
        raise ValueError(
            "a non-zero dispatch grace window can fire a contact action before "
            "its scheduled time, outside the permitted contact window")

    if set(OPTIMIZER_ENABLED_BY_ENTRY_POINT) != set(ENTRY_POINTS):
        raise ValueError("entry-point table and ENTRY_POINTS disagree")


_check()
