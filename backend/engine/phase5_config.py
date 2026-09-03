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
# W7: the shared pipeline
# --------------------------------------------------------------------------
# Which entry points serialise their read-decide-write through
# engine/opportunity_lock.py. Ruling W2/W7, 2026-09-04.
#
# Declared as a table rather than a per-call boolean so no caller can opt out
# of the lock by accident, and so the asymmetry is a property a test can
# assert in BOTH directions rather than an accident of three call sites.
#
# `trigger_event` is deliberately ABSENT, and this is the asymmetry the W6
# hand-off requires be preserved rather than tidied away. It does not share
# the race: it mints a fresh opportunity_id per call, so concurrent calls
# touch different rows, and duplicate delivery of one upstream event is
# already guarded by the UNIQUE index on opportunities.ingestion_event_id
# plus the IntegrityError handler that resolves to the winner. Applying the
# lock uniformly "for consistency" would serialise unrelated event ingestion
# behind one write lock and buy nothing.
#
# `dispatch` is absent for a different reason: the sweep does not run this
# pipeline at all. It advances an already-decided action and must never call
# execute_action(), which is not idempotent at the call level. It holds its
# own two short locks around its compare-and-swap. See dispatch_scheduled.py.
ENTRY_POINTS_USING_OPPORTUNITY_LOCK = ("batch", "customer_reply")

# Entry points that route through run_recovery_pipeline(). The Phase 5 gate
# requires "a single shared function is called by all three entry points",
# verified structurally; this names the three.
ENTRY_POINTS_USING_SHARED_PIPELINE = ("batch", "trigger_event", "customer_reply")

# Which field feeds classify()'s `error_reason` parameter in the shared
# pipeline. Ruling W1, 2026-09-04.
#
# Before unification the three entry points disagreed: core_loop passed the
# latest payment's error_reason, handle_customer_reply passed the
# opportunity's stored root_cause, and trigger_event passed the root_cause it
# had just been handed. The divergence was introduced by the Phase 1 schema
# split (commit 7c9fc24) -- before it, BOTH loop entry points called the
# identical `classify(payment)`.
#
# Unified on the opportunity's stored root_cause, because
# classify()'s output is a COMPLIANCE INPUT on the reply path:
# decide_action.py's intent-mismatch gate compares
# classification["root_cause"] against the LLM's mentioned_reason and can
# return allowed=False / flagged_manual_review. Its own message says
# "Extracted intent conflicts with STORED root_cause", wording that predates
# the Phase 1 split (commit e690789). Feeding that gate a per-attempt field
# would make it compare a customer's claim against something other than the
# case's diagnosis, and make its own reasoning string false.
#
# The payment's error_reason is retained as a fallback only when root_cause
# is NULL, which preserves core_loop's behaviour for any opportunity whose
# diagnosis is not recorded on the opportunity itself.
#
# Measured behaviourally identical at the time of the ruling: across all 150
# seeded opportunities (64 payment_failed, 17 with multiple payment
# attempts), ZERO have a latest-payment error_reason differing from their
# root_cause. That is structural, not incidental -- the only two writers of a
# payments row (generate_seed_data.py:322 and trigger_event.py:198) both set
# error_reason from the opportunity's own root_cause.
CLASSIFY_ROOT_CAUSE_SOURCE = "opportunity.root_cause"
CLASSIFY_ROOT_CAUSE_FALLBACK = "latest_payment.error_reason"


# Ceiling on how long the unified pipeline may HOLD opportunity_lock.
# Locked 2026-09-04, before the measurement that checks it was run.
#
# Derivation, so the number is not mistaken for one chosen to fit a result:
# the recorded hold for decide_action + execute_action is p50 5.88 ms /
# p95 6.24 ms, and the regression this guards against is putting
# optimize_opportunity() (p50 644 ms warm) inside the hold -- a ~110x jump
# that takes the number of workers able to queue against
# db.BUSY_TIMEOUT_MS from ~850 to about 7.
#
# 50 ms sits ~8x above the recorded p95, which absorbs scheduling noise on a
# machine where the full suite already takes ~7 minutes, and ~13x below the
# ~650 ms failure it must catch. A 10 ms bar would flake without detecting
# anything this one misses. The raw p50/p95 are reported by the measurement
# regardless of the bar.
UNIFIED_LOCK_HOLD_P95_CEILING_MS = 50.0

# Fields excluded from the before/after parity comparison, declared BEFORE
# the comparison runs so the exclusion list cannot be widened after seeing a
# diff.
#
# The parity test pins time.time() to a fixed value and runs the legacy and
# unified paths in the SAME process against two freshly-created databases, so
# timestamps, autoincrement ids and ml_recovery_probability are all
# deterministic and ARE compared. The only genuinely non-reproducible values
# are the uuid4-derived identifiers trigger_event mints per call.
PARITY_VOLATILE_FIELDS = ("opportunity_id", "payment_id", "id")

# Parity tolerance. A determinism property, not a statistical one: with the
# clock pinned and the inputs identical there is no distribution to set a
# confidence level against, so the only defensible tolerance is zero.
PIPELINE_PARITY_FIELD_TOLERANCE = 0

# The runtime kill switch, distinct from the table above.
#
# Two different questions, deliberately separated:
#
#   OPTIMIZER_ENABLED_BY_ENTRY_POINT -- should this caller COMPUTE a ranked
#       list and pass it to decide_action() at all? A deployment-shaped
#       question, answered per entry point, currently False everywhere.
#
#   OPTIMIZER_PATHWAY_ENABLED -- if a ranked list IS supplied, may the rule
#       engine act on it? A safety question, answered once, at the authority
#       boundary itself.
#
# The acceptance gate requires the optimizer be disableable "via configuration
# at runtime" with decisions "immediately reverting to pre-optimizer
# behaviour". Putting that switch only in the pipeline would leave any direct
# caller of decide_action() unaffected by it, so it lives here and is honoured
# inside decide_action(): with it False, a supplied ranked list is ignored and
# the hardcoded path runs, exactly as if no list had been passed.
#
# Default True: the pathway exists and is trusted. This is the emergency
# disable, not the deployment default -- what keeps the optimizer off in
# normal operation is the entry-point table above, all False.
#
# MUST be read as a module attribute (`phase5_config.OPTIMIZER_PATHWAY_ENABLED`)
# and never `from ... import`ed, or a mid-run flip would bind to the old value
# and silently fail to take effect. Enforced by
# test_the_kill_switch_is_not_bound_at_import_time.
OPTIMIZER_PATHWAY_ENABLED = True


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

# Execution states that positively mean "this contact has NOT reached the
# customer". Ruling A1, 2026-09-03.
#
# THE DEFECT. execute_action() writes recovery_decisions(action_type=
# 'reminder', outcome='executed') at SCHEDULE time, before anything is sent.
# decide_action() built contact_history from exactly that predicate, so a
# scheduled-but-unfired action counted as contact already made. Measured: a
# 4h-scheduled reminder revalidated at its due time returned
#
#     Cooldown active. 20.0h remaining before next contact allowed.
#
# -- the scheduling decision blocking the very action it scheduled, making the
# 4h timing structurally undispatchable. Through the same counter, three
# unfired scheduled reminders returned "Max 3 contact attempts reached",
# exhausting the customer's whole contact budget before any contact happened.
#
# WHY THIS IS PHRASED AS AN EXCLUSION, NOT AN INCLUSION. The obvious fix --
# count only decisions whose execution reached 'executed' -- silently breaks
# every pre-Phase-5 row: the golden corpus inserts decision rows with no
# execution row at all, and all 25 scenarios would stop counting as contact,
# weakening cooldown across the board. So a decision counts as contact UNLESS
# its execution row exists and is in one of these states. Absence of evidence
# is treated as contact made, which is the safe direction for a compliance
# rule.
#
# 'failed' and 'dispatched' are deliberately NOT here. A dispatch that was
# attempted may have reached the customer; counting it costs at most one
# delayed follow-up, while not counting it risks a real double-contact.
CONTACT_NOT_YET_DELIVERED_STATES = ("pending", "scheduled", "cancelled",
                                    "superseded")

# Re-validate compliance at dispatch time by calling back into
# decide_action(), never by re-implementing the checks in the dispatcher.
#
# Necessary because the contact-window check reads the local hour of the
# opportunity's created_at, so an action scheduled 3 days out would otherwise
# inherit the original hour and could fire outside 9am-8pm. Implemented as a
# callback specifically to avoid creating a second compliance authority --
# the dispatcher decides *when* an approved action fires, never *whether* it
# may (EXECUTION_PLAN.md:83).
#
# CORRECTION, ruling A2, 2026-09-03. As originally written this flag did not
# achieve what the paragraph above claims. Both window implementations
# (_within_contact_window and the hardcoded branch) read the local hour of
# `created_at`, which does not change between schedule time and due time -- so
# revalidating returned the *identical* window verdict and the 9pm-8am contact
# ban was unenforceable for every scheduled action. Closing it required giving
# decide_action() an evaluation clock; see DISPATCH_EVALUATES_WINDOW_AT_DUE_
# TIME below. The flag's original intent stands; its stated mechanism was
# wrong, and is corrected here rather than quietly reinterpreted.
DISPATCH_REVALIDATES_VIA_DECIDE_ACTION = True

# The dispatcher passes its own clock to decide_action(as_of=...) so the
# contact window is evaluated at the moment the action would actually fire.
# Ruling A2, 2026-09-03.
#
# Without this, an action scheduled at noon for 3 days out revalidates against
# noon and fires at 3am. That is a real violation of SoT section 7's 9am-8pm
# rule, in the same severity tier as the two concurrency defects fixed earlier
# in this phase -- not a theoretical one, because W6 is the component that
# makes scheduled firing reachable at all.
#
# `as_of` defaults to None everywhere else, and with it None decide_action()
# reads `created_at` exactly as before. That default path is what the golden
# corpus pins, so the amendment cannot change any pre-Phase-5 verdict.
DISPATCH_EVALUATES_WINDOW_AT_DUE_TIME = True


# --------------------------------------------------------------------------
# Network health: the unix -> simulated-hour mapping
# --------------------------------------------------------------------------
# Ruling of 2026-09-03, "Option A". bank_health_observations stores its windows
# in simulated hours starting at 0; a live opportunity has a unix timestamp.
# There was no correspondence between the two, so every live scoring landed at
# network_health_known=0 and the four network-health features were dead.
#
#     sim_hour = WINDOW + ((now_unix - ORIGIN) / 3600) mod (HORIZON - WINDOW)
#
# Why modulo rather than a clamped linear map: the lookup is NOT honest past
# the end of the series. For as_of beyond the last window it clamps
# (outcome_features.py:262, `lo > hi` -> `lo = hi`) and returns the single
# final 4h observation with known=True, forever -- every opportunity reading an
# identical constant while the feature asserts the data is real. That is worse
# than known=0, because a constant claiming to be real is indistinguishable
# from data the model can learn from. A linear map reaches that state on a
# date, silently. Modulo structurally cannot: it always lands inside the
# series.
#
# The cost of modulo is a discontinuity at the wrap -- two scorings either side
# of it read opposite ends of the series -- and a false periodicity with the
# horizon's period. Both were accepted as bounded and visible, against a
# failure mode that is neither.
#
# The offset by WINDOW and the reduced span keep sim_hour inside
# [WINDOW, HORIZON), which is exactly the range where the lookup is truthful:
# below WINDOW no observation has closed yet (known=False), and at or past
# HORIZON the clamp above takes over.
NETWORK_HEALTH_ORIGIN_UNIX = 1767225600      # 2026-01-01T00:00:00Z, arbitrary but fixed

# Simulated-hour span the seeded series covers.
#
# It must stay materially larger than
# outcome_features.NETWORK_HEALTH_WINDOW_HOURS (168.0), the trailing average's
# span: at horizon == trailing span every query averages from window 0 and the
# rolling value degenerates into a prefix average. Measured rolling-score
# spread by horizon -- 168h: 0.0864, 720h: 0.1572, 2880h: 0.2119.
#
# Set to 2880 first, matching the Data Factory's DEFAULT_HORIZON_HOURS
# (24 * 120), and reduced to 720 on the measured cost that the ruling named as
# the fallback trigger:
#
#     seed generation, 2880h : 1285 ms   51,840 rows
#     seed generation,  720h :  347 ms   12,960 rows
#     full pytest suite, 168h:  204 s
#     full pytest suite, 2880h: >20 min, stopped before completing
#
# The seed set is regenerated once per test through the seed_data_dir fixture,
# so the per-generation cost multiplies by roughly the test count. 720h keeps
# the trailing/horizon ratio at 0.23 -- comfortably non-degenerate, 0.157
# rolling spread against 0.212 at 2880h -- for about a quarter of the cost.
HEALTH_HORIZON_HOURS = 720

# Must equal bank_health_timeseries.WINDOW_HOURS; asserted in _check().
HEALTH_WINDOW_HOURS = 4


def simulated_hour_for(now_unix) -> float:
    """
    Map a live unix timestamp onto the seeded series' simulated-hour axis.

    Always returns a value in [HEALTH_WINDOW_HOURS, HEALTH_HORIZON_HOURS), so
    a lookup against a fully-seeded series always resolves to real observations
    and never to the clamped final-window state.
    """
    span = HEALTH_HORIZON_HOURS - HEALTH_WINDOW_HOURS
    elapsed_hours = (float(now_unix) - NETWORK_HEALTH_ORIGIN_UNIX) / SECONDS_PER_HOUR
    return HEALTH_WINDOW_HOURS + (elapsed_hours % span)


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

    from backend.db.db import EXECUTION_STATES
    unknown = set(CONTACT_NOT_YET_DELIVERED_STATES) - set(EXECUTION_STATES)
    if unknown:
        raise ValueError(
            f"CONTACT_NOT_YET_DELIVERED_STATES names states outside the closed "
            f"execution vocabulary: {sorted(unknown)}")
    if "executed" in CONTACT_NOT_YET_DELIVERED_STATES:
        raise ValueError(
            "'executed' means the contact reached the customer; excluding it "
            "from contact history would disable cooldown entirely")

    if DISPATCH_DUE_GRACE_SECONDS != 0:
        raise ValueError(
            "a non-zero dispatch grace window can fire a contact action before "
            "its scheduled time, outside the permitted contact window")

    if set(OPTIMIZER_ENABLED_BY_ENTRY_POINT) != set(ENTRY_POINTS):
        raise ValueError("entry-point table and ENTRY_POINTS disagree")

    unknown = set(ENTRY_POINTS_USING_OPPORTUNITY_LOCK) - set(ENTRY_POINTS)
    if unknown:
        raise ValueError(
            f"ENTRY_POINTS_USING_OPPORTUNITY_LOCK names unknown entry points: "
            f"{sorted(unknown)}")

    unknown = set(ENTRY_POINTS_USING_SHARED_PIPELINE) - set(ENTRY_POINTS)
    if unknown:
        raise ValueError(
            f"ENTRY_POINTS_USING_SHARED_PIPELINE names unknown entry points: "
            f"{sorted(unknown)}")

    if "trigger_event" in ENTRY_POINTS_USING_OPPORTUNITY_LOCK:
        raise ValueError(
            "trigger_event must stay OUT of the lock table: it mints a fresh "
            "opportunity_id per call and is already guarded by the UNIQUE "
            "index on ingestion_event_id. The asymmetry is deliberate.")

    if "dispatch" in ENTRY_POINTS_USING_SHARED_PIPELINE:
        raise ValueError(
            "the dispatcher must not route through run_recovery_pipeline(): "
            "it advances an already-decided action and must never call "
            "execute_action(), which is not idempotent at the call level")

    if UNIFIED_LOCK_HOLD_P95_CEILING_MS >= 500.0:
        raise ValueError(
            "the lock-hold ceiling must stay far below the ~650ms cost of "
            "optimize_opportunity(), or it cannot detect the regression it "
            "exists to catch")

    from backend.data_factory.bank_health_timeseries import WINDOW_HOURS
    if HEALTH_WINDOW_HOURS != WINDOW_HOURS:
        raise ValueError(
            f"HEALTH_WINDOW_HOURS ({HEALTH_WINDOW_HOURS}) disagrees with the "
            f"generator's WINDOW_HOURS ({WINDOW_HOURS}); the mapping's safe "
            "range would be wrong")

    from backend.ml.outcome_features import NETWORK_HEALTH_WINDOW_HOURS
    if HEALTH_HORIZON_HOURS <= NETWORK_HEALTH_WINDOW_HOURS:
        raise ValueError(
            f"HEALTH_HORIZON_HOURS ({HEALTH_HORIZON_HOURS}) must exceed the "
            f"trailing average span ({NETWORK_HEALTH_WINDOW_HOURS}), or every "
            "query averages from window 0 and the rolling value degenerates "
            "into a prefix average")


_check()
