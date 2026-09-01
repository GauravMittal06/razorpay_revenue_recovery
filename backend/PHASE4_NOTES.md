# Phase 4 — Optimizer: decision and rationale log

Read with `SoT.md`, `EXECUTION_PLAN.md`, `STATE_AND_DECISIONS.md`,
`FILE_INVENTORY.md`, `backend/PHASE3_HANDOFF.md`.

## 1. Status: CONDITIONALLY CLEARED — 12 of 14 Phase 4 gates pass

83 of 85 Phase 4 tests pass. Two gates fail, both disclosed below with raw
numbers and both reproducible from the suite. **No threshold was moved to
obtain a pass.** The two failing bars (`LATENCY_BUDGET_MS = 250.0`, ranking
agreement `0.90`) were declared before measurement — the latency budget in
the approved plan and in `optimizer_config.py`; the agreement bar taken from
Phase 3's own locked treatment-effect direction bar rather than chosen here.

Full-suite position: **217 passed, 16 failed**. Fourteen of those sixteen are
pre-existing failures, present identically in the main checkout before any
Phase 4 work (verified by running the suite there first). Phase 4 added two.

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

## 7. FAILING GATE 1 — latency (declared 250 ms, measured p95 ≈ 860 ms)

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

## 8. FAILING GATE 2 — ranking correctness against ground truth

Direction agreement at ground-truth gap ≥ 0.12, bar 0.90 (Phase 3's lock):

| Scenario | Agreement | Decisive pairs |
|---|---|---|
| `payment_failed`/`expired_card` (method-eligible) | **1.000** | 24 |
| `payment_failed`/`insufficient_funds` | **0.950** | 20 |
| `payment_failed`/`authentication_failed` (method-eligible) | 0.833 | 24 |
| `payment_failed`/`gateway_timeout` | 0.800 | 10 |
| `payment_failed`/`network_error` | **0.389** | 18 |
| `payment_failed`/`payment_declined` | no decisive pairs | 0 |
| `checkout_abandoned` | no decisive pairs | 0 |
| `invoice_overdue` | no decisive pairs | 0 |

**Attribution matters here.** Phase 4 owns "the optimizer ranks faithfully by
EIV" — asserted separately by
`test_the_optimizer_ranks_faithfully_by_eiv`, and it **passes**: the emitted
order is exactly descending EIV with a deterministic tiebreak, on every
opportunity. This gate measures the **Phase 3 artifact's** ordering quality
as surfaced through the optimizer. The failure is evidence about the model,
not about the ranking machinery.

### 8.1 Diagnosis of the `network_error` outlier

Not left as an undiagnosed mystery. On transient root causes the generator's
ordering is `retry (1.1) > escalate (0.35) > reminder (0.2) > payment_link
(0.05)`. The model's estimated incremental amounts on `network_error`:

    action        analytic_p    model_incremental_₹
    retry          0.9652              5222.9
    payment_link   0.9067              5265.4     <- over-valued
    reminder       0.9186              2893.3
    escalate       0.9291              1373.7     <- under-valued

**The model over-values `payment_link` — analytically near-identical to
`do_nothing` on a transient failure — and under-values `escalate`, which the
generator places second.** The same pattern is visible on `gateway_timeout`.
This is a specific, localised model weakness in the transient root-cause
class, and it is the direct cause of the sub-bar agreement.

### 8.2 Why several scenarios have no decisive pairs — the operating-point finding

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

### 8.3 Null result — the network-health hypothesis was tested and REFUTED

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

### 8.4 Limitation of this measurement itself

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
| G7 ranking correctness vs ground truth | **FAIL** — 2 of 5 covered scenarios at/above bar; §8 |
| G8 auditability, full considered set persisted | **PASS** |
| G9 method change scoreable, not executable | **PASS** |
| G10 latency within declared budget | **FAIL** — p95 858 ms vs 250 ms; §7 |
| G11 fail-closed | **PASS** — 4 checks |
| G12 near-tie flag, ranking unchanged | **PASS** — 6 checks |
| A5 baseline-cache equivalence | **PASS** — exactly 0.0 |
| A7 network-health limitation asserted | **PASS** (asserts the limitation holds) |

## 13. Integrity statement

No threshold was loosened anywhere in Phase 4 to obtain a pass. Both failing
gates retain the bars declared before measurement. One hypothesis (§8.3) was
tested and refuted, and the refutation is recorded rather than dropped. One
self-inconsistency (§4, cost prose vs table) and one measurement defect
(§8.4, omitted `fatigue_term`) were found and corrected, and both are
recorded rather than silently amended. The flagged-combination list was
widened, not narrowed.

**Phase 4 sign-off is a separate step and has not been granted.**
