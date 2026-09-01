# Phase 3 — Joint Outcome / Treatment-Effect Model — Notes

Covers: clean-install verification, `.gitignore` fix, the seed-43 treatment-
effect instability, 4-way split semantics, the `phase3_treatment_effect`
evaluation-population amendment, and the new `ml/` modules. Written
alongside implementation, not deferred, since this session's own build order
front-loaded the discovery/lock work (Steps 0–1) before any model code.

## 1. Clean-install / dependency verification

Built a throwaway venv with the exact pins in `backend/requirements.txt`
(`numpy==2.2.6 pandas==3.0.5 scikit-learn==1.7.2 xgboost==3.4.1
scipy==1.17.1`). All five installed cleanly from PyPI (scipy 1.17.1 does
exist — the "unverified" flag in the requirements.txt comment was about the
pin never having been tested against exactly these sibling pins, not about
the version being unavailable). `import scipy.stats` + a `pearsonr` smoke
test ran with **zero warnings** under `-W error`. Both shipped
`ml/models/{lr,xgb}_model.joblib` load cleanly under the same env. **No
requirements.txt change made** — the existing pin is correct and now
verified, not merely asserted.

## 2. `.gitignore` fix

Root `.gitignore` line 46 was a bare `data_factory` (no path prefix), which
matches any file or directory named `data_factory` anywhere and ignored the
**entire `backend/data_factory/` tree** — none of Phase 2's source, none of
`locked_thresholds.json`, none of the eval-set lock manifests were tracked.
Replaced with scoped entries for the two fully-regenerable, wiped-every-run
scratch directories (`backend/data_factory/output/`,
`backend/data_factory/registry/`); everything else under `data_factory/`
(all `.py` source, `locked_thresholds.json`, `phase3_eval/phase3_eval_lock.json`)
is now trackable. Large generated CSVs stay ignored via the pre-existing
global `*.csv` rule — nothing added there. **Not committed** (per
instruction) — this only makes tracking possible.

## 3. Seed-43 `insufficient_funds` instability — reasoned treatment (Decision F kept, seeds NOT swapped)

`phase3_eval_setup.py`'s advisory (non-gating) sanity check found seed 43's
*dataset* (empirical-vs-analytic, Phase 2's own tolerance) fails on one
bucket: `retry | payment_failed | insufficient_funds | same_method`, n=187,
analytic effect −0.055 (0.005 above the 0.05 direction-test floor), empirical
+0.020 — sign flip. Seed 44 and seed 42 both pass cleanly.

**Diagnosis:** this is the same instability class already documented and
accepted in `STATE_AND_DECISIONS.md` for the ground-truth bucketing work —
a bucket whose *true* effect sits just above the floor is, by construction,
one whose *empirical sign* is not reliably estimable from a raw case-mean
difference at this sample size; the floor exists precisely because such
buckets are noise-dominated. This is a property of that bucket's small true
effect size relative to n, not a generator defect, and it is a check on the
**raw empirical mean**, not on any trained model.

**Treatment:**
- **Seeds kept at `[42, 43, 44]` — not changed.** Swapping seed 43 for a
  seed chosen because its dataset happens to pass would be selecting seeds
  post-hoc on the very statistic being tested, which is a worse discipline
  violation than living with one known-marginal bucket on one of three
  *supporting* seeds.
- **The generator was not touched.** No calibration constant, no
  `insufficient_funds` term in `outcome_model.py`, no bucketing logic was
  changed to make this pass.
- **`phase3_multiseed`'s cross-seed pass criterion is unaffected**: it
  compares the *model's* estimated-effect sign across seeds for buckets that
  are direction-scored on multiple seeds, not the raw per-seed empirical
  check that flagged this. A regularized model trained on the full ~29k-row
  seed-43 dataset may well land closer to the true analytic value for this
  bucket than a single raw case-mean difference does — that is an empirical
  question the actual multi-seed run will answer, not something to
  pre-judge.
- **If, after training, `phase3_multiseed` fails on seed 43 specifically
  because of this bucket**, that failure is expected/explainable via this
  note and the per-bucket detail already surfaced by
  `ground_truth_treatment_effect_check` — it must still be **reported as a
  failure**, not silently waived. No gate was loosened to pre-empt this.

## 4. Four-way split — confirmed semantics

| Slice | Source | Size (baseline seed 42) | Disjointness | Frozen? |
|---|---|---|---|---|
| `training_pool` | `phase3_data_split` carve (customer-level, by `case_id`) | 2095 cases / 20 044 rows | case- and customer-disjoint from `calibration_holdout` | SEEN, not hash-locked as a fixed split target, but the *source file* is hash-locked |
| `model-selection-val` | `train_outcome_model.deterministic_train_val_split()` — `GroupShuffleSplit(groups=case_id, test_size=0.15, random_state=42)` on `training_pool` | ~15% of `training_pool`'s cases | case-disjoint from `train` (asserted); customer overlap with `train` is *not* required to be zero (both are "seen") | SEEN, deterministic (fixed `random_state`), reproducible on demand — not materialized as a separate frozen file |
| `calibration_holdout` | `phase3_data_split` carve | 455 cases / 4 493 rows | case- and customer-disjoint from `training_pool` | **UNSEEN, hash-locked** (`phase3_baseline_seed42_calibration_holdout`) |
| `temporal_holdout` | latest 15% of cases by `sim_hour` | 450 cases / 4 392 rows | case-disjoint from everything; customer overlap with `training_pool` **accepted** (Decision K) | **UNSEEN, hash-locked** (`phase3_baseline_seed42_temporal_holdout`) |

`train` and `model-selection-val` are both drawn from `training_pool` and are
therefore both data the model is allowed to see during development (fitting
and model/hyperparameter selection respectively) — neither is hash-locked,
because Decision A reserves that guarantee for genuinely unseen data. They
are still **deterministic and case-disjoint from each other**, per this
task's explicit requirement, via a fixed `GroupShuffleSplit` seed.

## 5. `phase3_treatment_effect` amendment — evaluation population

Original 2026-09-01T15:11:20Z lock specified tolerances but not *which*
dataset the gate runs against. Filled via a dated `_amendment_locked_at_utc`
(2026-09-01T15:50:25Z, still before any Phase 3 evaluation has ever run):
the gate evaluates on `training_pool ∪ calibration_holdout` (≈2550 cases,
everything but `temporal_holdout`), not `calibration_holdout` alone.
Rationale: `calibration_holdout`'s 455 cases would starve most of the 19
Phase-2-proven-evaluable buckets below `min_cases_per_bucket_for_check=100`,
collapsing the gate's statistical power exactly where it matters. No
numeric tolerance was changed. Full reasoning in the amendment text itself
(`backend/data_factory/locked_thresholds.json` → `phase3_treatment_effect`).

## 6. Network health — Decision B2 implementation

`backend/ml/bank_health_setup.py` re-derives the baseline-seed-42 health
series (deterministic, same generator code Phase 2 already proved
reproducible) and loads it into the previously-empty `bank_health_observations`
table — the extension point Phase 1/2 explicitly reserved for this
("consumed from Phase 3 onward"). `backend/ml/outcome_features.py`'s
`network_health_rolling()` is the one function that computes a trailing
168h-window aggregate (success_rate / timeout_rate / health_score) from that
table; both `train_outcome_model.py` (batch, over `training_pool`) and
`inference.py` (single-case) call the identical function against the
identical table, loaded via the identical `load_health_observations()` — this
is the train/serve parity mechanism for the network-health feature
specifically. Units are simulated hours throughout, consistent with every
other Data Factory timestamp (documented in `bank_health_setup.py`'s
docstring).

## 6b. Two correctness bugs found and fixed during feature-construction build

Both caught by smoke-testing `build_feature_frame_from_joint_df` on a small
slice **before** training any model on the output — not in production:

1. **Pandas read the literal string `"n/a"` as a missing value.** The frozen
   joint-dataset CSVs use `"n/a"` as a genuine, meaningful candidate value
   (`do_nothing` / `escalate`'s method/channel, `do_nothing`'s timing —
   written by the Data Factory's own `candidate_generation.do_nothing_candidate()`).
   `pd.read_csv` with defaults maps `"n/a"` to `NaN`, silently corrupting
   every `do_nothing`/`escalate` row's candidate identity — the `do_nothing`
   baseline is central to the entire EIV concept, so this was not cosmetic.
   Fix: `outcome_features.read_joint_csv()` uses
   `keep_default_na=False, na_values=[""]` — genuinely empty cells (a Python
   `None` `root_cause`/`days_overdue` for a non-payment-failed/non-invoice
   row) still become `NaN`; `"n/a"` stays a string. Every joint-CSV read in
   Phase 3 goes through this function.

2. **`nan or default` returns `nan`, not `default`.** NaN is truthy in
   Python, so the common `context.get("days_overdue") or 0.0` idiom keeps a
   NaN instead of substituting the default. Fix: `outcome_features._is_missing()`
   (handles both `None` and float `NaN`) + `_or_default()`; every
   missing-value substitution in the module goes through them.

## 6c. NetworkHealthLookup — performance, not a shortcut

The trailing-window network-health feature is precomputed in bulk
(`NetworkHealthLookup`: a per-channel pandas rolling mean over the trailing
42 four-hour windows, `min_periods=1`), turning a ~55s-per-24k-row
boolean-mask scan into a sub-second O(1) lookup. `network_health_rolling()`
remains the semantic reference; `NetworkHealthLookup.verify_against_reference()`
(run by the parity gate) asserts the bulk path reproduces it exactly. Both
`train_outcome_model.py` and `inference.py` build ONE lookup and hand it to
the same `build_feature_row()` — still one shared computation, just a fast
one. Contiguity assumption (no gaps in the 4h window series — true for the
Data Factory's output) is documented in the class docstring.

## 6d. Two evaluation-validity fixes found during the first gate run

1. **Cross-profile gate was feeding the model baseline network health for
   stress-world cases.** The stress joint dataset's outcomes were generated
   under the STRESS profile's own bank-health series (~4.5× incidents, mean
   health 0.46 vs 0.80). But `bank_health_observations` holds only the
   baseline series (`bank_health_setup.py` loads `profile="baseline"`), so
   the rolling network-health feature for stress-dataset rows was baseline
   health — decorrelated from the health that actually shaped those
   outcomes. Fix: the cross-profile gate builds a stress `NetworkHealthLookup`
   from the deterministically-regenerated stress health series (this is also
   what a live serve against a stress-shifted world would see). Training and
   the baseline/temporal gates are unaffected — they correctly use baseline
   health.
2. **Temporal ranking gate compared two different quantities.** It scored
   candidate pairs by "analytic *probability* treatment effect ordering vs
   model *expected recovered rupee* ordering" — a probability difference
   against an absolute rupee expected value. Corrected (dated amendment to
   `phase3_temporal.ranking_pair_definition`, 2026-09-01T16:22:09Z) to
   compare like-for-like: model probability-space treatment effect
   (`model_p(cand) − model_p(do_nothing)`) vs the generator's
   probability-space analytic effect. `min_ranking_direction_agreement`
   (0.85) and `min_ranking_pairs` (500) unchanged.

## 6e. Model capacity — a bump was tried and reverted

The two XGBoost heads use conservative capacity (300 trees, depth 4, lr
0.05), matching `train_risk_model.py`. A bump to (600, depth 5, lr 0.03 /
400, depth 5) was tried to reduce the visible treatment-effect *shrinkage*
(the single shared model under-shoots analytic effect magnitudes — e.g. it
estimates `retry|needs_action` at −0.008 vs the generator's −0.075).
Result: it **overfit the ~2100-case training pool** — in-profile calibration
degraded (ECE 0.019→0.021, bin-gap 0.036→0.058, *failing* `phase3_calibration`)
and cross-profile / temporal ranking did not improve. Reverted. `AUC ≈ 0.63`
appears to be near this synthetic world's observable-feature ceiling: the
outcome logit is dominated by hidden state the model cannot see (Phase 2's
`outcome_model.hidden_state_term`, weights up to 1.0 on six latent
variables), so added capacity fits noise.

## 6f. Evaluation results (final run — `evaluate_outcome_model.py`, seed-42 model)

**32 / 36 checks pass.**

| Gate | Result | Numbers |
|---|---|---|
| 1 · in-profile calibration (calibration_holdout) | **PASS** | max bin gap 0.036 ≤ 0.05; ECE 0.019 ≤ 0.03 |
| 2 · treatment-effect ⚠ (train_pool ∪ calib_holdout) | **PASS** | direction match **4/4 = 100%** ≥ 0.90; every bucket gap ≤ 0.10 (worst 0.067); 19 buckets evaluable, 4 direction-scored |
| 3 · cross-profile calibration (stress, unseen) | **FAIL** | max bin gap **0.101 > 0.10**; ECE **0.0815 > 0.07** |
| 4 · temporal calibration (temporal_holdout) | **PASS** | max bin gap 0.030 ≤ 0.06; ECE 0.013 ≤ 0.04 |
| 4 · temporal ranking-direction | **FAIL** | agreement **0.833 < 0.85** (2573/3089 pairs) |
| 5 · multi-seed (42/43/44, supporting) | **FAIL** | per-seed calibration + treatment-effect all pass; cross-seed bucket-sign stability passes (no conflicts); **temporal ranking fails on seed 42 (0.833) and 43 (0.773), passes seed 44 (0.894)** |
| 6 · parity — NetworkHealthLookup vs reference | **PASS** | max diff 7e-15 (FP epsilon; ≤ locked fallback atol 1e-9) |
| 6 · parity — batch path vs `ml.inference.py` | **PASS** | max diff **exactly 0.0** over 33 cases (incl. do_nothing, method_change, null-health) |
| 7 · failure behavior (5 malformed cases) | **PASS** | every one → clearly-flagged null, never a plausible score, never a crash |
| 8 · authority boundary (`inference.py`, `outcome_features.py`) | **PASS** | no import of / reference to any engine control fn or compliance/execution write target |

**The two real failures** (cross-profile calibration, temporal treatment-effect
ranking) both trace to the p_recovery head's modest discrimination (AUC ≈ 0.635,
near the observable-signal ceiling — §6e). The `⚠` causal treatment-effect
**direction** gate — the one the whole phase hinges on — **passes on all three
seeds**. Per `Phase_Acceptance_Test_Gates.md`, Phase 3 is **NOT signed off**
while any hard gate fails; **no locked threshold was loosened** to change that.
Whether the fix is a stronger model / richer feature set, or an
independently-reviewed reconsideration of the `phase3_cross_profile` (ECE 0.07)
and `phase3_temporal` (ranking 0.85) thresholds against this synthetic world's
observability ceiling, is a causally-sensitive-phase decision that needs the
independent review the "no self-certification" discipline requires — not a
unilateral call.

## 6g. Generator-cell interaction features — adopted, results

Two categorical columns added to the shared contract (`ALL_FEATURES` 24 → 26),
computed inside `build_feature_row()` from already-normalized values of columns
already in the contract — no new information, no simulator internals, no
`hidden_*`/`analytic_*`:
`cell_action_effect = candidate_action|event_type|root_cause|candidate_method_changed`
and `cell_action_timing = candidate_action|root_cause|candidate_timing`.
Rationale: OneHotEncoder + `max_depth=4` cannot isolate one cell of the
generator's `action_effectiveness` / `timing_term` lookup tables without
spending a tree's whole depth budget. Adopted on model-selection-val evidence
only (AUC 0.6356→0.6447, shrinkage slope 0.360→0.471); eval sets were not
consulted for the adoption decision.

**Result: the temporal ranking gate now passes; cross-profile is unmoved; a new
regression appeared on seed 43.** Full before/after in the session report.
Key numbers (gate values, all thresholds unchanged):

| | before | after |
|---|---|---|
| temporal ranking (primary) | 0.833 **FAIL** | **0.891 PASS** |
| temporal ranking (seed 44) | 0.894 PASS | 0.923 PASS |
| temporal ranking (seed 43) | 0.773 FAIL | 0.813 **still FAIL** |
| seed-43 treatment-effect direction | 1.000 (5/5) PASS | **0.800 (4/5) FAIL — new** |
| cross-profile ECE / maxgap | 0.0815 / 0.1013 | 0.0812 / 0.1016 (flat) |
| in-profile calibration ECE / maxgap | 0.0186 / 0.0357 | 0.0137 / 0.0299 |
| effect shrinkage slope | 0.360 | 0.543 |

The seed-43 treatment-effect regression is the `retry|payment_failed|
insufficient_funds|same_method` bucket (n=160): model **+0.0099** vs analytic
**−0.0550**. This is the *same bucket* flagged as unstable in §3 above — where
seed 43's own realized data shows an empirical effect of **+0.020** against an
analytic **−0.055**. The pre-change model shrank so hard (slope 0.36) that it
landed near zero on the analytic side almost by accident; the sharper model
commits to what seed-43's data actually shows. So the regression is the model
becoming *more* faithful to its training data on a bucket whose realization
disagrees with the generator's own expectation. Predicted in §3 and reported
as a failure, not waived.

## 6h. Independent-review decisions (2026-09-01) — two items closed

These are the **reviewer's** calls, not the implementer's. The implementer is
barred from re-litigating either.

### (a) Seed-43 treatment-effect regression — WAIVED, not fixed

The feature change was **not** reverted and no further model change was made to
recover this bucket. Reviewer's reasoning, recorded verbatim:

> the failing bucket (retry|payment_failed|insufficient_funds|same_method,
> n=160) was already flagged in PHASE3_NOTES §3 as unstable pre-change —
> seed-43's own realized empirical effect (+0.020) already disagreed with its
> analytic ground truth (−0.055) at the Phase 2 dataset level. Magnitude still
> passes (gap 0.065 < 0.10); only sign flips, on an effect at the noise floor
> (~0.022 probability-sd per draw, from the earlier noise analysis). The new
> model is more faithful to noisy training data here, not less accurate.

Implementation: `phase3_multiseed`'s locked criterion is **unchanged**. The
waiver lives in `evaluate_outcome_model.MULTISEED_WAIVERS` and is applied at the
report layer only. It fires **only** if the named bucket is the *sole*
direction miss for that seed, so it can never mask a second undisclosed
failure, and the gate suite prints `[WAIVED — DISCLOSED EXCEPTION]` on every
run. The rollup is reported as **CONDITIONAL** with the exception named in the
check label — never silently green.

### (b) Cross-profile gate — AMENDED (the gate, not the model)

`phase3_cross_profile` redefined from a raw-ECE numeric bound to a
**ranking-transfer** criterion, dated `_amendment_locked_at_utc:
2026-09-01T18:10:24Z`. Gating now: stress ranking-AUC must not degrade from
in-profile AUC by more than 0.05 absolute, and must clear 0.55. Raw calibration
level (ECE / maxgap) is still **computed and printed on every run** as a
disclosed known limitation, but is no longer pass/fail. The original 0.10 /
0.07 bounds are retained in the file for the record.

Justification (full text in `locked_thresholds.json`), citing:
1. **AUC invariance under monotone transforms.** The gate's stated purpose is
   to distinguish learned structure from memorized numbers. Memorization shows
   up as ranking collapse; AUC is invariant to any monotone rescaling of
   scores, so it isolates ordering from level. Raw ECE conflates the two and
   penalizes a model identically whether it failed to learn structure or merely
   could not anticipate an unobservable level shift.
2. **The direct null-result experiment.** The generator-cell feature change cut
   temporal shrinkage ~50% (slope 0.360→0.543), lifted temporal ranking
   0.833→0.891, improved in-profile ECE 0.0186→0.0137 and stress AUC
   0.628→0.638 — and moved cross-profile ECE by **0.0003**. A change that
   improved structural learning on every other axis left this metric flat.
   That is evidence it is not measuring a structural-learning gap the model can
   close.
3. **No valid corrected test exists.** A recalibration-protocol variant cannot
   be tested: the whole stress dataset is the locked eval artifact and no
   disjoint stress calibration sample exists — established by the provenance
   audit that invalidated an earlier circular result (a shift parameter fit on
   stress eval labels, then scored on a superset of those same rows).

This reasoning predates, and is independent of, wanting a passing result — it
was derived from the gate's stated purpose and from a null result, and it is
recorded as such in the amendment text itself.

## 6h-note. Waiver did not cover seed 43's SECOND failure — rollup still fails

The item-(a) waiver fires correctly and is visible in the gate output. It does
**not** make the multi-seed rollup pass, because seed 43 fails a *second*,
separate gate the waiver does not name: **temporal ranking-direction 0.813 <
0.85**. The reviewer's decision waived "this one documented, disclosed
exception" (the treatment-effect regression) while directing the rollup to be
marked CONDITIONALLY PASSED; with a second uncovered failure present those two
instructions cannot both hold. The waiver was **not** extended on the
implementer's own initiative — `apply_multiseed_waivers()` prints
`[NOT WAIVED] seed 43 temporal: no waiver covers this failure` and the rollup
is reported FAIL (CONDITIONAL, 1 disclosed waived exception). Awaiting the
reviewer's call on whether to extend the waiver to seed-43 temporal ranking or
to accept the rollup as failing with one waived exception.

(Also fixed during this pass: the waiver's `bucket_key` was first transcribed in
`data_factory/validators.py`'s spelling — `...|same_method` — while this
module's `gate_treatment_effect` renders `method_changed` as the raw bool —
`...|False`. The exact-match guard correctly refused to fire the waiver rather
than matching something adjacent; the key now carries this module's spelling and
the mismatch risk is called out in a comment beside it.)

## 6i. Deliberately NOT resolved (documented, not gating)

- **Near-tie temporal ranking noise floor.** The temporal gate's ground truth
  (`analytic_p`) is generated *including* a per-candidate `rng.normal(0,
  outcome_noise_sigma=0.15)` term inside `z` before `p = sigmoid(z)`
  (`outcome_model.py:177–179`, returned at :195). Within a case the do_nothing
  term cancels, so a pair's ground-truth gap carries the difference of two
  independent draws — ≈**0.022 probability-sd** of noise no model can predict.
  The 0.05 eligibility floor sits only ~2.3 noise-sd from zero, so a share of
  "eligible" pairs are ordered by a realized noise draw rather than by
  systematic effect. Separately, the metric weights all eligible pairs equally,
  so ~47% of the score is carried by pairs whose misordering costs the
  optimizer almost nothing, while the economically decisive pairs (gap > 0.12)
  already sit at 0.955–0.982. **Not changed**: the gate now passes on the
  primary model (0.891) and on seed 44 (0.923), so the metric is not the
  binding constraint, and changing a metric that a good model clears would be
  unjustified. Recorded as a known measurement limitation for Phase 9 to
  revisit if it ever becomes binding.
- **Network-health features carry ~zero learned signal** in this synthetic
  world (permutation ΔAUC +0.0004). Correctly plumbed and train/serve
  identical; the weakness is that Phase 2's generator barely lets network
  health move outcomes. Kept, per Decision B2 / serving parity. Not a model
  defect.

## 6j. Phase 3 EXIT gate report (final run, all thresholds as locked)

**33 / 36 checks pass.** Model: two XGBoost heads, 26-feature shared contract.

| Gate | Result | Numbers |
|---|---|---|
| in-profile calibration (calibration_holdout, 455 cases) | **PASS** | max bin gap 0.0299 ≤ 0.05; ECE 0.0137 ≤ 0.03 |
| treatment-effect ⚠, primary | **PASS** | direction 4/4 = 1.000 ≥ 0.90; all gaps ≤ 0.10; 19 buckets evaluable |
| cross-profile (AMENDED: ranking transfer) | **PASS** | in-profile AUC 0.6461, stress AUC 0.6268, degradation +0.0193 ≤ 0.05; stress AUC ≥ 0.55; n=3000 |
| ↳ known limitation, reported not gated | — | stress calibration level ECE 0.0812, maxgap 0.1016 (mean_pred 0.8857 vs mean_actual 0.8045) |
| temporal calibration (450 cases) | **PASS** | max bin gap 0.0452 ≤ 0.06; ECE 0.0148 ≤ 0.04 |
| temporal ranking-direction | **PASS** | 0.891 (2751/3089) ≥ 0.85 |
| multi-seed · seed 42 treatment-effect | **PASS** | 1.000 (4/4) |
| multi-seed · seed 43 calibration | **PASS** | max gap 0.0359; ECE 0.0169 |
| multi-seed · seed 43 treatment-effect | **FAIL → WAIVED** | 0.800 (4/5); gaps ≤ 0.10; waived bucket `retry\|payment_failed\|insufficient_funds\|False` |
| multi-seed · seed 43 temporal calibration | **PASS** | max gap 0.0241; ECE 0.0119 |
| multi-seed · seed 43 temporal ranking | **FAIL — not waived** | 0.813 (2493/3066) < 0.85 |
| multi-seed · seed 44 calibration | **PASS** | max gap 0.0304; ECE 0.0150 |
| multi-seed · seed 44 treatment-effect | **PASS** | 1.000 (4/4) |
| multi-seed · seed 44 temporal calibration | **PASS** | max gap 0.0250; ECE 0.0106 |
| multi-seed · seed 44 temporal ranking | **PASS** | 0.923 (2743/2973) |
| multi-seed rollup | **FAIL (CONDITIONAL, 1 waived)** | {42: True, 43: False, 44: True}; blocked by seed-43 temporal ranking |
| cross-seed bucket-sign stability | **PASS** | no conflicts |
| parity · lookup vs reference | **PASS** | max diff 7.11e-15 ≤ 1e-9 |
| parity · batch vs `ml.inference.py` | **PASS** | max diff **0.0 exact**, 33 cases incl. do_nothing / method_change / null-health |
| failure behavior (5 malformed cases) | **PASS** | each → clearly-flagged null, no crash, no plausible-looking score |
| authority boundary (`inference.py`, `outcome_features.py`) | **PASS** | no execution-authority import or reference |
| artifact hash verification | **PASS** | all frozen Phase 3 artifacts verified before use |

Remaining blocker: **seed-43 temporal ranking-direction (0.813)**, uncovered by
any waiver. See §6h-note.

## 7. New/changed files this session (Phase 3 Steps 0–2)

**New:** `backend/data_factory/phase3_eval_setup.py`,
`backend/ml/bank_health_setup.py`, `backend/ml/outcome_features.py`,
`backend/ml/inference.py`, `backend/ml/train_outcome_model.py`,
`backend/ml/evaluate_outcome_model.py`, this file.
**Changed:** `backend/data_factory/validators.py` (numpy-bool fix, §Step 0),
`backend/data_factory/eval_set_lock.py` (Phase 3 artifact-lock functions,
additive), `backend/data_factory/locked_thresholds.json` (new `phase3_*`
blocks + one dated amendment), `.gitignore` (scoped, not committed).
**Untouched:** everything under `backend/engine/`, `backend/api/`,
`backend/llm/`, `backend/ml/simulate_training_data.py`,
`backend/ml/train_risk_model.py`, `backend/ml/models/{lr,xgb}_model.joblib`,
`backend/data_factory/legacy/`, and every other Phase-2 generative module
(`entities.py`, `outcome_model.py`, `candidate_generation.py`,
`candidate_outcome_dataset.py`, `calibration_profiles.py`,
`bank_health_timeseries.py`).
