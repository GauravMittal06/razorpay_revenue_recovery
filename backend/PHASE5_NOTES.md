# Phase 5 Notes — Rule Engine & Bounded Executor

Working record, written as the phase proceeds. `PHASE5_HANDOFF.md` is a
separate document produced at sign-off and draws on this one.

Read with `EXECUTION_PLAN.md`, `SoT.md`, `STATE_AND_DECISIONS.md`,
`Phase_Acceptance_Test_Gates.md`, `backend/PHASE4_HANDOFF.md`.

---

## 0. Document drift discovered in Phase 5

Two places where the project's own specification documents disagree with each
other. Both are recorded here rather than silently resolved, because in each
case a reader consulting only one document would reach a different conclusion
about what the system is supposed to do.

### 0.1 `method_change` — SoT ↔ EXECUTION_PLAN

| Document | Says |
|---|---|
| `SoT.md:63` | lists "alternate payment method" among the rule engine's dispatchable tools |
| `EXECUTION_PLAN.md:206` | "structurally excludes autonomous payment-method switching: there is no code path anywhere in the executor capable of dispatching a method change" |

**Ruling applied in Phase 5:** `EXECUTION_PLAN.md` governs as the operative
spec — method change is evaluable, never executable. `SoT.md` is the earlier,
more general description; the EXECUTION_PLAN statement is later and more
specific, and is reinforced by a permanent invariant
(`EXECUTION_PLAN.md:301`, "Payment-method changes are never dispatched
autonomously, structurally").

**Resolved 2026-09-02.** `SoT.md:63` was amended: "alternate payment method"
was removed from the dispatchable-tools list, and a sentence was added
recording that it is ranked as a candidate but never dispatched autonomously,
pointing here for the rationale. The edit was deliberately minimal -- one line
changed in a 205-line file, nothing else moved.

Note that `SoT.md:59` (Section 3.5, Optimize) still reads "Rank every eligible
**(action, timing, payment-method)** combination", and that is *correct* and
was left alone. The optimizer genuinely does rank payment-method combinations;
the drift was only ever in what the executor may dispatch. The two sections
now express the evaluable/executable split rather than contradicting it.

**Structural note that matters more than the drift.** There is no
`method_change` action type anywhere in the system. The candidate vocabulary
is `do_nothing | retry | reminder | payment_link | escalate`
(`data_factory/candidate_generation.py:32`). A method change is represented as
`action_type="retry"` carrying a `method` different from the opportunity's
current one, flagged on the candidate dict as `method_changed: True`
(`candidate_generation.py:154`), and emitted only for root causes in
`METHOD_CHANGE_RELEVANT_ROOT_CAUSES` (`expired_card`,
`authentication_failed`).

The consequence is that the boundary currently holds *by absence, not by
enforcement*: `decide_action()` never reads a candidate today — it emits a
bare action string — so no `method` attribute exists anywhere near the
executor. **The moment Phase 5 passes ranked candidates into
`decide_action()`, that attribute becomes reachable, and a method-change retry
would be dispatchable as an ordinary retry.** Phase 5 must therefore construct
the exclusion in the same change that creates the exposure. It also means the
existing gate test, which substring-searches source for the literal
`"method_change"`, is searching for a token that structurally cannot appear —
see section 2.

### 0.2 `blocked_max_retries` — EXECUTION_PLAN ↔ STATE_AND_DECISIONS

| Document | Says |
|---|---|
| `EXECUTION_PLAN.md:103` (before Phase 5) | includes `blocked_max_retries` in the closed outcome vocabulary |
| `STATE_AND_DECISIONS.md:109-111` | "`blocked_max_retries` explicitly dropped", with a recorded rationale and a "what breaks if reversed" clause |

Unlike 0.1, the later and more specific document here is
`STATE_AND_DECISIONS.md`, not `EXECUTION_PLAN.md` — so the precedent from 0.1
does not transfer, and the conflict was escalated rather than resolved by
analogy. Resolved by explicit ruling in favour of
`STATE_AND_DECISIONS.md`; `EXECUTION_PLAN.md:103` was amended to match (that
one line only). Full analysis in section 1.

Also noted: the enumeration at `STATE_AND_DECISIONS.md:109` is itself
partially stale — it names five values and omits `flagged_manual_review`,
which the later Stage 3 LLM confidence gate added. Its *ruling* on
`blocked_max_retries` stands; its *list* does not.

---

## 1. `blocked_max_retries` — analysis and resolution

### 1.1 What was found

`blocked_max_retries` was a declared member of the closed compliance
vocabulary with **no producer anywhere, in any commit in the project's
history**. Checked at every revision:

    294f517  emits=0  mentions=1        078ca79  emits=0  mentions=1
    b512628  emits=0  mentions=1        7c9fc24  emits=0  mentions=1
    e690789  emits=0  mentions=1        866e478  emits=0  mentions=1

The single mention in each revision is `decide_action()`'s docstring. At the
first commit (`294f517:118`) the max-retries branch already returned
`action_type="stop", outcome="executed"`, identical to today.

### 1.2 What the max-retries branch actually does

`decide_action.py`:

```python
if contact_count >= MAX_RETRIES:
    return {"action_type": "stop", "allowed": True,
            "reasoning": f"Max {MAX_RETRIES} contact attempts reached. ...",
            "outcome": "executed", "triggered_by": "rule"}
```

`execute_action.py` then treats `stop` as a terminal business resolution:
`status='stopped'`, `resolved_at`, `recovered_bool=0`,
`partial_recovery_amount=0`, `resolution_type='stopped'`.

### 1.3 Is `stop` doing double duty?

Today, no. `action_type == "stop"` appears twice in `decide_action()`:

- the `already_stopped` guard — `allowed=False`,
  `outcome='blocked_already_stopped'`. A *reflection* of an existing terminal
  state; `execute_action()` writes nothing for it.
- the max-retries branch — `allowed=True`, `outcome='executed'`. **The sole
  producer.**

Nothing in `api/`, `llm/`, or `engine/` elsewhere produces a `stop` decision,
and the optimizer never proposes one (`stop` is not in the candidate
vocabulary). So `WHERE action_type='stop' AND outcome='executed'` is currently
an exact, unambiguous query for "terminated by the retry ceiling". No
information is lost by the absence of a distinct outcome code.

**Forward-looking risk, and why it shapes the W3 design.** The
EXECUTION_PLAN's Phase 5 executable vocabulary includes `stop`. If the
fallthrough loop emits `stop` when it exhausts all candidates, `stop` acquires
a second meaning — "no compliant candidate was available" — and that query
silently becomes wrong, conflating budget-exhausted with nothing-to-do. The
double duty does not exist today, but Phase 5 is the phase that could create
it. **W3 therefore routes candidate exhaustion to `flagged_manual_review`, not
to `stop`.** That choice is load-bearing, not stylistic.

### 1.4 Why the value was removed rather than the branch added

Adding a real `blocked_max_retries` branch would be a live regression, not a
clarification. By the `allowed == (outcome == 'executed')` invariant — enforced
behaviourally at `tests/test_compliance_regression.py:522` — such a branch must
carry `allowed=False`. `execute_action()` then skips its
`if decision["outcome"] == "executed":` block entirely, so:

- no `recovery_executions` row is written,
- **the opportunity is never closed**,
- `core_loop` re-selects it (`WHERE status IN ('open','recovering')`) on the
  next cycle, `contact_count` is still ≥ `MAX_RETRIES`, and it re-emits the
  same outcome **every cycle, indefinitely**.

Making it safe would require a blocked-prefixed outcome that nonetheless
performs a terminal state transition — breaking both the "blocked outcomes
have no side effects" semantic and the `allowed`/`outcome` agreement
invariant. That is exactly what `STATE_AND_DECISIONS.md:111` means by
"would require restructuring the stop-transition logic".

There is also a semantic argument: the other five `blocked_*` codes all mean
*"not now, the opportunity stays open, try later."* Max-retries means *"never
again, the opportunity is closed."* Filing a permanent termination under the
same prefix makes the vocabulary less auditable, not more.

And doing it inside Phase 5 would deliberately break the W1 golden corpus on
two scenarios, contradicting this phase's own backward-compatibility gate.

### 1.5 Dependency sweep before removal

No downstream consumer referenced it as a distinct bucket:

- **Specs:** `SoT.md` 0 occurrences; `Phase_Acceptance_Test_Gates.md` 0. The
  entire `blocked_*` vocabulary appears on exactly one line per document
  (`EXECUTION_PLAN.md:103`, `STATE_AND_DECISIONS.md:109`). No Phase 6/7/8
  section names a compliance outcome bucket at all — every "outcome" mention
  there refers to *business* outcome (recovered / partially recovered / lost).
- **API:** the only hardcoded outcome literal anywhere is
  `flagged_manual_review` (`api/queries.py:191,198`). `get_cases()` takes
  `outcome` as an opaque pass-through query param.
- **Frontend:** the vocabulary is enumerated in exactly two places —
  `frontend/src/components/FiltersBar.jsx:3-11` (filter dropdown) and
  `frontend/src/statusColors.jsx:15-22` (`OUTCOME_STYLES`) — and **both omit
  `blocked_max_retries`**, listing precisely the six emitted values.
- **Tests:** both consumers (`test_compliance_regression.py:515`,
  `test_phase0_bootstrap.py:325`) are membership/subset checks. No exact-set or
  length assertion exists, so removing an unemitted value can only tighten
  them.
- **Data:** `recovery_decisions` is empty; 0 rows carry the value.

Three independent sources agreed the real vocabulary is **six**, against seven
declared: the W1 golden corpus (measured across 25 scenarios), the frontend's
hand-written enumerations, and the `STATE_AND_DECISIONS.md:109` ruling. The
frontend was in fact the most accurate enumeration in the project — more so
than `STATE_AND_DECISIONS.md:109`, which predates `flagged_manual_review`.

### 1.6 What changed

| File | Change |
|---|---|
| `backend/db/db.py` | `blocked_max_retries` removed from `DECISION_OUTCOMES`, with the rationale recorded inline |
| `backend/engine/decide_action.py` | removed from the docstring's `outcome` type-union |
| `EXECUTION_PLAN.md:103` | removed from the closed-vocabulary table (that one line only) |
| `backend/tests/test_phase5_regression.py` | two docstrings corrected; assertions unchanged |

**No behaviour changed.** `decide_action()`'s logic is untouched; the W1
golden corpus passes unmodified. `test_blocked_max_retries_remains_unreachable`
is deliberately retained — the removal is what makes accidental reintroduction
plausible, since nothing now rejects the string at write time.

---

## 1a. PROJECT CLOSEOUT LIST — must be resolved before final sign-off

> **Carry this section forward verbatim into every subsequent phase's notes
> and hand-off document.** It is not a Phase 5 to-do list. Items here are
> deferred by explicit ruling, not forgotten, and each one must be either
> resolved or formally retired with a recorded reason before the project is
> signed off. An item may leave this list in exactly three ways: it is fixed,
> a ruling retires it and the reason is written down here, or the finding
> itself is withdrawn as erroneous with the diagnosis recorded. It may never
> leave by being absorbed into a "known failures" count and stopping being
> counted.
>
> **Status as of 2026-09-04: two open items (C3, C4).** C1 fixed by
> re-implementation, C2 withdrawn as a probe artefact, C3 opened by W6, C4
> opened by W7. Resolved entries stay below with their evidence rather than
> being deleted — a closeout list that erases its own history cannot be
> audited. Add new items here as they are found.

| # | Item | Status | Ruled |
|---|---|---|---|
| C1 | `test_higher_true_incremental_value_ranks_above_lower` encoded the retracted methodology -- probability ground truth compared against rupee-space model output on constructed contexts. | **RESOLVED 2026-09-03 by re-implementation, not retirement.** Replaced by `tests/test_phase4_ranking_correctness.py`. Measured 0.8886 / 0.8966 at the locked floor and 0.9511 / 0.9489 at the G7 floor; both bars met at their own floors. Evidence below. | 2026-09-03 |
| C4 | **The first `opportunity_lock` hold in any process is ~780 ms, not ~6 ms.** `decide_action()` loads the ML model lazily (`_load_ml_model()`), and the first call in a process happens inside `decide_action()` — which the pipeline calls INSIDE the lock. So the very first hold after a restart includes a `joblib.load`. Measured from a cold process, 8 consecutive opportunities: hold #1 **779.35 ms**, then 9.75 / 6.23 / 6.00 / 7.23 / 6.46 / 5.89 / 6.41 ms — a **121.5x** spike, and the warm figures match the 5.88 ms p50 recorded in `opportunity_lock.py`. Against `db.BUSY_TIMEOUT_MS = 5000` this puts the first cycle after any restart in the same regime the optimizer was banned from the lock for: roughly the seventh concurrent worker fails with "database is locked". | **OPEN — found while measuring W7's lock hold, deliberately not fixed in W7.** Not introduced by W7: the pre-W7 sequence shows it too (p95 946 ms on the same machine before the model warms). The fix belongs to the compliance authority's loading strategy — warming the model before the lock is entered, or at import — and changing when a frozen-adjacent module loads its artifact needs its own ruling. Pinned as a documented limitation by `test_the_first_lock_hold_is_inflated_by_the_lazy_model_load`, which asserts the limitation rather than a guarantee, and skips with a printed reason when the model is already resident. | 2026-09-04 |
| C3 | `payment_link` is dispatchable but has no delivery path. `SoT.md:63` names it among the tools the rule engine dispatches; `phase5_config.EXECUTABLE_ACTIONS` includes it; `decide_action.CONTACT_ACTIONS` treats it as customer contact and applies the contact-hours window to it. But `deliver_message.ELIGIBLE_ACTIONS` is `{"retry", "reminder"}`, so a dispatched `payment_link` writes **no `messages` row and produces no customer-visible artifact at all**. | **OPEN — found while planning W6 (ruling A8, 2026-09-03), deliberately not fixed in W6.** Fixing it means deciding what a payment-link message *is* (it must carry a link this system does not mint), which is a product question, not a dispatch question. W6 neither widened nor narrowed the gap; its idempotency proof deliberately uses `reminder` because `payment_link` would report zero customer-visible actions whether or not the dispatcher worked. | 2026-09-03 |

### C1 detail — ranking correctness, re-implemented

**What was wrong.** The old test compared across measurement spaces:

    truth = a["analytic_p"]         - b["analytic_p"]          # probability
    model = a["incremental_amount"] - b["incremental_amount"]  # rupees

Those orderings are permitted to disagree — `E[amount|recovered]` varies per
candidate, the ~16% rupee-space pair-order sensitivity disclosed in
`PHASE4_NOTES` §8.6. Phase 4 §8.2 had already isolated the fault exactly: same
contexts, same ground truth, **0.958 in probability space vs 0.812 in rupee
space**. The comparison axis was the bug. The test also ran on hand-constructed
contexts — the same error that produced the retracted C2 finding.

**What replaced it.** `tests/test_phase4_ranking_correctness.py` implements
`locked_thresholds.json / phase3_temporal / ranking_pair_definition` verbatim —
a definition locked **2026-08-30T15:32:43Z**, before Phase 4 began and long
before this measurement was conceived. Like-for-like: probability-space
treatment effect on both sides, both against the same `do_nothing` baseline
within the same case, on frozen held-out data. The pairing and effect
computation are **reused** from `evaluate_outcome_model.compute_effects()` —
the same function the trusted `phase3_temporal` gate calls — rather than
reimplemented, so the two gates cannot drift into measuring different things.

**Measured** (printed on every run, pass or fail):

| holdout | floor 0.05 (locked, bar 0.85) | floor 0.12 (G7, bar 0.90) |
|---|---|---|
| temporal (held out in time) | **0.8886** (2745/3089) | **0.9511** (700/736) |
| calibration (unseen cases) | **0.8966** (2828/3154) | **0.9489** (705/743) |
| seed-43 (unseen world, measured not asserted) | 0.8921 (17888/20052) | 0.9568 (4054/4237) |

**No threshold was loosened, and the test asserts at both operating points so
the result cannot depend on which bar is picked.** The old test carried a 0.90
bar borrowed, by its own comment, from "Phase 3's own 0.90 bar" — which belongs
to `ground_truth_treatment_effect`, a bucket-level direction measurement, not
pairwise within-case ranking. Agreement rises monotonically with the effect
floor (0.889 → 0.928 → 0.951 → 0.991 on the temporal holdout at floors
0.05/0.08/0.12/0.20), so a bar is only meaningful alongside the floor it was set
at. This implementation reproduces Phase 4's independently-reported 0.966/0.967
regime at G7's own 0.12 floor, which is what validates it.

A third test asserts the monotonicity itself: if agreement ever fell as the
generator separated pairs *more* decisively, the measurement would be suspect.
| C2 | ~~The joint outcome model has learned the wrong sign for `payment_history_score`.~~ | **RETRACTED 2026-09-03.** Not a model defect. The finding was an artefact of a diagnostic probe extrapolating outside the training support. Full diagnosis below; no fix required, nothing to carry forward. | 2026-09-03 |

### C2 — RETRACTED. The finding was a probe artefact, not a model defect.

**Claim as originally raised:** the joint outcome model had learned the wrong
sign for `payment_history_score`, inverted in 75–99% of cases across four
conditions, against a generator that is monotonically positive in it.

**That claim was wrong.** The model is correct within the range of data it was
trained on. The inversion was produced by the diagnostic probe swinging the
feature to values that occur **zero times** in the training corpus.

#### The diagnostic trail

**1. Generation — correct, re-confirmed.** `payment_history_score` enters the
outcome logit with a net coefficient of **+0.7 per unit**:

    liquidity_state      = clip01(normal(0.3 + 0.5 * payment_history_score, 0.22))
    recovery_willingness = clip01(0.5*liquidity_state + 0.4*customer_responsiveness + noise)
    hidden_state_term    = 0.9*liquidity_state + ... + 1.0*recovery_willingness
    z = ... + hidden_state_term + ... ;  p = sigmoid(z)

    d(z)/d(phs) = 0.5 * (0.9 + 1.0*0.5) = +0.7

**2. Feature engineering — no bug.** `payment_history_score` is a plain
`float()` passthrough into the feature row (`outcome_features.py:325`), with no
scaling, encoding, or transform of its own before the shared preprocessor.
Training and serving build the row through the *same* function, so no
train/serve skew is possible here.

**3. Correlation — no confounder.** In the training corpus (20,044 rows,
2,095 cases): `corr(phs, past_recovery_rate) = +0.0231` — the two customer
scores are drawn from independent Beta distributions and are duly independent
in the data, so the suppressor/collinearity hypothesis is dead. The largest
absolute correlation of `phs` with any other numeric feature is `days_overdue`
at **−0.0456**. Nothing to latch onto.

**The corpus itself carries the correct positive relationship**, monotonically:

| phs quintile | mean phs | recovered rate |
|---|---|---|
| 0 | 0.3904 | 0.8880 |
| 1 | 0.5463 | 0.8880 |
| 2 | 0.6310 | 0.8985 |
| 3 | 0.7213 | 0.9077 |
| 4 | 0.8335 | 0.9101 |

`corr(phs, recovered) = +0.0325`. Note the magnitude: a **2.2 percentage-point**
swing on a 0.8984 base rate. That is the entire true signal available.

**4. Training script — no bug.** `train_outcome_model.py` builds a
`ColumnTransformer` addressed **by column name**, not position
(`OneHotEncoder` on categoricals, `SimpleImputer(median)` + `StandardScaler` on
numerics), so a column-ordering or feature-list-drift bug is structurally
excluded. `StandardScaler` cannot flip a sign. `feature_columns` is persisted
from `feats.ALL_FEATURES`, the same list serving reads.

**5. The actual cause — extrapolation outside the training support.**

Measured distribution of `payment_history_score` in the training corpus:

    p0    0.2031      p50   0.6284      p95   0.8714
    p10   0.4101      p75   0.7440      p99   0.9212
    p25   0.5150      p90   0.8246      p100  0.9236

    fraction of corpus below 0.05 : 0.00000
    fraction of corpus above 0.95 : 0.00000

**The probe swung 0.05 → 0.95. Both ends lie outside the observed data
entirely.** Partial dependence over 400 real corpus rows shows precisely what
a gradient-boosted tree does there — a flat leaf below the minimum, and a cliff
above the maximum:

    phs    mean p_recovery    delta        in support?
    0.01     0.88803                       no
    0.05     0.88803         +0.00000      no      <- flat: no training data
    0.20     0.88803         +0.00000      no
    0.30     0.88825         +0.00022      yes
    0.50     0.88740         +0.00336      yes
    0.70     0.91164         +0.02262      yes
    0.90     0.91380         +0.00513      yes
    0.95     0.85047         -0.06332      no      <- the entire "inversion"
    0.99     0.85047         +0.00000      no

That **−0.063 cliff at 0.95 is the −0.063 mean delta originally reported.** The
whole finding was that one step.

**6. Confirmation on the live path.** The same 92 live opportunities, the same
wired mapping, only the swing range changed:

| swing range | raises (correct) | mean Δp |
|---|---|---|
| p25 → p75 `[0.515, 0.744]` | **92/92 (100.0%)** | **+0.03948** |
| p10 → p90 `[0.410, 0.825]` | **87/92 (94.6%)** | **+0.03452** |
| p0 → p100 `[0.203, 0.924]` | 19/92 (20.7%) | −0.03542 |
| `[0.05, 0.95]` (original probe) | 19/92 (20.7%) | −0.03542 |

Within the interquartile range the model is directionally correct on **every
single opportunity**. The p0→p100 and extrapolated rows being *identical* is
itself the proof of flat extrapolation: 0.05 and 0.203 produce the same
prediction, as do 0.924 and 0.95.

The in-support magnitude is also right, not merely the sign: the model moves
**+0.0229** across p10→p90, against **+0.0221** in the corpus between phs
quintile 0 and 4. It learned the relationship at close to the correct size.

#### Verdict

No defect. No fix required. Nothing to retrain. `ml/inference.py`,
`outcome_features.py` and the model artifacts were read and introspected only;
none was modified.

#### Why the probe was wrong, and what would have caught it sooner

The probe held every other feature at a real opportunity's values and swung one
feature between two constants chosen for being "extreme" — 0.05 and 0.95 —
without first checking whether either value appears in the training data.
Neither does. A tree ensemble has no defined behaviour outside its training
support; whatever it returns there is an artefact of leaf placement, not a
learned relationship.

**This is the same class of error as the retracted Phase 4 G7 finding
(closeout C1): a conclusion drawn from constructed inputs that the model was
never fit on.** Two such errors in this project now. The lesson is cheap and
mechanical: *any* single-feature directional probe must report the feature's
training-support range alongside its result, and must swing within it.

#### Relation to the disclosed seed-43 temporal ranking gap — unrelated

`PHASE3_HANDOFF.md` §2(c) records a seed-43 temporal ranking agreement of 0.813
against a 0.85 bar, root cause undiagnosed. It is **not** the same issue:

- **Different axis.** The seed-43 gap is concentrated in *action × event_type*
  buckets (`reminder|checkout_abandoned` 49.9%, `reminder|invoice_overdue`
  21.3%, `payment_link|invoice_overdue` 20.2%). C2 concerned a *customer
  attribute*, which is orthogonal to those.
- **Different seed behaviour.** The gap is seed-specific (42: 0.891,
  44: 0.923, 43: 0.813). The C2 artefact reproduced on seed-42 data — the
  training pool and the seeded live world — so it was not seed-conditional.
- **Decisively:** C2 dissolves entirely under correct probing, so there is no
  defect left for the two to share a root cause through.

The seed-43 gap remains open and undiagnosed on its own terms, with its
existing revisit trigger (Phase 6/7 live data showing the same action×context
pattern). This investigation says nothing about it either way.

---

## 1b. W3/W4 rulings — the optimizer-driven pathway

All approved 2026-09-02. Recorded because each one is a compliance or
authority decision that a later reader would otherwise have to re-derive from
the code.

| # | Ruling | Where enforced |
|---|---|---|
| R-W3-1 | Fallthrough is permitted **only** when the baseline outcome is `blocked_contact_hours`. Every other blocking rule is opportunity-scoped and blocks all candidates equally, so falling through one would be the ranked path overturning a compliance decision. | `decide_action()` guard; `test_a_blocked_opportunity_is_never_unblocked_by_a_ranked_list` |
| R-W3-2 | Substitution is restricted to a baseline `action_type` of `retry` or `reminder`. Keeps the optimizer out of auto-escalation, the attempt ceiling's `stop`, and deeply-overdue escalation without re-deriving which branch fired. Deliberately more conservative than "the optimizer chooses whenever compliance allows"; **not widened for now**. | `decide_action()` guard; the three not-overridden tests |
| R-W3-3 | `payment_link` is customer contact and is gated by the contact window. It never reached that check pre-Phase-5 because it could not be a hardcoded `default_action`. SoT section 7 scopes the window to customer-facing actions and exempts escalation as internal routing. | `CONTACT_ACTIONS`; `test_payment_link_respects_the_contact_window` |
| R-W3-4 | On exhaustion with a blocked baseline, the **specific block** is recorded, not the generic `EXHAUSTION_OUTCOME` -- "outside contact hours" tells the audit trail more than "nothing was executable". | `_decide_action_from_ranked()`; `test_an_all_contact_list_outside_the_window_keeps_the_specific_block` |
| R-W3-5 | `ml_recovery_probability` keeps **one provenance** across both paths: the legacy scorer's read on the action actually selected. The optimizer's `predicted_p_treated` is deliberately not copied in -- it lives in `recovery_candidates`, reachable via `candidate_id`. Costs one extra call to the small legacy model; avoids giving one column two meanings. | `_decide_action_from_ranked()` |
| R-W4-1 | The runtime kill switch `OPTIMIZER_PATHWAY_ENABLED` is separate from the per-entry-point enablement table. The table answers "should this caller compute a ranked list"; the switch answers "if one is supplied, may the rule engine act on it". The switch lives at the authority boundary so a direct caller of `decide_action()` cannot bypass it. | `phase5_config`; `decide_action()`; `tests/test_phase5_disable_path.py` |

**Implementation note, W3.** `_within_contact_window()` knowingly duplicates
the window condition inside the hardcoded body, because that body must stay
literally unmodified and extracting a shared helper would have been a
refactor. The duplication is pinned by a 24-hour equivalence test, so a change
to one that is not mirrored in the other fails rather than diverging silently.

**Implementation note, W4.** The kill switch is read as a module attribute,
never `from`-imported. A from-import binds at import time, and every later
flip would be silently ignored -- the disable path would look present and be
inert. `test_the_kill_switch_is_not_bound_at_import_time` asserts this
statically.

**A design error worth recording.** W3's first implementation returned *every*
blocked baseline unchanged, which silently disabled the fallthrough in the one
case it exists for. Two tests caught it before review. The corrected rule is
R-W3-1 above.

---

## 1d. Network health at serving time — closed on the data side, blocked on the wiring

The Phase 4 hand-off recorded network health as "unavailable at serving time"
and filed it as a Phase 6/7 closure item, on the reading that it depended on
data the system did not have. That reading was wrong: this project is
synthetic end to end by design, with no external integration now or ever, so
the gap was always ours to close.

**Closed in Phase 5 (data side).**

| Change | File |
|---|---|
| `bank` and `psp` columns added to `payments` | `db/db.py` |
| every seeded payment assigned a `(bank, method, psp)` triple | `data/generate_seed_data.py` |
| a network-health series seeded for every channel | `data/generate_seed_data.py` |
| `load_bank_health_observations()` | `db/db.py` |

Vocabularies and the health generator are **imported** from `data_factory`
(`entities.BANKS/PSPS`, `bank_health_timeseries.generate_series_for_channel`,
the `baseline` calibration profile), not reimplemented, so a seeded payment
names the same channels, drawn the same way, as the training rows. The one
restatement is the two-line "pick a channel carrying this method" rule, whose
original lives in a private function inside a frozen module.

**Strictly additive, verified.** Channel selection and health generation draw
from a dedicated generator (`CHANNEL_RNG_SEED`), never the main `rng`. Against
the pre-change generator: `merchants.json`, `customers.json` and
`opportunities.json` byte-identical; `payments.json` identical except the two
added fields; 177/177 payments populated.

A first attempt drew from the main stream and silently regenerated the whole
seed set, which surfaced as a new failure in
`test_do_nothing_legitimately_wins_at_least_one_real_ranking` -- do_nothing
stopped winning any ranking because the underlying cases had changed. Fixed
before merge; recorded because "adding a field" quietly changing every other
field is exactly the class of defect a seed generator can hide.

**Still blocked (wiring side).** `network_health_known` is still `0.0` on every
live scoring, because `optimize.py` hardcodes `bank=None`, `psp=None`,
`decision_time_hours=0.0` (module docstring, lines 62-66) and is a **frozen
Phase 4 input**. Two things are needed and neither is Phase 5's to decide:

1. **Unfreeze `optimize.py`** enough to read the channel off the latest
   payment. Three lines.
2. **Define a unix → simulated-hour mapping.** `bank_health_observations`
   windows are simulated hours from 0; a live opportunity has a unix
   timestamp. There is no defined correspondence, and whichever is chosen
   determines which health window a live scoring reads -- a model-input
   decision, not a mechanical one.

Proven sufficient in the meantime: attaching the real channel to a context by
hand yields `network_health_known = 1.0` with live values
(`health_score=0.687`, `success_rate=0.839`, `timeout_rate=0.110`). The data
is correct and complete; only the frozen wiring stands between it and the
model.

`HEALTH_HORIZON_HOURS = 168` is **provisional and deliberately not** the Data
Factory's `DEFAULT_HORIZON_HOURS` (2880). Measured seed-generation cost:
2880h ≈ 1.4s, 720h 347ms, 168h 92ms, none 19ms -- and the seed set is
regenerated once per test through the `seed_data_dir` fixture. 168h is
defensible only while the series is unread by the live path; it must be
revisited alongside the mapping ruling.

---

## 1e. Frozen-list exception — `optimize.py` network-channel plumbing

**Granted 2026-09-03. NOT YET EXERCISED** — held pending the unix → simulated-
hour mapping ruling, at the grantor's instruction, so the plumbing cannot land
without a correct mapping behind it.

| | |
|---|---|
| Frozen input | `backend/engine/optimize.py` (Phase 4 hand-off, section 4) |
| Scope of exception | the three hardcoded lines in `build_optimizer_context()`: `bank=None`, `psp=None`, `decision_time_hours=0.0` |
| Permitted change | read the real `(bank, psp)` off the opportunity's latest payment; supply a real `decision_time_hours` |
| NOT permitted | any change to candidate generation, scoring, EIV arithmetic, ranking, or persistence |

**Why it is narrow.** `bank`, `psp` and `decision_time_hours` are **not model
features** — verified against `outcome_features.ALL_FEATURES`, which contains
26 entries and none of these three. They are lookup keys only. So this change
can affect the model through exactly four features
(`network_health_score_rolling`, `..._success_rate_rolling`,
`..._timeout_rate_rolling`, `network_health_known`) and through nothing else.
That is what makes it plumbing rather than a scoring-logic change.

**Evidence backing it.** The seeded data is already proven sufficient: with the
channel attached by hand, `network_health_known` reads `1.0` with live values
(`health_score=0.687`, `success_rate=0.839`, `timeout_rate=0.110`), while the
same context through `optimize.load_context()` reads `0.0`. Section 1d.

---

## 1f. The unix → simulated-hour mapping — measured constraints

Three facts, measured, that any mapping has to respect.

**1. The horizon must be materially larger than the trailing window.**
`NETWORK_HEALTH_WINDOW_HOURS = 168.0` is the trailing span the rolling average
covers; observations are 4h windows. If the seeded horizon equals the trailing
span, every query averages from window 0 and the "recent health" semantics
collapse into a prefix average. Measured spread of the rolling `health_score`
across a horizon:

| horizon | windows/channel | rolling score spread | std | trailing/horizon |
|---|---|---|---|---|
| 168h | 42 | 0.0864 | 0.0258 | **1.00 — degenerate** |
| 720h | 180 | 0.1572 | 0.0429 | 0.23 |
| 2880h | 720 | 0.2119 | 0.0500 | 0.06 |

The current `HEALTH_HORIZON_HOURS = 168` compresses the signal ~2.5x and is
structurally wrong, not merely provisional. It must rise with whichever
mapping is chosen.

**2. Before the first window closes, the lookup is honest.** `as_of < 4.0`
gives `hi < 0` → `known=False`. Clean.

**3. Past the end of the series, the lookup is NOT honest.** For
`as_of > max(window_end)`, `hi` clamps to the last index and `lo > hi` is
corrected to `lo = hi` (`outcome_features.py:262`), so it returns **the single
final 4h observation, with `known=True`**, forever. Every opportunity past the
horizon then reads an identical constant while the feature asserts the data is
good. This is strictly worse than `known=0`, because a constant that claims to
be real is indistinguishable from real data the model can learn from. Any
mapping that can walk off the end of the series inherits this failure mode.

---

## 1g. A Phase 4 gate test whose limitation we deliberately closed

`test_phase4_optimizer.py::test_live_context_has_no_network_health_and_says_so_explicitly`
**failed when the network-health wiring landed. That was the test working, not
a regression.**

Phase 4 wrote it to pin a disclosed limitation rather than let it pass
silently — the live context had no `bank`/`psp` and every scoring ran at
`network_health_known = 0.0`. Phase 5 closed exactly that gap, by approved
ruling. The test objected, which is what a tripwire on a pinned property is
for.

**Amended 2026-09-03**, dated and evidence-backed, and **renamed** to
`test_live_context_carries_network_health_and_says_so_explicitly` since
"has_no" no longer described it. Assertions inverted:

| before | after |
|---|---|
| `context["bank"] is None and context["psp"] is None` | `... is not None` |
| `row["network_health_known"] == 0.0` | `== 1.0` |
| `row["network_health_score_rolling"] is None` | `is not None` |

**Inverted, not deleted.** The property stays pinned in its new direction. The
episode is itself the argument for keeping such tests: had it not existed,
closing the gap would have changed the optimizer's model inputs across every
live scoring with nothing objecting. That is also why section 1f's variance
tripwire was added on the other side of the same feature.

**A second failure in the same run was mine**, not the product's:
`test_the_seeded_horizon_and_the_mapping_modulus_are_one_constant` used `is`
to compare two ints. It held at 2880 by accident and broke at 720 — neither is
in CPython's small-integer cache, so identity was never guaranteed. Corrected
to `==`, plus an AST check that the seed generator *imports* the constant
rather than defining a second literal, which is the property the test was
actually reaching for.

---

## 1c. W5 findings — the write side

### execute_action() is NOT idempotent at the call level

Measured, not assumed. Calling `execute_action()` twice with the same decision
dict produces **two decisions and two executions**:

    call 1 decision_id      : 1
    call 2 decision_id      : 2
    recovery_decisions rows : 2
    recovery_executions rows: 2

The `UNIQUE` index on `recovery_executions.decision_id` does **not** prevent
this, because each call mints a *new* decision row to hang the execution off.
What the index does prevent, verified separately, is a second execution row
for an existing decision:

    second execution row for the SAME decision_id: REJECTED
      -> UNIQUE constraint failed: recovery_executions.decision_id

**Why this is safe for W6 as designed, and what would break it.** The
dispatcher advances an execution row that already exists (an `UPDATE`), rather
than calling `execute_action()` again. The idempotent-dispatch gate is
therefore satisfiable through the index. If W6 is ever implemented by
re-calling `execute_action()`, an idempotency key on the decision becomes
mandatory. Written down at
`test_calling_execute_action_twice_creates_two_decisions_not_one`, which
asserts the limitation rather than a guarantee that does not exist.

Not fixed in W5: adding an idempotency key is outside the step's scope and
would change the decision contract. Flagged, not silently absorbed.

### Foreign keys are enforced

`PRAGMA foreign_keys` is on, so `recovery_decisions.candidate_id` must
reference a real `recovery_candidates` row. W5 relies on this: an invented
candidate reference raises `IntegrityError` rather than being coerced to NULL,
because a decision claiming to come from a candidate that was never scored is
a defect worth surfacing.

### The do_nothing defect, before and after

Before, a `do_nothing` decision reached the executed branch and
`EXECUTION_STATE_MAP.get(action, "executed")` hit its default:

    recovery_executions rows: 1
      -> {'state': 'executed', 'scheduled_for': None, 'executed_at': 1788376460}

After:

    recovery_decisions rows : 1
    recovery_executions rows: 0

The guard is keyed off `EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS` rather than the
literal `"do_nothing"`, so anything added to that list is covered
automatically. Note this defect was never reachable *through* `decide_action()`
-- the ranked path skips evaluable-only actions -- but `execute_action()` is a
public function and was writing fabricated execution records for anyone who
called it directly.

### Two permanent-gate amendments, both dated 2026-09-02

`test_executor_action_set_matches_the_decider` was pinned to the four
pre-Phase-5 actions and tripped when `payment_link` was added, exactly as the
Phase 5 plan predicted it would. Amended to assert `STATUS_MAP` against
`phase5_config.EXECUTABLE_ACTIONS` -- the declared vocabulary -- instead of a
second hardcoded literal. **Strictly stronger than what it replaced:** the
executor and the declaration can no longer drift in either direction, and
widening the vocabulary now requires a visible edit to a config file whose own
tests assert it against `EXECUTION_PLAN.md`. The bar was not moved to obtain a
pass.

`test_payment_link_is_the_only_declared_action_the_executor_still_lacks` was
the W2 forcing function. It tripped with `executor gap changed: []` and was
tightened, per the instruction it carried, to assert the gap is empty in both
directions.

---

## 1h. W6 — scheduled dispatch, and the three defects found while planning it

`engine/dispatch_scheduled.py` is new. Reviewing W5's write side against what
a dispatcher would actually have to do surfaced three defects that had to be
fixed before the sweep could be correct at all. Each was reproduced *before*
being fixed, and the pre-fix output is recorded here rather than described.

### A1 — cooldown counted decisions, not contacts

`execute_action()` writes `recovery_decisions(action_type='reminder',
outcome='executed')` at **schedule** time, before anything is sent.
`decide_action()` built `contact_history` from exactly that predicate, so a
scheduled-but-unfired action counted as a contact already made.

Reproduced before the fix, a 4h-scheduled reminder revalidated at its due
time:

    Cooldown active. 20.0h remaining before next contact allowed.

The scheduling decision blocking the very action it scheduled — which makes
the `4h` timing **structurally undispatchable**. Through the same counter,
three unfired scheduled reminders returned:

    Max 3 contact attempts reached. Stopping further automated contact.

so the customer's entire contact budget was consumed before any contact
happened.

**The fix** (`_undelivered_decision_ids()`) is phrased as an *exclusion*, and
that phrasing is the whole design. The obvious formulation — count only
decisions whose execution reached `'executed'` — silently breaks every
pre-Phase-5 row, because the golden corpus inserts decision rows with **no
execution row at all** and all 25 scenarios would stop counting as contact.
So a decision counts as contact **unless** its execution row exists and names
a state in `phase5_config.CONTACT_NOT_YET_DELIVERED_STATES`. Absence of
evidence is treated as contact made, the safe direction for a compliance rule.

No threshold moved: cooldown is still 24h and the ceiling is still 3. Only the
question "has this contact happened" is now answered from the table that owns
it.

**An honest limit on the backward-compatibility proof.** The golden corpus
reproduces at 0 differing fields, which is what the ruling required — but the
corpus **cannot detect this class of change in either direction**. Running the
inverted predicate as a negative control, the corpus still passed 5/5 while
four directional tests failed, because no corpus scenario carries an execution
row. Corpus identity therefore proves the amendment *disturbs nothing
pre-existing*; it does **not** prove the amendment is correct. The correctness
evidence is `tests/test_phase5_revalidation.py`, which the same negative
control shows is sensitive in both directions — it catches both "stopped
counting a real contact" and "started counting an unfired one".

### A2 — the contact window could not be revalidated at all

`phase5_config.DISPATCH_REVALIDATES_VIA_DECIDE_ACTION` was declared at W2 to
stop a 3-day-scheduled action firing at 3am. It did not do that. Both window
implementations — `_within_contact_window()` and the hardcoded branch — read
the local hour of the opportunity's `created_at`, which does not change
between schedule time and due time, so revalidating returned the **identical**
verdict it gave at scheduling. The 9pm–8am contact ban was unenforceable for
every scheduled action.

**The fix** is an `as_of` evaluation clock on `decide_action()`. With `as_of`
None the expression is byte-for-byte the pre-amendment one, which is what the
corpus pins; the dispatcher passes the moment the action would actually fire.
The W2 comment claiming the flag already closed this has been corrected in
place rather than quietly reinterpreted.

This one bit immediately and usefully: the first full run of
`tests/test_phase5_dispatch.py` happened at 23:00 local and **every**
scheduling test failed, because every due action was correctly cancelled as
`blocked_contact_hours`. The tests were wall-clock-dependent, not the code.
They now pin `now` through a `now_in_window()` helper — the `now` counterpart
of the suite's existing `recent_in_window_ts()`.

### A7 — the customer was contacted at schedule time

`deliver_recovery_message()` gated only on `outcome == 'executed'` and the
action type. But `outcome` is a **compliance** verdict — it says the action
was permitted, and it is written the moment the action is approved — while
whether the action has fired lives in `recovery_executions.state`. For a
scheduled action the two disagree for the whole scheduling window.

Measured before the fix:

    execution state      : scheduled
    executed_at          : None
    scheduled_for        : +4h from now
    delivery result      : delivered=True status=ok
    agent messages sent  : 1     <-- while the execution is 'scheduled'

and the dispatcher would have sent a second at due time. After:

    agent messages sent  : 0

Reading a lifecycle answer out of the compliance field is exactly the
conflation the "Execution separation" gate and the five-distinct-concepts
invariant forbid, so this is a correctness fix, not a feature. Delivery now
requires the execution to be in `DELIVERABLE_STATES = ('dispatched',
'executed')` and **fails closed** when the caller cannot name the execution:
a missed message is visible in the returned status and costs one delayed
follow-up, while a duplicate contact is not recoverable. All three production
entry points now pass `decision_id`, pinned structurally so W7's unification
cannot drop it.

### The dispatcher itself

Selection is `state='scheduled' AND scheduled_for <= now`, ordered
deterministically. Advancement is **only** compare-and-swap `UPDATE`s:

    UPDATE recovery_executions SET state = ?
    WHERE execution_id = ? AND state = ?

`rowcount == 1` means this sweep won the row; `0` means another sweep already
took it, and this one then does nothing at all. `execute_action()` is never
called and nothing is ever INSERTed — enforced by a static test, not by
convention, because that is the property the whole idempotency gate rests on.

The optimizer is not called anywhere in the module, so the ~650ms ranking call
can never land inside `opportunity_lock`'s ~6ms hold. The action was ranked
and authorised at schedule time; re-ranking at dispatch would be re-deciding.
`OPTIMIZER_ENABLED_BY_ENTRY_POINT["dispatch"]` is therefore inert by design
(ruling A9).

**Idempotency evidence.** Sequential double sweep — decisions 1→1→1,
executions 1→1→1, 1 agent message after two sweeps. Barrier-forced concurrent
sweeps, 25 trials × 2 workers on one due row — exactly 1 dispatch and 1
message in every trial (25 and 25 in total). Negative control with the CAS
predicate removed — **2 messages**, so the tests demonstrably detect the
failure they claim to prevent. The barrier test is the one that establishes
the property; this project has already recorded that a green concurrency test
is weak evidence by construction.

### A4 — the stuck-row question, answered explicitly

The sweep selects only `'scheduled'`, so a row left in `'dispatched'` is never
retried. Two ways to get there:

* **the process dies** between the claim and the completion;
* **`deliver_recovery_message()` raises** — an LLM failure, a persistence
  failure. The row is left in `'dispatched'` with `executed_at` NULL and the
  exception is returned in the result's `reason`, not swallowed.

**This is at-most-once by choice, and it is intentional for W6.** A claimed
row may already have reached the customer, so an automatic retry is precisely
the duplicate contact the CAS exists to prevent. Failing to send is
recoverable by a human; sending twice is not. `stuck_dispatches()` enumerates
these rows for exactly that review.

**What is *not* covered, and is a tracked follow-up rather than a silent
gap:** there is no alerting, no dashboard surface and no operator workflow
around `stuck_dispatches()` — it is a function nobody currently calls. A row
can sit stuck indefinitely with nothing drawing attention to it. That is
acceptable for W6, whose job is that the dispatcher be *correct*, and it is
recorded in section 2 below so it does not disappear.

### A6 — where an abandonment reason is recorded

Ruling A6 offered two options and approved "as proposed"; the durable one was
taken. `recovery_executions.state_reason` (nullable TEXT) records why the
dispatcher abandoned a queued action, because "every action the system takes
**or declines to take** is logged with a reason" and a return value that
vanishes with the process does not satisfy that.

It is deliberately **free text and never a `DECISION_OUTCOMES` token** —
putting a compliance-vocabulary value in the lifecycle table is the exact
conflation the Execution-separation gate forbids. A test asserts the column
never holds a bare compliance token.

Since `CREATE TABLE IF NOT EXISTS` leaves an existing table alone, a new column
would never reach a database file that predates it. `db.ADDITIVE_COLUMNS` plus
`_apply_additive_columns()` applies it idempotently. This is a deliberately
small mechanism, not a migration framework.

---

## 1i. W7 — shared pipeline unification

`engine/pipeline.py` is new. `run_recovery_pipeline()` is the single
`classify → optimize → authorize → execute → message` function, called by
`core_loop.py`, `trigger_event.py` and `handle_customer_reply.py`. The gate
requires this be verified *structurally* — "a single shared function is
called by all three entry points" — not merely by matching output.

### The drift the three had already accumulated (ruling W1)

They disagreed about what feeds `classify()`'s `error_reason`:

| entry point | passed |
|---|---|
| `core_loop` | `latest_payment.error_reason`, falling back to `opportunity.root_cause` |
| `handle_customer_reply` | `opportunity.root_cause` |
| `trigger_event` | the `root_cause` argument |

**Introduced by the Phase 1 schema split (`7c9fc24`), not designed.** Before
it, both loop entry points called the identical `classify(payment)`
(`e690789`). The commit message is bare, there is no Phase 1 notes document,
and the decision log contains no entry ruling on it.

**But `handle_customer_reply`'s choice turned out to be substantively right,
which reversed the initial recommendation.** `classification["root_cause"]`
is a **compliance input** on the reply path: `decide_action.py:571-586`'s
intent-mismatch gate fires only when intent arguments are supplied — only
that entry point does — compares `classification["root_cause"]` against the
LLM's `mentioned_reason`, and on conflict returns `allowed=False`,
`outcome="flagged_manual_review"`. Its own message reads *"Extracted intent
conflicts with **stored root_cause**"*, wording introduced in `e690789`,
**before** the split — independent corroboration older than the divergence.
On `core_loop` the gate never fires, so there the field is ML input and
message phrasing only, exactly as that module's docstring claims.

Unified on `opportunity.root_cause` with `latest_payment.error_reason` as a
NULL-fallback. **Behaviourally identical, and structurally so:** across all
150 seeded opportunities (64 `payment_failed`, 17 multi-attempt, 0 with no
payment row) there are zero cases where the two differ — because the only two
writers of a `payments` row, `generate_seed_data.py:322` and
`trigger_event.py:198`, both set `error_reason` from the opportunity's own
`root_cause`.

### Parity evidence

Measured **in one process, against two freshly-seeded databases, with
`time.time()` pinned**, so timestamps, autoincrement ids and
`ml_recovery_probability` are all directly comparable rather than normalised
away. The legacy sequences are held verbatim in the test file, copied from
`9ae5b77`, with the `git show` command to re-verify each in its docstring.

    batch parity        : 92 opportunities compared, 0 differing (tolerance 0)
      recovery_decisions : legacy 92  unified 92
      recovery_executions: legacy 86  unified 86
      messages           : legacy  1  unified  1
    trigger_event parity: 6 specs compared, 0 differing
                          (all 3 event types + both invoice branches + replay)
    reply parity        : 5 pairs compared, 0 differing
                          outcomes exercised: ['executed', 'flagged_manual_review']

The reply set deliberately includes the low-confidence and intent-mismatch
cases, so the branches that can **block** are compared, not just the ones
that act.

### The four hard constraints

1. **`opportunity_lock` adopted unchanged.** Not edited. Its public shape is
   pinned by a test. Which entry points use it is now the declared table
   `ENTRY_POINTS_USING_OPPORTUNITY_LOCK`, asserted **both ways** — the two
   that lock do, and `trigger_event` does not — with an import-time raise if
   anyone adds `trigger_event` to it.
2. **Optimizer outside the lock — measured, not just structural.**

       legacy (pre-W7): lock hold p50 28.06 ms   p95 31.79 ms
       optimizer OFF  : lock hold p50 25.21 ms   p95 30.57 ms | total p50  29.54 ms
       optimizer ON   : lock hold p50 23.85 ms   p95 25.92 ms | total p50 796.39 ms
       ceiling (committed before the run): 50.0 ms

   Total time rises ~27x with the optimizer on while the **lock hold stays
   flat** — the direct empirical form of the proof. Legacy vs unified also
   matches, so W7 did not change the hold.
3. **`decision_id` passthrough, four independent proofs.** One call site in
   the recovery path (`pipeline.py`) and it passes `decision_id`; no entry
   point calls `decide_action`/`execute_action`/`deliver_recovery_message`/
   `classify`/`opportunity_lock` directly any more; each entry point produces
   exactly 1 agent message; and a **negative control** that strips
   `decision_id` from the shared call reproduces `status=
   skipped_unverified_execution` with **0 messages**.
4. **`execute_action()` called exactly once** per pipeline run, asserted per
   entry point with a call counter; and `trigger_event`'s duplicate-event
   replay runs the pipeline **0** times, verified with a spy.

### An amended test, disclosed

`test_every_production_caller_supplies_the_execution_it_delivers_for`
asserted a delivery call in *each of the three* entry points. Unification
made that premise false. Replaced by
`test_the_recovery_path_has_exactly_one_delivery_call_site`, which is
**strictly stronger**: before, three sites each had to pass `decision_id` and
a fourth could appear unnoticed; now the recovery path has exactly one, and
there is nowhere for a call to drift to. Paired with
`test_no_entry_point_bypasses_the_shared_pipeline`.

### Why the dispatcher is not a caller

`dispatch_scheduled.py` advances an already-decided action and must never
call `execute_action()`, which is not idempotent at the call level. Routing
it through the shared pipeline would violate that. `phase5_config._check()`
raises if `dispatch` is ever added to `ENTRY_POINTS_USING_SHARED_PIPELINE`.

### What stayed in the callers, deliberately

`trigger_event`'s validation, dedup short-circuit and INSERTs;
`handle_customer_reply`'s history-before-insert ordering, intent parse,
fail-closed message persist, and its `try/except → status="engine_error"`.
Hoisting that last one would impose it on the other two, which currently let
exceptions propagate — a behaviour change wearing the costume of a cleanup.

---

## 2. Open obligations carried into later steps

- **Tighten `test_method_change_has_no_reachable_executor_path`.** It
  substring-matches `"method_change"` inside the legitimate `"method_changed"`
  feature key, producing 14 false-positive offenders across `data_factory/`
  and `ml/`. Fix is a word-boundary match
  (`(?<![0-9A-Za-z_])method_change(?![0-9A-Za-z_])`). But per section 0.1 the
  tightened test still proves nothing, because no such token can exist — so it
  is retained only as a cheap tripwire, and the boundary is re-verified for
  real by new structural tests in W8.
- **Retracted G7 test — ruled out of Phase 5 scope on 2026-09-02, and
  escalated to the project closeout list as item C1 (section 1a).** Untouched;
  still one of the 16 known failures. Tracked there rather than here, because
  it must survive past this phase's hand-off.
- **`do_nothing` would fabricate an execution row.** A `do_nothing` decision
  reaching `execute_action()` hits the `EXECUTION_STATE_MAP` default and
  INSERTs a `recovery_executions` row with state `executed`, asserting a
  dispatch that never happened. Fix belongs in W5.
- **`"executed"` is a member of both closed vocabularies** —
  `DECISION_OUTCOMES` (compliance) and `EXECUTION_STATES` (lifecycle). Live
  collision; the W8 structural tests pin it rather than rename it.
- **~~`allowed` is read by no production code.~~ CORRECTED 2026-09-04 (W7,
  ruling W8/A10) — the sentence was false as written.** `allowed` IS read by
  production code, in three places: `decide_action.py:344` and `:481` (the
  ranked path, added by W3) and `dispatch_scheduled.py:202` (added by W6).
  The claim was true when first written and went stale as those two steps
  landed.

  **The substance survives, and it is the part that mattered:**
  `execute_action()` still branches on `outcome == "executed"` and never on
  `allowed`, so "only `allowed: True` reaches the executor" is still proven
  at the executor boundary through a proxy field rather than the permission
  bit. **RESOLVED in W7** — the equivalence is now mechanical rather than
  conventional: `tests/test_phase5_authority_invariants.py` asserts
  `allowed == (outcome == "executed")` across all 25 corpus scenarios
  (measured: 0 violations), asserts that an execution row is written exactly
  when `allowed` is true, and pins that the executor still branches on
  `outcome` so a future switch to `allowed` prompts retiring the rationale
  rather than leaving it stale a second time.
- **Latency.** p95 measured at 747.9 / 737.5 / 724.3 ms across three runs
  against the declared 250 ms budget (`optimizer_config.LATENCY_BUDGET_MS`).
  Not met, unchanged from Phase 4, and no Phase 5 budget was invented to
  replace it. Re-measured after W6: p50 753.3 ms / p95 859.1 ms. Unchanged in
  substance; W6 adds no scoring call.

### Added by W6 (2026-09-04)

- **~~"W8" is referenced in this section but is not a planned step.~~
  CLOSED 2026-09-04 by ruling A10: there is no W8; all three items were
  folded into W7 and are done.** The resolution was not a judgement call —
  each of the three appears verbatim in `EXECUTION_PLAN.md`'s own Phase 5
  *Validation/tests* list, so they were always Phase 5 work and W7 is Phase
  5's last step:

  | "W8" item | The Phase 5 validation clause it already was | Status |
  |---|---|---|
  | `allowed`-vs-`outcome` made mechanical | "The existing authority tests (only `decide_action`'s `allowed: True` output ever reaches the executor)" | done, `test_phase5_authority_invariants.py` |
  | pin the `'executed'` collision | "A structural test confirming that no query can conflate a `recovery_executions` lifecycle state with a `recovery_decisions` compliance outcome" | done, same file |
  | tighten the method_change gate | "A structural test confirming there is no reachable code path anywhere that dispatches a payment-method-change action" | word boundary applied; **2 offenders remain, see below** |

- **`test_method_change_has_no_reachable_executor_path` still fails, now with
  2 offenders instead of 14.** The word-boundary match
  (`(?<![0-9A-Za-z_])method_change(?![0-9A-Za-z_])`) cleared every
  `"method_changed"` feature-key false positive. What remains are two genuine
  bare `"method_change"` string constants at
  `ml/evaluate_outcome_model.py:325` and `:344` — but they are **display
  labels in an offline Phase 3 evaluation script**, naming an edge case in a
  parity report. They are not action types and there is no executor path
  through that module.

  **Deliberately left failing rather than made to pass.** The obvious fix is
  to scope the gate to code that can actually reach the executor
  (`engine/`, `api/`, `db/`), which its own name already implies — but that
  is narrowing a gate's scope to obtain a pass, and it would drop the known-
  failure count from 12 to 11. That is a ruling to take explicitly, not a
  side effect of W7. **Open for Phase 5 sign-off.**

- **`stuck_dispatches()` has no operator surface.** W6 chose at-most-once
  semantics deliberately (section 1h, ruling A4): a row whose delivery raised
  is left in `'dispatched'` and never retried, because a claimed row may
  already have reached the customer. The function that enumerates those rows
  exists but nothing calls it — no alert, no dashboard, no workflow. A row can
  sit stuck indefinitely with nothing drawing attention to it. The *safety*
  property is complete; the *operability* one is not.

- **`retry_only_count`, `last_action_type` and `hours_since_last_action` still
  count unfired scheduled actions.** Amendment A1 was ruled for cooldown and
  the attempt ceiling, and was implemented to exactly that scope — those three
  are ML features, not compliance inputs, and still read the unfiltered
  history. So the model can be told a retry happened when it has only been
  queued. Arguably the same defect one layer over, and arguably against the
  project's "never invent an input" rule; deliberately **not** silently
  extended beyond the ruling, because changing model inputs is a scoring
  change and needs its own decision. Flagged for a ruling, not fixed.

- **`opportunities.status` is set at schedule time.** `execute_action()` moves
  the opportunity to `recovering` when it *queues* an action, not when the
  action fires, and the dispatcher does not walk that back when it cancels one
  (ruling A11, approved as a note). Confirmed non-stranding: `core_loop.py:32`
  selects `status IN ('open','recovering')`, so a cancelled-dispatch
  opportunity is re-picked on the next batch cycle. Recorded because "status
  says recovering" and "an action is pending" are not the same claim, and a
  future reader may assume they are.
