"""
Phase 6 declared constants for live experiment assignment and outcome
observation.

Same discipline as `phase5_config.py`: every value here is a **declared bound
or a recorded ruling**, not a measured property, and this module is written
and committed at X0 -- before `assign_experiment_group.py` exists, before any
opportunity is assigned, and before the randomization-balance gate is ever
evaluated. A threshold committed after seeing the result it judges is not a
threshold.

Nothing here may be loosened to obtain a pass. A bound may only be amended for
a diagnosed reason independent of the failing result, and the amendment must
be disclosed as such.

All rulings recorded here were fixed on 2026-09-04.

Deliberately separate from `phase5_config.py`, which is a frozen input to
Phase 6. Phase 6 adds no numbers to that file and edits none of its values.
"""

import hashlib

from backend.db import db as _db


# --------------------------------------------------------------------------
# The assignment itself
# --------------------------------------------------------------------------
# Ruling, 2026-09-04: hash bucketing (option R2), not stratified/blocked
# randomization (R4).
#
# blake2b over the opportunity_id plus a locked salt, read as a uniform draw
# in [0, 1). Chosen over the alternatives for four properties Phase 7 depends
# on:
#
#   * It is genuinely random with respect to every covariate, and the argument
#     is structural rather than empirical: `opportunity_id` is
#     `"opp_" + uuid4().hex[:12]` (trigger_event.py), minted before any
#     covariate is read and independent of amount, root cause, customer,
#     merchant and time. Hashing an identifier that carries no signal cannot
#     induce a correlation with a covariate.
#   * It is reproducible. Any row's group can be recomputed from its id and
#     the committed salt below, years later, by anyone. `SystemRandom` would
#     be equally random and completely unauditable.
#   * It is stateless, so it is correct under concurrency. A seeded
#     `random.Random` sequence gives the same assignment only if the call
#     order is identical, which two workers cannot guarantee.
#   * It is idempotent. Re-deriving a group for an id already assigned yields
#     the same answer, so a retry can never re-randomize a live opportunity.
#
# R4 was declined as premature: stratification buys balance this design has no
# evidence of needing. If the X5 balance gate actually fails, escalating to R4
# is the documented response -- with the failure as its evidence.
ASSIGNMENT_METHOD = "hash_blake2b_v1"

# The salt. LOCKED -- changing it re-randomizes every future assignment and
# silently splits the experiment population into two incomparable regimes.
# It exists so the bucketing is not a bare hash of a public identifier.
ASSIGNMENT_SALT = "phase6-experiment-assignment-2026-09-04"

# Ruling, 2026-09-04: 0.5, not the 0.20 originally proposed.
#
# Chosen for statistical power, not demo realism. The usual reason to keep a
# holdout small -- every control opportunity is revenue deliberately left
# unrecovered -- does not apply to a synthetic system where no real money is
# at stake. Against the reconciled population (150 seeded opportunities, of
# which 51 are already terminal), an even split is the only allocation that
# gives the balance gate a usable control arm at achievable volume.
HOLDOUT_FRACTION = 0.5

# The closed group vocabulary. `experiment_assignment."group"` may hold
# nothing else.
GROUPS = ("control", "treatment")
CONTROL_GROUP = "control"
TREATMENT_GROUP = "treatment"


def assignment_bucket(opportunity_id: str) -> float:
    """
    The opportunity's uniform draw in [0, 1).

    The whole randomization, in one pure function of the id. Deterministic,
    stateless, and independently recomputable from the salt above -- which is
    what makes an assignment auditable after the fact rather than merely
    asserted.

    64 bits of the digest scaled by 2**64. The truncation is not a
    compromise: 2**64 buckets is ~19 orders of magnitude finer than any
    holdout fraction this system will ever declare.
    """
    digest = hashlib.blake2b(
        (ASSIGNMENT_SALT + ":" + opportunity_id).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def assigned_group(opportunity_id: str) -> str:
    """control when the draw falls below the holdout fraction, else treatment."""
    return (CONTROL_GROUP if assignment_bucket(opportunity_id) < HOLDOUT_FRACTION
            else TREATMENT_GROUP)


def assignment_method_record() -> str:
    """
    What lands in `experiment_assignment.assignment_method`.

    Self-describing on purpose: a row records the method, the salt and the
    fraction that produced it, so an assignment stays interpretable even if
    this file is later amended for a different population.
    """
    return (f"{ASSIGNMENT_METHOD}:salt={ASSIGNMENT_SALT}"
            f":holdout={HOLDOUT_FRACTION}")


# --------------------------------------------------------------------------
# Control suppression
# --------------------------------------------------------------------------
# Ruling, 2026-09-04: a suppressed control opportunity produces a
# recovery_decisions row carrying this outcome. It is a new member of the
# closed compliance vocabulary, added in db.DECISION_OUTCOMES at X0.
#
# The alternative -- writing no decision row at all -- was declined twice
# over. It violates the permanent invariant that every action the system
# takes OR DECLINES TO TAKE is logged with a reason, and it would leave the
# counterfactual-consistency gate with nothing affirmative to inspect:
# "control has no executed decisions" is much weaker evidence than "control
# has N decisions, all of them suppressions".
#
# Reusing `flagged_manual_review` was declined outright: it means "a human
# should decide", and routing holdout suppression into it would inject the
# entire control arm into the manual-review queue.
SUPPRESSION_OUTCOME = "suppressed_holdout"

# What an opportunity with no `experiment_assignment` row means.
#
# Ruling, 2026-09-04: it is NOT in the experiment, and suppression does not
# apply to it. This is a deliberate, dated exception to the project's
# fail-closed default, and it is recorded as an exception rather than left to
# look like an oversight.
#
# Failing closed here -- suppressing whenever a group is unknown -- would
# freeze all 150 pre-Phase-6 seeded opportunities, since none of them was ever
# randomized. That would not be conservative; it would silently disable the
# entire demonstration world in the name of an experiment those rows are not
# part of.
#
# The cost is bounded and is Phase 7's to carry, not this module's: an
# unassigned opportunity is excluded from every incremental number, because it
# belongs to neither arm.
UNASSIGNED_IS_SUPPRESSED = False


# --------------------------------------------------------------------------
# Outcome observation
# --------------------------------------------------------------------------
# The closed vocabulary for `opportunities.resolution_type`. Declared here for
# the first time -- Phase 1 shipped it as a schema comment, which no test
# could assert against.
#
# `lost` is new (ruling, 2026-09-04). It is NOT a synonym for `stopped`:
# `stopped` means the engine chose to stop trying and the case was closed by
# policy, while `lost` means the money is definitively gone as an observed
# fact about the world. Collapsing them would make "how much did we actually
# fail to recover" unanswerable, which is precisely the question Phase 7 asks.
RESOLUTION_TYPES = _db.RESOLUTION_TYPES

# Partial recovery has NO distinct resolution_type (ruling, 2026-09-04). It is
# INFERRED:
#
#     resolution_type = 'recovered' AND partial_recovery_amount < amount_at_risk
#
# Adding a `partially_recovered` member would force every "was this recovered"
# query in the system to match two values instead of one, and any query that
# forgot the second would silently under-count recoveries. The inference is
# exact, so the value adds no information.
PARTIAL_RECOVERY_IS_INFERRED = True

# The closed vocabulary for where an observed outcome came from, persisted on
# `opportunities.outcome_source` (additive column, ruling A7).
#
# Without this, "there is exactly one ingestion path" is true but unauditable:
# nothing in the data would say which caller drove a given outcome.
OUTCOME_SOURCES = _db.OUTCOME_SOURCES


# --------------------------------------------------------------------------
# The randomization-balance gate
# --------------------------------------------------------------------------
# HARD gate. Locked here in full -- formula, covariates, level lists,
# degenerate-case conventions and eligibility floor -- because a gate
# specified in prose is a gate that can be reinterpreted after seeing its
# result.
#
# All covariates are fixed at opportunity creation and never mutate, so
# "balance at assignment time" and "balance at creation time" are the same
# measurement. Stated so the gate cannot later drift into comparing post-hoc
# values, which would measure the intervention's effect rather than the
# quality of the randomization.

# Standardized mean difference bound, applied to every covariate below.
#
# 0.10 is the covariate-balance convention (SMD < 0.10 = negligible
# imbalance). It is NOT Cohen's 0.20, which is a small-EFFECT-SIZE convention
# for a different construct; an earlier draft of this plan conflated the two
# and was corrected on 2026-09-04 before anything was locked.
MAX_ABS_SMD = 0.10

# Below this many assigned opportunities the gate reports its raw numbers but
# returns NOT_EVALUABLE rather than PASS. Refusing to certify balance on a
# sample too small to detect imbalance is the point.
#
# AMENDED 2026-09-04 at X5: 200 -> 3500.
#
# The original 200 was set without a power analysis and was incompatible with
# MAX_ABS_SMD = 0.10, which was locked at the same time. SE(SMD) is roughly
# 2/sqrt(n), so at n=240 each level's SMD has SD ~= 0.13 while the gate takes
# the maximum over ten such quantities against a 0.10 bound.
#
# Measured under the true-random null -- the real locked hash, seeded and
# reproducible, analytics/balance_power_analysis.py:
#
#        n     passed    pass rate   95% lower   median max|SMD|
#      240    2 / 600        0.33%       0.09%            0.2335
#      500   49 / 600        8.17%       6.23%            0.1600
#     1000  208 / 600       34.67%      30.97%            0.1126
#     1500  362 / 600       60.33%      56.37%            0.0924
#     2000  465 / 600       77.50%      73.99%            0.0787
#     2500 1760 / 2000      88.00%      86.50%
#     3000 1875 / 2000      93.75%      92.60%
#     3500 1940 / 2000      97.00%      96.16%   <- smallest clearing 95%
#     4000 1975 / 2000      98.75%      98.16%
#     4500 1985 / 2000      99.25%      98.77%
#
# A correct randomizer fails this gate 99.67% of the time at n=240. Those
# failures carried no information about the randomizer whatsoever.
#
# This is NOT a loosening, and the distinction matters. MAX_ABS_SMD is
# untouched at 0.10 -- the bound that judges the result is exactly what it was
# before the result was seen. What changed is the precondition for the gate
# being evaluated at all, moved in the CONSERVATIVE direction: more evidence
# is now required before balance may be certified, not less. It also makes
# this constant finally do the job its own comment claims, since at n=240 the
# gate returned FAIL when the honest answer was "cannot tell".
#
# 3500 is the smallest n whose 95% Wilson LOWER bound clears a 95% pass rate
# (97.00% observed, 96.16% lower, 2000 trials). The lower bound rather than
# the point estimate because a Monte Carlo pass rate is itself an estimate:
# choosing on the point estimate would clear the criterion by sampling luck
# about half the time. n=3000 does not clear it (93.75% / 92.60%).
#
# The trial count is 2000 at the decision points for a reason. A first pass at
# 500 trials put n=3500 at 98.0% (96.4% lower) and a second at 96.0% (93.9%
# lower) -- straddling the criterion, so the chosen floor moved between runs.
# The cause was that `_draw_rows` minted ids with uuid.uuid4(), which reads
# os.urandom and ignores the seed, making the whole analysis unreproducible. A
# locked threshold justified by a measurement nobody can replay is not
# justified, so id generation is now seeded (same 48-bit shape and
# distribution) and the estimate is stable to repeat runs.
#
# The 95% criterion controls the FALSE-FAILURE rate only, so the same module
# measures the other side: how often the gate fires on an assigner that IS
# biased. At n=3500, 300 trials, reported against the imbalance each bias
# actually induces:
#
#     induced |SMD|   vs bound   detection
#            0.0130      below        3.7%   (the null rate)
#            0.0827      below       36.3%
#            0.1312      ABOVE       80.0%
#            0.1743      ABOVE       99.7%
#            0.2671      ABOVE      100.0%
#
# Read that in the induced-|SMD| column. Detection near the null rate BELOW
# the bound is correct behaviour, not a blind spot -- a gate that fired there
# would be enforcing a tighter threshold than the one locked. Above the bound
# it rises sharply and saturates.
#
# The residual caveat is only the definition of the bound itself: an imbalance
# smaller than 0.10 SMD passes by construction, so a PASS means "no imbalance
# beyond the declared tolerance", not "the arms are identical".
MIN_ASSIGNED_N = 3500

# Continuous covariates. SMD = (mean_t - mean_c) / sqrt((s_t^2 + s_c^2) / 2),
# with s the sample standard deviation (ddof=1).
CONTINUOUS_COVARIATES = ("amount_at_risk",)

# Categorical covariates, as {name: (levels...)}.
#
# Per level k, with p the within-arm proportion of that level:
#     SMD_k = (p_t - p_c) / sqrt((p_t(1 - p_t) + p_c(1 - p_c)) / 2)
#
# `diagnosis` is root_cause when event_type == 'payment_failed', else
# event_type. It replaces a root_cause covariate carrying a NULL level.
# Verified 2026-09-04 against the seeded population: root_cause IS NULL
# exactly when event_type != 'payment_failed' (54 checkout_abandoned + 32
# invoice_overdue = 86 of 150, zero exceptions). A NULL level is therefore
# perfectly collinear with event_type, and lumping 86 rows into it would be
# blind to a checkout_abandoned/invoice_overdue split -- an imbalance the
# 8-level form detects and the 7-level form scores as exactly 0.
#
# `is_payment_failed` is NOT redundant with `diagnosis`, and this is the one
# place the two overlap on purpose. Balance on a partition does not imply
# balance on a coarsening of it: six root-cause levels each off by +0.03 in
# the same direction all clear 0.10 individually while their union is off by
# 0.18. `diagnosis` cannot see that; this binary can. The two genuinely
# redundant levels of the original event_type covariate -- checkout_abandoned
# and invoice_overdue, which are identical partitions in both -- were dropped
# rather than gated twice.
CATEGORICAL_COVARIATES = {
    "diagnosis": (
        "insufficient_funds", "payment_declined", "gateway_timeout",
        "authentication_failed", "expired_card", "network_error",
        "checkout_abandoned", "invoice_overdue",
    ),
    "is_payment_failed": ("yes", "no"),
}

# Level lists above are DECLARED, not derived from observed data, so a level
# that draws zero rows still appears in the report as absent instead of
# silently vanishing. These names are the source of truth they must agree
# with; _check() enforces it.
DIAGNOSIS_LEVEL_SOURCES = (
    "backend.engine.trigger_event.VALID_ROOT_CAUSES",
    "backend.engine.trigger_event.VALID_EVENT_TYPES - {payment_failed}",
)

# A categorical level enters the gate only when the SMALLER arm is expected to
# hold at least this many of it: min(h, 1-h) * n_level >= this.
#
# The classic >=5 expected-cell-count rule, applied to the arm that actually
# drives the noise. Written holdout-aware rather than as a raw count so it
# stays correct if HOLDOUT_FRACTION is ever amended.
MIN_EXPECTED_ARM_COUNT = 5

# If levels excluded by that floor together cover more than this share of the
# assigned population, the gate reports NOT_EVALUABLE -- never PASS.
#
# Without it, exclusion becomes a way to manufacture a pass: clear the two
# largest levels, drop the other six as underpowered, and declare balance.
MAX_EXCLUDED_COVERAGE = 0.20

# The SMD denominator vanishes only when both arms' proportions are 0 or 1.
# Both cases are given an explicit value here rather than left to a
# ZeroDivisionError or a silent nan that would compare False against every
# bound and pass.
#
#   numerator == 0 too  -> 0.0   (both arms are 100% that level: balanced)
#   numerator != 0      -> inf   (present in one arm, absent from the other:
#                                 maximal imbalance, and it must FAIL rather
#                                 than be swallowed as "undefined")
DEGENERATE_SMD_BALANCED = 0.0
DEGENERATE_SMD_IMBALANCED = float("inf")


# --------------------------------------------------------------------------
# The counterfactual-consistency gate
# --------------------------------------------------------------------------
# HARD gate. Control opportunities must show no selected candidate, no
# executed decision, no dispatched or executed execution, and no outbound
# message at or after their assignment timestamp.
#
# Every count is reported raw. The gate is all-zero on the control arm AND
# non-zero on the treatment arm: without the second half, "control shows
# nothing" is unfalsifiable, since a system doing nothing at all would pass it.
COUNTERFACTUAL_CONTROL_EXPECTED = 0
COUNTERFACTUAL_TREATMENT_MIN = 1


# --------------------------------------------------------------------------
# Self-consistency checks
# --------------------------------------------------------------------------
# Explicit raises, run at import, for the same reason phase5_config does it:
# a contradictory edit to this file fails immediately and loudly rather than
# at whatever later point the inconsistency happens to matter.

def _check() -> None:
    if not 0.0 < HOLDOUT_FRACTION < 1.0:
        raise ValueError(
            f"HOLDOUT_FRACTION must leave both arms non-empty, got "
            f"{HOLDOUT_FRACTION}")

    if set(GROUPS) != {CONTROL_GROUP, TREATMENT_GROUP}:
        raise ValueError("GROUPS disagrees with the two named group constants")

    if SUPPRESSION_OUTCOME not in _db.DECISION_OUTCOMES:
        raise ValueError(
            f"SUPPRESSION_OUTCOME {SUPPRESSION_OUTCOME!r} is not in the closed "
            "compliance vocabulary db.DECISION_OUTCOMES")

    if SUPPRESSION_OUTCOME == "flagged_manual_review":
        raise ValueError(
            "holdout suppression must not reuse flagged_manual_review: that "
            "value means a human should decide, and reusing it would inject "
            "the whole control arm into the manual-review queue")

    if UNASSIGNED_IS_SUPPRESSED:
        raise ValueError(
            "UNASSIGNED_IS_SUPPRESSED must remain False. Suppressing every "
            "opportunity with no assignment row would freeze the entire "
            "pre-Phase-6 seeded population, which was never randomized.")

    if "lost" not in RESOLUTION_TYPES:
        raise ValueError(
            "`lost` must stay in the resolution vocabulary; it is not a "
            "synonym for `stopped` (ruling 2026-09-04)")

    if "partially_recovered" in RESOLUTION_TYPES:
        raise ValueError(
            "partial recovery is INFERRED from partial_recovery_amount, never "
            "a resolution_type member (ruling 2026-09-04)")
    if not PARTIAL_RECOVERY_IS_INFERRED:
        raise ValueError("PARTIAL_RECOVERY_IS_INFERRED must remain True")

    if MAX_ABS_SMD > 0.10:
        raise ValueError(
            f"MAX_ABS_SMD is {MAX_ABS_SMD}; the covariate-balance convention "
            "is 0.10. Anything looser is Cohen's effect-size bound, which "
            "measures a different construct.")

    if MIN_EXPECTED_ARM_COUNT < 5:
        raise ValueError(
            "MIN_EXPECTED_ARM_COUNT below 5 admits levels whose proportion "
            "estimate is too noisy for its SMD to mean anything")

    if not 0.0 <= MAX_EXCLUDED_COVERAGE <= 1.0:
        raise ValueError("MAX_EXCLUDED_COVERAGE must be a share")

    if DEGENERATE_SMD_IMBALANCED <= MAX_ABS_SMD:
        raise ValueError(
            "the one-arm-empty degenerate case must fail the bound, not pass "
            "it")

    if not CONTINUOUS_COVARIATES or not CATEGORICAL_COVARIATES:
        raise ValueError("the balance gate needs covariates on both sides")

    # The declared level lists must match their named sources
    # (DIAGNOSIS_LEVEL_SOURCES), or the gate silently stops covering a root
    # cause the entry point still accepts.
    #
    # That check is NOT made here, and the reason is a defect this file
    # originally shipped with. `_check()` runs at import, so importing
    # trigger_event here created a cycle -- trigger_event ->
    # assign_experiment_group -> phase6_config -> trigger_event -- which broke
    # `import backend.engine.trigger_event` in a fresh interpreter. It
    # survived X2's full suite only because collection happened to import
    # phase6_config first, which masks it entirely.
    #
    # A configuration module must not import an entry point at import time.
    # The assertion itself is unchanged and still mechanical; it lives in
    # tests/test_phase6_config.py::
    # test_diagnosis_levels_cover_the_entry_points_accepted_vocabulary, which
    # is free to import whatever it needs. Nothing was weakened -- the check
    # moved to a place that can perform it safely.

    if COUNTERFACTUAL_CONTROL_EXPECTED != 0:
        raise ValueError(
            "a control opportunity showing any executed action is exactly the "
            "failure this gate exists to detect")
    if COUNTERFACTUAL_TREATMENT_MIN < 1:
        raise ValueError(
            "without a non-zero treatment expectation, a system that acts on "
            "nobody would pass the counterfactual gate")


_check()
