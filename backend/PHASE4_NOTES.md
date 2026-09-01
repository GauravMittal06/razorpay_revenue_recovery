# Phase 4 — Optimizer: decision and rationale log

Read with `SoT.md`, `EXECUTION_PLAN.md`, `STATE_AND_DECISIONS.md`,
`FILE_INVENTORY.md`, `backend/PHASE3_HANDOFF.md`.

## 1. Status: CONDITIONALLY CLEARED — 13 of 14 Phase 4 gates pass

**One outstanding disclosed gap: G10 (latency).** No threshold was moved to
obtain a pass; `LATENCY_BUDGET_MS = 250.0` was declared before measurement
and stands unchanged at a measured p95 of ~858 ms (§7).

**G7 (ranking correctness) PASSES at 0.966 / 0.967** on frozen seed-42 and
seed-43 Phase 3 data, well above the 0.90 bar. An earlier revision of this
document recorded G7 as FAILING at 0.389 with a `payment_link`/`escalate`
diagnosis; **that finding is RETRACTED as a measurement error in Phase 4's
own test** — see §8.0. It was not a model defect.

One genuinely new model property was found in the course of that
investigation and is disclosed in §8.6: the amount head varies
`E[amount|recovered]` with the candidate, which the generator does not,
perturbing ~16% of live pair orderings. **Known, not fixed, carried
forward** — not blocking Phase 4.

Full-suite position: **217 passed, 16 failed**. Fourteen of those sixteen are
pre-existing failures, present identically in the main checkout before any
Phase 4 work (verified by running the suite there first). Of the two Phase 4
failures, one is G10; the other is the G7 test, which still encodes the
retracted methodology and is a known-failing test with a known cause (§8.3).

## 2. What Phase 4 delivered

`engine/optimize.py` — generates a bounded candidate set for a real
opportunity, scores each against one shared `do_nothing` baseline, ranks by
Expected Incremental Value, attaches the carried-forward near-tie confidence
disclosure, and writes the full considered set to `recovery_candidates`.

    EIV = expected_recovered_amount(candidate)
        − expected_recovered_amount(do_nothing)
        − intervention_cost(candidate)

Reuse discipline held exactly:

- `data_factory/candidate_generation.py` imported **unmodified**. Phase 4
  contains **zero eligibility rules of its own** — the twelve G1 unit tests
  assert against the shared module, so they pin the offline/live contract
  rather than a Phase 4 reimplementation.
- `ml/inference.py` imported **unmodified**. `optimize.py` loads no model
  artifact, builds no feature row, and calls no predictor — mechanically
  enforced by `test_optimizer_does_not_open_a_second_scoring_path`.
- Write access is one statement, `INSERT INTO recovery_candidates`,
  mechanically enforced by `test_optimizer_writes_only_the_audit_table`.
- `selected` is **always 0**. "Selected" means the rule engine approved
  execution; the optimizer has no authority to grant that. Phase 5 sets it.

`decide_action.py`, `execute_action.py` and `core_loop.py` are untouched.
Phase 4 delivers a callable module; wiring it into the pipeline is Phase 5.

## 3. Ambiguity rulings, as implemented

| # | Ruling | Implementation |
|---|---|---|
| A1 | Cost values proposed + recorded | `engine/intervention_cost.py`, §4 below |
| A2 | Near-tie is OR, not AND | `attach_confidence()`; either signal alone marks low |
| A3 | Flagged list **widened** | `escalate\|checkout_abandoned` added — see §5 |
| A4 | Config in a new file | `engine/optimizer_config.py`; `locked_thresholds.json` untouched |
| A5 | Baseline cached once per opportunity | Equivalence proven to exactly 0.0 by test |
| A6 | Pruning audit option (i) | Diff against the shared module's own constants; §6 |
| A7 | Network health unavailable live | Asserted, not silent; §8 |
| A8 | Locked EIV, not SoT §3.3's three-factor ERV | Implemented as locked; ERV's causal-incrementality factor deferred to post-Phase 6/7 |
| A9/A10/A11 | Carried to Phase 5 | No Phase 4 action |

## 4. Intervention cost — locked Phase 4 decision (ruling A1)

**Every value is a synthetic placeholder.** No Razorpay price list, no
telecom rate card, no agent-cost study backs them. They must be replaced
before any non-synthetic claim is made from an EIV number.

| Action | Cost (₹) |
|---|---|
| `do_nothing` | 0.0 |
| `reminder` email / sms / whatsapp | 2.0 / 5.0 / 7.0 |
| `retry` | 3.0 |
| `payment_link` email / sms / whatsapp | 4.0 / 7.0 / 9.0 (reminder + 2.0) |
| `escalate` | 250.0 |

Ordering rationale, which matters far more than the magnitudes:

1. `do_nothing` = 0 **definitionally** — if the baseline had a cost, its EIV
   would not be exactly zero, and that zero is a locked invariant.
2. An email reminder is the cheapest touch; email is effectively free at
   volume and carries the lowest fatigue weight.
3. A gateway retry sits just above it: no customer contact and therefore no
   fatigue component at all, but a real per-attempt gateway fee an email
   does not carry.
4. Channel ordering email < sms < whatsapp — directionally correct for the
   Indian market (SMS telecom tariff; WhatsApp Business per-conversation
   above SMS).
5. `payment_link` = same-channel reminder + 2.0: link generation and hosting,
   plus a higher fatigue weight because it asks for money directly.
6. `escalate` two orders of magnitude above everything: human agent time is
   the scarcest resource, and this is what stops escalation winning ties by
   default.

An unpriced action **raises** `UnknownActionCost` rather than defaulting to
zero — a silently-zero cost would make an unpriced action the single most
attractive candidate in the ranking. An unknown channel is priced at the
most expensive known channel, never an average, so an unrecognised channel
can never look cheaper than a known one.

**Correction recorded:** the first draft of `intervention_cost.py` described
`retry` as the cheapest non-zero action, which contradicted its own table
(retry 3.0 > email 2.0). The table was the intent; the prose was corrected
to match it and a test now pins the ordering. Recorded here rather than
silently amended.

## 5. Flagged-bucket list widened (ruling A3)

The locked Phase 3 hand-off named `reminder`/`payment_link` ×
`checkout_abandoned`/`invoice_overdue`. Phase 3 §2(c) had itself recorded
**four** concentrations among the 573 disagreeing pairs:

| Combination | Share |
|---|---|
| `reminder \| checkout_abandoned` | 49.9% |
| `reminder \| invoice_overdue` | 21.3% |
| `payment_link \| invoice_overdue` | 20.2% |
| **`escalate \| checkout_abandoned`** | **17.5%** |

The locked wording named only two action types and so under-covered its own
evidence: `escalate|checkout_abandoned` carried a share comparable to
combinations that *were* named. It is now in
`PHASE3_LOW_CONFIDENCE_COMBINATIONS`.

`payment_link|checkout_abandoned` is retained because the locked wording
names it, and is marked **structurally unreachable** — the shared generator
emits `payment_link` only for `payment_failed` and `invoice_overdue`. A test
asserts it stays unreachable, so if that changes the coverage already exists.

## 6. Pruning audit without modifying the frozen module (ruling A6)

The shared generator does not *reject* candidates, it simply never emits
them, so it cannot report what it dropped without being modified — and it is
frozen. Option (i) was implemented and **did not prove fragile**:

- The naive space is built from the module's **own exported constants**
  (`TIMING_HOURS`, `METHODS`, `CHANNELS`), in the same per-action shape the
  module emits — 42 tuples against the 8–14 it actually emits.
- Each exclusion is attributed using the module's **own public read-only
  helpers** (`eligible_timings`, `eligible_channels`,
  `eligible_retry_methods`).
- Anything those helpers cannot explain falls to `structural_eligibility` by
  elimination — which is how action-level suppression (retry on a
  non-`payment_failed` event, `payment_link` on a checkout, a terminal
  opportunity) is attributed **without this file restating those rules**.

No eligibility rule is duplicated in Phase 4. The fallback to option (ii)
was not needed.

## 7. FAILING GATE 1 — latency: BUDGET NOT YET MET

**Status: the declared 250 ms budget is NOT MET and was NOT fixed in Phase 4.**
Accepted as a disclosed, carried-forward item on review, because the fix
requires modifying frozen `ml/inference.py` and re-running Phase 3's
train/serve parity gate — out of Phase 4 scope. It remains an open
obligation, not a closed one: any later claim that the optimizer is
"live/demo-ready on latency" is unsupported until this gate passes.
Owner: Phase 5 or Phase 9.

### Measured (declared 250 ms, p95 ≈ 860 ms)

    warm, 12 opportunities, at the enforced candidate ceiling
    p50 = 757.6 ms      p95 = 858.1 ms      budget = 250.0 ms   FAIL

Diagnosed, not guessed. Stage breakdown per opportunity:

    context assembly    1.41 ms
    candidate generation 0.03 ms
    baseline scoring    48.59 ms
    candidate scoring  594.82 ms   (51.3 ms per call)
    pruning audit        0.11 ms
                        --------
                       644.95 ms      99.7% is model inference

The cost is per-call fixed overhead in single-row inference: each
`score_candidate` builds a one-row DataFrame and runs two sklearn pipelines
(`ColumnTransformer` → XGBoost). Measured directly:

    one row at a time, 12 candidates   350.00 ms
    one batched call, same 12 rows      52.76 ms      6.6x

So a batch scoring entry point would bring an opportunity to roughly 100 ms,
comfortably inside the budget. **That requires adding a function to
`ml/inference.py`, a frozen Phase 3 module, and would require re-running
Phase 3's train/serve parity gate.** It is therefore correctly out of Phase 4
scope and is recommended as a Phase 5/Phase 9 item.

The budget was **not** moved to obtain a pass.

## 8. GATE 2 — ranking correctness: PASSES. Original finding RETRACTED.

### 8.0 RETRACTION

**An earlier version of this section reported a ranking-correctness FAILURE
(`network_error` 0.389, `gateway_timeout` 0.800) and diagnosed it as the
model "over-valuing `payment_link` and under-valuing `escalate`" on
transient root causes. Both claims are RETRACTED. They were a measurement
error in Phase 4's own test, not a model defect, and the diagnosis had the
direction backwards.**

Two compounding faults in the original G7 implementation:

1. **Wrong comparison space.** It compared the generator's analytic
   *probability* against the model's *rupee* incremental amount. Phase 3's
   2026-09-01 temporal amendment had already identified exactly this
   mis-specification and corrected its own gate to compare like-for-like
   probability-space effects. Phase 4 reintroduced the error the amendment
   removed.
2. **Off-distribution contexts.** It measured on hand-constructed contexts
   rather than real generated cases, which pushed the amount head roughly
   8× further off-distribution than it ever goes on live data (§8.5).

The retracted numbers came from those two faults acting together, not from
the model.

### 8.1 The real result, on Phase 3's frozen data

Re-measured against the generator's **recorded `analytic_p`** — real ground
truth, no reconstruction — on the frozen, hash-verified Phase 3 artifacts,
scored through Phase 3's own batch path. Direction agreement at
ground-truth gap ≥ 0.12:

| Source | Decisive pairs | Agreement |
|---|---|---|
| seed 42 temporal holdout (unseen) | 1,084 | **0.966** |
| seed 43 joint (the disclosed-gap seed) | 6,451 | **0.967** |
| — `network_error` (seed 43) | 82 | **0.988** |
| — `gateway_timeout` (seed 43) | 73 | **0.986** |
| — `expired_card` (seed 43) | 1,337 | 0.966 |
| — `authentication_failed` (seed 43) | 1,180 | 0.965 |

`network_error` is **0.988**, not 0.389. **G7 PASSES**, comfortably above the
0.90 bar on both seeds.

The per-action rank bias on frozen data also runs **opposite** to the
retracted diagnosis (negative = model ranks the action *higher* than truth
does; seed 43):

| root_cause | do_nothing | retry | reminder | payment_link | escalate |
|---|---|---|---|---|---|
| `authentication_failed` | +0.25 | −1.92 | +1.66 | **+1.05** | **−5.65** |
| `gateway_timeout` | +0.26 | +0.20 | −0.40 | **+0.87** | **−2.45** |
| `network_error` | +0.63 | +0.04 | +1.14 | **−1.30** | −0.10 |

`escalate` is ranked consistently *higher* than truth and `payment_link`
mostly *lower* — the reverse of what was originally reported.

At population scale there is essentially nothing to attribute: at gap ≥ 0.12
`network_error` produced **1 disagreeing pair out of 82** and
`gateway_timeout` 1 of 73. The retracted diagnosis rested on a single
hand-constructed context.

### 8.2 Isolation — which fault caused what

Same contexts and same ground truth, varying only the comparison axis:

| | probability space | rupee space |
|---|---|---|
| Frozen eval data | 0.966 (n=1084) | 0.940 (n=1084) |
| Constructed contexts (original G7) | 0.958 (n=96) | **0.812** (n=96) |
| — `network_error` | **1.000** (n=18) | **0.389** (n=18) |

The ground-truth reconstruction was **not** the problem — constructed
contexts scored 0.958 in probability space, close to the frozen 0.966. The
**comparison space** was.

### 8.3 Test-suite status — HONEST DISCREPANCY, recorded not hidden

`test_higher_true_incremental_value_ranks_above_lower` in
`backend/tests/test_phase4_optimizer.py` **still encodes the flawed
methodology and therefore still fails**. It was deliberately left untouched:
correcting it is a code change, and the review that produced this retraction
scoped this pass to documentation only.

So the suite and this document disagree by design. The gate table's G7 =
PASS is supported by the frozen-data evidence in §8.1, **not** by the
automated test as currently written. **Open obligation:** re-implement that
test to compare probability-space against probability-space (and, separately,
to assert the rupee-space axis with the §8.5 sensitivity acknowledged),
running it against frozen eval cases rather than constructed contexts.
Until then it is a known-failing test with a known cause.

### 8.4 Attribution, unchanged

Phase 4 owns "the optimizer ranks faithfully by EIV" — asserted by
`test_the_optimizer_ranks_faithfully_by_eiv`, which **passes**: the emitted
order is exactly descending EIV with a deterministic tiebreak, on every
opportunity.

### 8.5 Why the constructed contexts broke it

The mechanism behind the retraction: the model's amount head varies
`E[amount|recovered]` with the candidate, which the generator does not. On
the hand-constructed contexts the original G7 used, that variation ran ~8×
its live magnitude (0.191 vs 0.023 median within-case spread), which is what
turned a correct probability ordering into a 0.389 rupee ordering. Full
disclosure entry follows in §8.6.

### 8.6 NEW DISCLOSURE — candidate-dependent `E[amount|recovered]` head

**Status: KNOWN, NOT FIXED, CARRIED FORWARD. Not blocking Phase 4.**
Candidate for a scoped Phase 3 model revision later. Deliberately not fixed
here: changing the amount head is Phase 3 model surgery and would require
retraining and re-running Phase 3's gates — the same scope reasoning that
keeps the G10 latency fix out of Phase 4.

**Mechanism.** `expected_recovered_amount = p_recovery x E[amount|recovered]`.
In the generator, `draw_outcome()` draws the recovered fraction from
`rng.beta(*profile.partial_recovery_fraction_beta)` with probability
`profile.partial_recovery_probability` — **neither depends on action_type,
timing, method or channel**. Ground truth therefore says
`E[amount|recovered]` is *candidate-independent* within a case. The model's
amount head varies it with the candidate anyway, because the candidate tuple
is in its feature row. That variation is spurious, and because EIV is
computed in rupee space it can invert an ordering the probability head gets
right.

**Measured impact.**

Within-case `(max − min) / mean` of predicted `E[amount|recovered]`
(ground truth: should be ~0):

| Population | p50 | p90 | max | n |
|---|---|---|---|---|
| Frozen eval cases | 0.026 | 0.081 | 1.105 | 426 |
| **Live opportunities (optimizer path)** | **0.023** | 0.064 | 1.060 | 120 |
| Constructed contexts (original G7) | **0.191** | 0.583 | 0.747 | 16 |

Ordering impact:

- **Live opportunities: 997 of 6,140 candidate pairs (16.2%)** have a
  rupee-space ordering that disagrees with the probability-space ordering.
- **Frozen eval data: 32 of 1,047 (3.1%)** of pairs the probability head
  orders *correctly* are flipped to wrong by the amount head.
- Mean predicted `E[amount|recovered]` normalised by case mean shows no
  systematic action-type bias — `do_nothing` 1.0033, `retry` 1.0043,
  `reminder` 1.0005, `payment_link` 0.9987, `escalate` 0.9936. The effect is
  per-case noise, not a per-action tilt.

**Why the live spread is small but the pair impact is not.** The within-case
spread on live data is only ~2%, but baseline recovery probability sits at
p50 = 0.909 (§8.7), so most candidate pairs are separated by very little in
probability. A 2% wobble in the amount term is then large enough to decide
the ordering. This finding and the saturation finding compound each other.

**Why this was never caught before.** Phase 3's 2026-09-01 temporal
amendment moved its ranking gate *into* probability space — correctly, for
measuring the probability head. The consequence is that **the rupee-space
ordering has never been gated anywhere**, and rupee space is exactly where
Phase 4's EIV lives. This is not a restatement of the recorded
shrinkage/near-tie weakness; it is an unmeasured property sitting in a gap
those gates were amended not to cover.

**What breaks if ignored.** Any downstream consumer that treats the
rupee-space EIV ordering as more authoritative than the underlying
probability signal would be relying on a ~16%-perturbed ordering without
knowing it.

### 8.7 Why several scenarios have no decisive pairs — the operating-point finding

This is the most significant finding of the phase, and it corroborates the
carried-forward Phase 3 requirement rather than contradicting it.

Baseline recovery probability over 60 real opportunities:

    min 0.605   p25 0.875   p50 0.909   p75 0.936   max 0.962
    55% of opportunities sit above p = 0.90

At p ≈ 0.91 the sigmoid is saturated (z₀ ≈ 2.3–2.9). The **entire** analytic
span of the reachable candidate space — 1.13 to 1.79 logits — compresses to a
probability span of only **0.02 to 0.13**. Even a fully adverse context
(history 0.05, recovery 0.05, retry_count 2, 9 days elapsed, 3 prior
contacts) only pulls the baseline down to 0.825.

Phase 3 measured ranking agreement of 0.838 at gaps of 0.05–0.08 and 0.982
above 0.20. **Almost the whole reachable candidate space at Phase 4's live
operating point lies inside the band Phase 3 demonstrated to be unreliable.**
That is exactly why `payment_declined`, `checkout_abandoned` and
`invoice_overdue` produce no pairs separated by 0.12 — the environment does
not offer that much separation there.

Agreement-vs-gap curve at Phase 4's operating point, against Phase 3's:

| Ground-truth gap | Phase 4 | Phase 3 |
|---|---|---|
| 0.00–0.02 | 0.481 (n=547) | — |
| 0.02–0.05 | 0.608 (n=245) | — |
| 0.05–0.08 | 0.712 (n=66) | 0.838 |
| 0.08–0.12 | 0.655 (n=84) | 0.919 |
| 0.12–0.20 | 0.667 (n=36) | 0.955 |
| 0.20+ | 0.900 (n=60) | 0.982 |

Agreement rises with gap size, reproducing Phase 3's monotone shape, but sits
below Phase 3's curve at every band.

### 8.8 Null result — the network-health hypothesis was tested and REFUTED

The obvious explanation for §8.2 was the A7 network-health gap. It was tested
directly by re-running the whole curve with `bank`/`psp`/`decision_time_hours`
populated from the real `bank_health_observations` table (51,840 rows):

| Gap band | health UNKNOWN | health KNOWN |
|---|---|---|
| 0.05–0.08 | 0.738 | 0.647 |
| 0.08–0.12 | 0.675 | 0.652 |
| 0.12–0.20 | 0.766 | 0.742 |
| 0.20+ | 0.895 | 0.915 |
| **OVERALL** | **0.587** | **0.571** |

**Populating network health does not improve ranking agreement.** The A7 gap
is a real capability limitation but is **not** the cause of the ranking
shortfall. Root cause of the gap between Phase 4's and Phase 3's curves
remains **undiagnosed** beyond §8.1's transient-class finding.

### 8.9 Limitation of the constructed-context measurement

Stated plainly so a reviewer can weigh it. Phase 3 measured agreement on its
frozen eval corpus, where each row carries the generator's actual sampled
hidden state. Phase 4 cannot do that for a live opportunity — there is no
hidden state to read — so ground truth here is **reconstructed** by inverting
the model's own `do_nothing` probability to recover z₀, then applying the
generator's four candidate-dependent terms (`action_effectiveness`,
`timing_term`, `fatigue_term`, `network_health_term`; every other term is
candidate-independent and cancels within a case).

**This anchors ground truth on the model's own baseline.** If that baseline
is biased, every analytic probability shifts with it. The two measurements
are therefore **not directly comparable**, and the Phase 4 numbers should not
be read as "the model got worse" — only as "at the live operating point, with
ground truth reconstructed this way, agreement measures this."

An earlier version of this measurement omitted `fatigue_term` and was
corrected before the numbers above were taken.

## 9. Disclosed limitation — no network health at serving time (ruling A7)

The live schema has **no `bank` and no `psp` column on `payments`**, and
`bank_health_observations.window_start/window_end` are in **simulated hours**
with no defined mapping from a live unix timestamp. `optimize.py` therefore
passes `bank=None, psp=None, decision_time_hours=0.0`, and all four
network-health features are unavailable live —
`network_health_known = 0.0` on every scoring.

Phase 3 explicitly parity-tested this exact regime (the "null-network-health
case"), so it is **safe**, and §8.3 shows it is not what is costing ranking
quality. It remains a real capability gap. `test_live_context_has_no_network_
health_and_says_so_explicitly` asserts it rather than letting it pass
silently. **Phase 6/7 closure item.**

## 10. Other recorded decisions

- **`current_method` is `None`, not a fabricated default**, when an
  opportunity has no payment behind it (`checkout_abandoned`). An invented
  `"card"` would be an input the model treats as real; `None` reaches the
  encoder as an unseen category and is ignored — the honest representation of
  "we do not know". `retry`, the only action carrying a method, is not
  structurally eligible for those event types anyway.
- **The ceiling is a `raise`, not an `assert`.** `assert` is stripped under
  `python -O`, and a bound that disappears in an optimised interpreter is not
  a bound.
- **Near-tie is symmetric** — a candidate is flagged if it is within the band
  of an adjacent candidate above *or* below it. If two candidates are within
  noise of each other, both are unresolved, not just the upper one.
- **Fail-closed.** A failed baseline aborts the whole optimization and writes
  **nothing** (a partial set with a missing baseline would be a silently
  wrong audit record). A single candidate's scoring failure is persisted with
  `pruned_stage='scoring_failed'` and a NULL EIV, so the failure is visible
  rather than looking like a candidate never considered.
- **Schema migration.** Three nullable columns were added to
  `recovery_candidates`. A database created before Phase 4 needs either a
  rebuild (`python -m backend.db.db`) or three `ALTER TABLE ... ADD COLUMN`
  statements (`eiv_confidence TEXT`, `eiv_confidence_reason TEXT`,
  `eiv_gap_to_next REAL`).

## 11. Observed behaviour on the real corpus (120 opportunities)

- Candidate-set size **8–14**, ceiling 16, worst case 14 exactly as
  enumerated — `payment_failed` with a method-change-relevant root cause.
- `do_nothing` EIV **exactly 0.0 on every opportunity**, no exceptions.
- `do_nothing` ranked **first on 4 of 120** opportunities, winning only where
  every alternative had negative incremental value. It is a competitive
  option, not a floor.
- Confidence labels: **18 high, 1230 low**
  (`near_tie` 704, `near_tie+phase3_flagged_bucket` 521,
  `phase3_flagged_bucket` 5, none 18).

That last number is not a bug — it is §8.2 restated. The median top-of-ranking
gap is 0.0036 of amount at risk, i.e. a probability-equivalent separation of
0.0036 against a noise floor Phase 3 measured at ≈0.022 probability-sd. On
this corpus the model genuinely does not resolve most candidate pairs, and
the flag says so. **This strongly corroborates the carried-forward
requirement:** presenting these orderings as confident recommendations would
overstate the model's demonstrated resolution.

## 12. Exit gate table

| Gate | Result |
|---|---|
| G1 per-eligibility-rule unit tests (12 rules) | **PASS** — 40 test cases |
| G2 static no-execution-authority check | **PASS** — 6 mechanical checks, in `test_permanent_gates.py` so Phase 9 re-runs them |
| G3 `do_nothing` correctly zero-valued | **PASS** — exactly 0.0, all sampled |
| G4 `do_nothing` genuinely competitive | **PASS** — ranks first on 4/120 |
| G5 candidate ceiling declared + enforced | **PASS** — exhaustive sweep; `raise`, not `assert` |
| G6 single shared baseline, one scoring path | **PASS** |
| G7 ranking correctness vs ground truth | **PASS** — 0.966 (seed 42, n=1084) / 0.967 (seed 43, n=6451) on frozen data, vs a 0.90 bar. Original 0.389 FAIL retracted as a Phase 4 test-methodology error; the automated test still encodes it and is known-failing (§8.0, §8.3) |
| G8 auditability, full considered set persisted | **PASS** |
| G9 method change scoreable, not executable | **PASS** |
| G10 latency within declared budget | **FAIL** — p95 858 ms vs 250 ms; §7 |
| G11 fail-closed | **PASS** — 4 checks |
| G12 near-tie flag, ranking unchanged | **PASS** — 6 checks |
| A5 baseline-cache equivalence | **PASS** — exactly 0.0 |
| A7 network-health limitation asserted | **PASS** (asserts the limitation holds) |

## 13. Integrity statement

No threshold was loosened anywhere in Phase 4 to obtain a pass. The one
outstanding gate (G10 latency) retains the bar declared before measurement.

**G7's status changed from FAIL to PASS on evidence, not on a moved bar.**
The 0.90 agreement bar is unchanged; what changed is that the measurement
was found to be wrong and was redone correctly, against the generator's
recorded `analytic_p` on frozen, hash-verified Phase 3 artifacts. The
retracted numbers, the reason they were wrong, and the fact that the
automated test still encodes the flawed methodology are all recorded in
§8.0–§8.3 rather than deleted. A reader can reconstruct exactly what was
claimed, why it was wrong, and what replaced it.

Corrections and null results recorded rather than silently amended:

- §8.0 — G7's 0.389 finding and its `payment_link`/`escalate` diagnosis,
  **retracted**; the diagnosis had the direction backwards.
- §8.8 — the network-health hypothesis for the ranking gap, **tested and
  refuted** (0.587 → 0.571).
- §4 — cost prose contradicted its own table; prose corrected, test added.
- §8.9 — the constructed-context measurement omitted `fatigue_term` in its
  first version.
- §5 — the flagged-combination list was widened, not narrowed.

One new model property was found and is disclosed as **known, not fixed**
(§8.6) rather than being folded into a passing result.

**Phase 4 sign-off is a separate step and has not been granted.**
