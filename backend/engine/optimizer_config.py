"""
Phase 4 declared constants for the optimizer.

Deliberately a separate module from `data_factory/locked_thresholds.json`.
That file holds Phase 2/3 *statistical tolerances* and is marked read-only
for Phase 4 by the Phase 3 hand-off (section 4, "frozen inputs"). The
Phase 4 acceptance gate nonetheless requires the candidate-count ceiling to
be "declared in config and enforced by an assertion, not just measured" --
so the declaration lives here, and nothing in Phase 4 edits a Phase 3 lock.

Every number in this file is a declared engineering bound, not a measured
model property. Each one records how it was derived.
"""

# --------------------------------------------------------------------------
# Candidate-set ceiling
# --------------------------------------------------------------------------
# Enumerated directly from data_factory/candidate_generation.py's own rules,
# not guessed. The worst case is a payment_failed opportunity whose root
# cause is method-change-relevant (expired_card / authentication_failed),
# because that is the only shape where retry contributes two methods:
#
#   payment_failed + method-change-relevant root cause
#       do_nothing                                  1
#       retry       2 methods x 2 timings           4
#       reminder    2 timings x 2 channels          4
#       payment_link 2 timings x 2 channels         4
#       escalate                                    1
#                                                  --
#                                                   14   <-- maximum
#
#   payment_failed, other root causes                12
#   invoice_overdue                                  10
#   checkout_abandoned                                8
#
# The ceiling is set at 16 rather than exactly 14 so that a future
# root-cause or timing-window addition in the shared module surfaces as a
# deliberate config change rather than an immediate hard failure -- while
# still being tight enough that an accidental combinatorial blow-up (the
# naive cross product is 4 actions x 4 timings x 4 methods x 3 channels =
# 192 before do_nothing) trips the assertion immediately.
MAX_CANDIDATES = 16

# The measured worst case above, asserted separately by the test suite so
# that if the shared module's rules change, the test tells us the real
# number moved rather than silently consuming the headroom.
OBSERVED_MAX_CANDIDATES = 14


# --------------------------------------------------------------------------
# Near-tie band (carried-forward Phase 3 requirement)
# --------------------------------------------------------------------------
# Phase 3 measured pairwise ranking agreement as a function of the true
# effect gap, on the primary model:
#
#       gap 0.05 - 0.08   agreement 0.838
#       gap 0.08 - 0.12   agreement 0.919
#       gap 0.12 - 0.20   agreement 0.955
#       gap > 0.20        agreement 0.982
#
# 0.05 is the lower edge of the band Phase 3 demonstrated to be unreliable,
# so it is the threshold below which an EIV ordering is not claimed as
# resolved. The Phase 3 evidence is in PROBABILITY space and EIV is in
# rupees, so the band is scaled by the opportunity's amount_at_risk to
# convert: a gap of 0.05 x amount is the rupee equivalent of a 0.05
# probability gap on full recovery of that amount.
#
# This is a DISPLAY threshold. It never enters the ranking.
NEAR_TIE_BAND_FRACTION = 0.05

# The (candidate action_type, opportunity event_type) combinations Phase 3
# disclosed as carrying a broad, undiagnosed ranking-quality gap on seed 43
# (temporal ranking agreement 0.813 vs a 0.85 bar). Recorded per combination
# with its measured share of the 573 disagreeing pairs.
#
# The first four are the combinations named in the locked Phase 3 hand-off
# requirement. The fifth, escalate|checkout_abandoned, is a WIDENING of that
# locked list, approved as ambiguity ruling A3: it carried 17.5% of the
# disclosed disagreement share -- more than payment_link|invoice_overdue's
# 20.2% is above reminder|invoice_overdue's 21.3% is above it -- and the
# original wording named only reminder/payment_link, which under-covered the
# evidence Phase 3 itself recorded. See PHASE4_NOTES.md.
#
# payment_link|checkout_abandoned is retained because the locked wording
# names it, and is marked here as structurally unreachable: the shared
# candidate generator emits payment_link only for payment_failed and
# invoice_overdue. A test asserts it stays unreachable, so if that ever
# changes, the coverage is already in place rather than needing to be
# remembered.
PHASE3_LOW_CONFIDENCE_COMBINATIONS = frozenset({
    ("reminder", "checkout_abandoned"),      # 49.9% of disagreeing pairs
    ("reminder", "invoice_overdue"),         # 21.3%
    ("payment_link", "invoice_overdue"),     # 20.2%
    ("payment_link", "checkout_abandoned"),  # locked; structurally unreachable
    ("escalate", "checkout_abandoned"),      # 17.5% -- widened per ruling A3
})

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"

REASON_NEAR_TIE = "near_tie"
REASON_FLAGGED_BUCKET = "phase3_flagged_bucket"


# --------------------------------------------------------------------------
# Latency budget
# --------------------------------------------------------------------------
# End-to-end for ONE opportunity: context assembly, candidate generation,
# every model evaluation, ranking, confidence attachment and persistence --
# measured with the model artifact already warm, since a cold joblib load is
# a process-startup cost paid once, not a per-opportunity cost.
#
# 250 ms is chosen as a live/demo-suitable interactive bound: the Control
# Tower's reasoning panel renders per opportunity, and a quarter second is
# the point past which that stops feeling immediate. It is a declared
# budget, not a measured result -- the suite measures the real number and
# prints it.
LATENCY_BUDGET_MS = 250.0
