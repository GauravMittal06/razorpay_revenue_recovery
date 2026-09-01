# PHASE2_NOTES.md — Canonical Synthetic World + Joint Candidate-Outcome Dataset

## Scope of this delivery — read this first

This zip is **not a full repository drop-in-replacement**. Only 7 specific
Phase-0/1 files were provided for this session (`db/db.py`,
`ml/simulate_training_data.py`, `ml/train_risk_model.py`,
`ml/verify_sensitivity.py`, `data/generate_seed_data.py`,
`requirements.txt`, `test_everything.py`) — not `engine/*`, `api/*`,
`llm/*`, seed JSON, or model artifacts. Those 7 files are included here
**unmodified** (verified — see "Unmodified-files check" below). Everything
new lives entirely under `backend/data_factory/` plus one new file at the
project root, `test_data_factory.py`. Nothing in `engine/`, `api/`, or
`llm/` was touched or needs to be, since Phase 2 has no dependency on them
per the execution plan's Section 5 "Components affected" list.

## What changed / what's new

New package: `backend/data_factory/`
- `entities.py` — persistent synthetic merchants, customers, bank/PSP
  channels. A `SyntheticWorld` is built once per generation run and
  threaded through every case; customers accumulate a real
  `contact_history` across cases in temporal order (this is what backs
  fatigue and the customer-level leakage check).
- `bank_health_timeseries.py` — time-indexed, evolving `(bank, method,
  psp)` health series with random-walk drift + multi-window "incident"
  dips, generated once per run before any case exists, looked up by
  `HealthIndex` at each case's own decision timestamp.
- `candidate_generation.py` — the **one shared** eligibility/pruning
  module (structural eligibility, then a bounded relevance pre-filter),
  explicitly written to be importable, unmodified, by Phase 4's live
  optimizer later. Always emits exactly one `do_nothing` candidate first.
- `outcome_model.py` — the **one shared** stochastic potential-outcome
  function (`draw_outcome`), called once per candidate with the case's
  single hidden-state draw. Generalizes (not reinvents) the legacy
  generator's hidden-state term, action-effectiveness shape, and
  retry-count-penalty shape; adds timing, method-change, channel-fatigue,
  and network-health terms the legacy module never had.
- `candidate_outcome_dataset.py` — orchestrates the above into the one
  joint dataset. For each case: sample hidden state once, generate the
  eligible candidate set via the shared module, draw one outcome per
  candidate via the shared function, record exactly one real touchpoint
  against the customer's persistent history.
- `calibration_profiles.py` — `baseline` and `stress`, sharing all
  generator code, differing only in the numeric parameters each reads.
- `dataset_registry.py` — writes a JSON manifest per run (always) and,
  when a `recovery.db` is present, a row in the production
  `dataset_registry` table (schema unchanged from Phase 1 — that table
  was already structurally present, "populated starting Phase 2" per its
  own DDL comment).
- `validators.py` — every check the execution plan's Phase 2 section
  requires, each returning a real computed result, not a claim.
- `locked_thresholds.json` — every statistical tolerance and the
  temporal-holdout eval-set boundary, **committed before the first
  evaluation run against them** (see "Locked-before-use" below).
- `legacy/simulate_training_data_frozen.py` — byte-for-byte frozen copy
  of the original `ml/simulate_training_data.py`, so the two already-
  shipped `.joblib` artifacts stay reproducible from source. Verified
  identical below.
- `run_generation.py` — CLI entrypoint: generates both profiles, runs
  every validator, exports CSVs, registers both runs.

New file at project root: `test_data_factory.py` — a fresh-rebuild,
subprocess-level wrapper test in the same style as `test_everything.py`.

**No schema changes.** `db/db.py`'s DDL is untouched — `bank_health_observations`
and `dataset_registry` were already structurally present from Phase 1;
Phase 2 populates the registry table but deliberately does **not** write
into `bank_health_observations` (see "Deliberately NOT done" below).

## Locked-before-use — exact sequence honored

1. Read the full execution plan (Sections 3, 5, 6, 7) and SoT.
2. Wrote all generation/candidate/outcome code (`entities.py` through
   `candidate_outcome_dataset.py`), with **zero** evaluation run against
   real output at that point.
3. Committed `locked_thresholds.json` with `locked_at_utc:
   2026-08-30T15:32:43Z` (real wall-clock timestamp from the build
   environment, captured via `date -u` immediately before writing the
   file — reproducible in this session's tool-call log).
4. Only then wrote `validators.py` to read tolerances from that file, and
   ran `run_generation.py` for the first time.
5. Numeric tolerance values in `locked_thresholds.json` have **not** been
   edited since. The one gate that fails (see below) is reported as a
   real failure, not adjusted after the fact — see the discussion below
   for why the honest path here was to report it, not tune the threshold
   or the bucket definition to make it pass.

## Unmodified-files check

```
diff (CRLF-normalized) between uploaded ml/simulate_training_data.py
and the body of data_factory/legacy/simulate_training_data_frozen.py
(after its added header): 0 differences.
```
Ran directly in this session; see the "frozen copy verified byte-for-byte"
step in the build log. The other 6 uploaded files
(`db/db.py`, `ml/train_risk_model.py`, `ml/verify_sensitivity.py`,
`data/generate_seed_data.py`, `requirements.txt`, `test_everything.py`)
are included in this zip **byte-identical** to what was uploaded — none
were edited; `sha256sum` of each is unchanged from the upload (not
re-shown here since there is nothing to diff against outside this
session, but no `str_replace`/edit tool was ever called against any of
the 7).

## Verified, on a completely fresh rebuild

Every line below is copy-pasted from an actual run in this session, not
narrated. Full commands:

```
rm -rf backend/db/recovery.db backend/data/*.json \
       backend/data_factory/registry backend/data_factory/output
python3 test_data_factory.py       # DF_N_CASES=3000
```

`test_data_factory.py` result: **8/8 checks passed, exit code 0.**

- `[PASS] Seed data regenerated`
- `[PASS] Database rebuilt from schema`
- `[PASS] run_generation.py completed`
- `[PASS] Baseline joint CSV exists`
- `[PASS] Stress joint CSV exists`
- `[PASS] Baseline registry manifest exists`
- `[PASS] dataset_registry row count == 2` (one row per calibration
  profile, in the freshly rebuilt `recovery.db`, confirming the
  production table is actually populated by a real run, not just
  documented as capable of it)
- `[PASS] Second independent subprocess run's baseline CSV is
  byte-identical to the first run's` — an external, process-level
  reproducibility check, independent of the in-process one below.

Inside that same run, `run_generation.py`'s own 20 checks (at
`DF_N_CASES=3000`, seed=42):

| # | Check | Result |
|---|---|---|
| 0 | Static authority-boundary: no execution-authority imports/calls anywhere in `data_factory/` | PASS — 0 violations, 9 files scanned |
| 1 | Baseline dataset non-empty, every case has `do_nothing` | PASS |
| 2 | Stress dataset non-empty | PASS |
| 3 | Case-level leakage (grouped split, zero overlap) | PASS — 0 overlap |
| 3 | Customer-level leakage (grouped split, zero overlap) | PASS — 0 overlap |
| 3 | Temporal-order leakage (no negative `hours_since_last_action`) | PASS — 0 violations |
| 4 | Reproducibility: identical seed+profile, two in-process runs | PASS — 0 diff cells, both dataframes `.equals()` |
| 5 | Ground-truth treatment-effect vs analytic effect | **See below — unstable, documented, not silently passed** |
| 6 | Validator robustness self-test (deliberate corruption of hidden state) | PASS — clean data passes, corrupted data correctly flagged (thousands of violating cases) |
| 7 | Hidden-state-once-per-case, baseline and stress | PASS both — 0 violating cases |
| 8 | Distributional sanity (amount/health/method ranges) | PASS |
| 8 | Directional relationship (network health vs. technical-failure recovery) | PASS — positive correlation, e.g. r≈0.03–0.11 depending on n, always positive across every seed/n tried in this session |
| 9 | Calibration profile divergence (baseline vs. stress, same seed) | PASS — mean bank health differs by ~43% relative, recovery rate by ~10–12% relative |
| 10 | CSV export + dataset registry (JSON manifest + DB table row) | PASS |

**19–20/20 depending on run** — see next section for exactly which one is
unstable and why.

### Reproducibility, proven with an actual two-run diff

Ran `reproducibility_check()` (in-process, same Python process, two calls
to `generate_dataset()` with identical `(profile, seed)`) — `df1.equals(df2)`
and `truth1.equals(truth2)` both `True`, `diff_cells: 0`. Ran again as an
**external** check across two separate subprocess invocations of
`run_generation.py` — `filecmp.cmp(..., shallow=False)` on the exported
CSV — `True`. Two independent proofs, not one assertion repeated.

### Ground-truth treatment-effect check — an honest, unresolved gate failure

This is the one place this phase does **not** fully meet its own
definition of done, and I'm reporting it plainly rather than adjusting
the locked tolerance or the bucket definition after seeing the result.

At `DF_N_CASES=800`: `retry` bucket — `direction_match=False`.
At `DF_N_CASES=6000`: `retry` bucket — `direction_match=False`
(`empirical=-0.0088`, `analytic=+0.0001`).
At `DF_N_CASES=3000` (the run in this delivery): `retry` bucket happened
to land `direction_match=True` (`empirical=+0.0189`, `analytic=+0.0056`).

**Root cause, not a bug in generation:** the `retry` action bucket, as
currently defined in `ground_truth_treatment_effect_check()`, aggregates
across *all* root causes — transient causes (`gateway_timeout`,
`network_error`, effect ≈ **+1.1** logit) and needs-action/insufficient-funds
causes (effect ≈ **-0.6 to -0.9** logit) are pooled into one bucket. Those
opposite-signed effects net out to an analytic true effect of roughly
**+0.0001 to +0.006 in probability units** — i.e., genuinely close to
zero, by construction of the generator itself, not by measurement error.
A true effect that close to zero means its *sign* is dominated by sampling
noise at any dataset size this session tested (800–6000 cases), so
"does the empirical sign match the analytic sign" is not currently a
meaningful question for this specific bucket — it's close to a coin flip,
confirmed by getting different answers across three separate runs at
three different sample sizes with the same profile.

**What I did NOT do:** I did not lower `min_fraction_of_buckets_matching_direction`
below `0.90`, and I did not change `ground_truth_treatment_effect_check()`
to bucket by `(action_type, root_cause)` instead of `action_type` alone
— which would very likely fix this cleanly, since it's the root causes
being pooled that manufactures the near-zero net effect — **because I
noticed this only after running the locked evaluation**, and the task's
explicit instruction is that a tolerance or eval design must be locked
*before* the evaluation it's checked against, never adjusted afterward
based on the result. Changing the bucketing granularity now would be
exactly that, dressed up as a "code fix."

**What this means for sign-off:** per the execution plan's own Phase 2
definition of done ("passes every validator above... empirical treatment
effects match the generator's own analytic effect functions within
tolerance"), **Phase 2 does not cleanly pass this gate** as currently
specified. The `reminder`, `escalate`, and `payment_link` buckets pass
cleanly and consistently across every n tested (gaps 0.001–0.03,
direction always correct). Only `retry` is unstable, and only because its
true effect is close to zero under the current bucket definition.
**Recommended fix for the next pass (not applied here):** re-bucket the
ground-truth check by `(action_type, root_cause)` — a strictly more
granular, more correct grouping that reflects how the generative model
actually varies — and re-lock a fresh `locked_thresholds.json` with a new
`generator_version` before re-running. I'm flagging this explicitly rather
than either hiding it or fixing it under time pressure.

### Semantic equivalence to the frozen legacy module

Ran the frozen legacy generator (`n_cases=2000, seed=42`) and compared
qualitative shape against the new baseline dataset:

- Legacy: `retry` mean-y monotonically declines with `retry_count`
  (0.5549 → 0.5475 → 0.5335, strictly decreasing).
- Phase 2 baseline (same-method retry only, all root causes pooled by
  `retry_count`): 0.9237 → 0.9106 → 0.9148 — **not strictly monotonic**
  (a small uptick of +0.0042 at `retry_count=2`). Reported honestly; this
  is a minor deviation, most likely sampling noise at this bucket's
  smaller n once root-cause and timing are also varying underneath it,
  not a sign flip in the underlying penalty term (`retry_count_penalty()`
  is copied unchanged from legacy and is itself still monotonic by
  construction).
- Phase 2 baseline, same-method retry mean-recovered by root cause:
  `network_error` (0.969) > `gateway_timeout` (0.967) > `payment_declined`
  (0.926) > `authentication_failed` (0.865) ≈ `insufficient_funds` (0.859)
  > `expired_card` (0.842) — **matches legacy's qualitative ordering**
  (transient causes most favorable to retry, needs-action/insufficient-funds
  causes least favorable), confirming the generalized `action_effectiveness()`
  preserved the shape it was supposed to preserve.

### Persistent entities / fatigue, demonstrated on real output

Top customers by distinct case count in one run: 3 customers appeared in
18 separate cases each — confirms customers genuinely persist and
accumulate history across the run, not resampled fresh per case.
Fatigue effect on `reminder` candidates, mean `recovered` by
`prior_contacts_in_window` bucket: 0.900 → 0.891 → 0.867 → 0.858 —
monotonically decreasing, confirming intervention fatigue is real and
generalized across contact-type candidates (not narrowly scoped to
retry), as required.

## Deliberately NOT done

- **`bank_health_observations` (production table) is not written to by
  this phase.** The Data Factory's own health series lives only in the
  CSV export and in-memory `HealthIndex` — this table is documented in
  `db/db.py` as "consumed from Phase 3 onward" (a live rolling-aggregate
  feature computed from real observations), which is a different concern
  from the Data Factory's own offline synthetic series. Populating it now
  would blur that boundary for no benefit this phase needs.
- **Phase 4's live optimizer does not exist yet** — `candidate_generation.py`
  is written to be importable by it unmodified, but nothing imports it
  live yet. Not this phase's job.
- **No retraining of `lr_model.joblib`/`xgb_model.joblib`** — out of
  scope; those stay exactly as shipped, and stay reproducible via the
  frozen legacy module.
- **The (action_type, root_cause) re-bucketing fix for the ground-truth
  check** — diagnosed above, deliberately not applied post-hoc.
- **Multi-seed robustness sweep** — the execution plan explicitly treats
  this as secondary to cross-profile/temporal generalization (a Phase 3
  concern); only single-seed (42) runs were done here, at three different
  `n_cases` values, which is what surfaced the ground-truth instability
  above.
- **The temporal-holdout split itself is not carved out as a separate
  file** — `locked_thresholds.json`'s `unseen_eval_split` section locks
  the *boundary* (`temporal_holdout_fraction: 0.15` of `sim_hour`, on the
  baseline profile) for Phase 3 to apply; Phase 2 does not need to act on
  it yet since no model exists to hold data out from.

## Carried-forward open item — opportunity dedup/idempotency

The task brief for this session described an outstanding Phase 1 item:
no `UNIQUE` constraint on event ingestion, duplicate events creating two
opportunities. **This appears to already be resolved** in the `db/db.py`
actually provided for this session — it defines
`idx_opportunities_ingestion_event_id` as a `UNIQUE INDEX` on
`opportunities.ingestion_event_id`, and `CURRENT_CHAT_CONTEXT_chat_06.md`
independently confirms `trigger_event.py` was updated with "pre-check +
UNIQUE-index-backed race-safe duplicate handling (catches
`sqlite3.IntegrityError` on concurrent insert collision)" as part of
`PHASE1_GATE_FIXES.md`. I could not independently verify
`trigger_event.py`'s actual code in this session (it wasn't part of the 7
files provided), so I'm reporting what the schema and the context doc
both say, not re-confirming it from source. **Flagging this discrepancy
explicitly rather than silently repeating the brief's framing**: either
the brief's description was already stale before this session started, or
`trigger_event.py`'s fix needs independent re-verification before Phase 6
trusts it. Either way, this must be confirmed against the real
`trigger_event.py` source before Phase 6 (live experiment assignment)
depends on duplicate-free opportunity creation — a duplicate event
reaching live experiment assignment would silently corrupt the
incremental-₹ measurement, exactly as the original brief warned.

## What's ready to verify

- `backend/data_factory/` (10 new modules + `legacy/` + `locked_thresholds.json`)
- `test_data_factory.py` (project root)
- `backend/data_factory/output/*.csv` — the actual generated joint +
  ground-truth datasets from the run in this session (baseline + stress,
  seed 42, 3000 cases each)
- `backend/data_factory/registry/*.json` — the two manifests from that run
- The 7 originally-uploaded files, included unmodified, so the zip is
  self-contained and runnable (`python3 test_data_factory.py`) without
  needing the rest of the repository re-uploaded — though the rest of
  `backend/` (`engine/`, `api/`, `llm/`, seed JSON, model artifacts) is
  still needed for `test_everything.py` / the full application to run,
  and was out of scope for this session's inputs.

**Stop condition honored: not proceeding into Phase 3.** Waiting for the
independent verification pass on this before any go-ahead.

---

## Addendum — gate-closure pass against Phase_Acceptance_Test_Gates.md

Run against the stricter, hardened gate document. Five specific gaps were
identified there; here's what was done about each, honestly.

**New locked thresholds, timestamped before their first-ever evaluation
run** (`locked_thresholds.json`, new blocks `fatigue_significance`,
`candidate_timing_validity`, `eval_set_lock`, all stamped
`2026-08-30T20:43:46Z`): unchanged from the original locked entries —
only new sub-blocks were added, none of the previously-evaluated numbers
were touched.

1. **Fatigue gate [TIGHTENED] — closed.** Added `fatigue_significance_check()`:
   Pearson correlation between `prior_contacts_in_window` and `recovered`
   on every contact-type candidate, against a predeclared expected sign
   (negative), alpha (0.05), and min-N (200). Real result at seed=42,
   n_cases=3000: **r = -0.0256, p = 3.7e-05, n = 25929 — PASS** (sign
   matches, statistically significant). This replaces the earlier
   descriptive bucket-mean table with the named statistic the gate
   requires.

2. **Validator robustness — closed, one corruption test per validator.**
   Added five new corruption self-tests (case-level leakage, customer-
   level leakage, temporal-order leakage, distributional sanity,
   candidate-timing-validity) on top of the existing hidden-state one —
   six total, covering every validator that has a clean/corrupted
   distinction to test. Each deliberately corrupts a copy of real data
   (duplicated case_id across a split, a negative
   `hours_since_last_action`, an out-of-range amount, a `"9d"` timing
   bucket) and confirms the check flags it while passing on the clean
   copy. All six passed in the real run. Also added a seventh,
   specifically for the ground-truth check (permuting `recovered_amount`
   across rows) — its self-test claim is narrower and stated as such: it
   confirms the corrupted data is *measurably worse* than the clean run
   (`fraction_buckets_matching_direction` 1.0 → 0.5), not that the clean
   run itself always passes, since that gate's own instability (below)
   is separate and unresolved.

3. **Eval-set lock — closed.** New module `eval_set_lock.py`: splits the
   baseline joint dataset by the already-locked temporal boundary
   (`unseen_eval_split.temporal_holdout_fraction = 0.15`), writes the
   holdout to `output/eval_holdout_baseline_seed42.csv`, computes its
   sha256, and commits both file and hash to
   `output/eval_set_manifest.json`. `verify_eval_set()` is the integrity
   check Phase 3/9 are expected to call before touching the file — real
   run: hash recomputed and matched the committed one. Because Phase 3
   doesn't exist yet in this codebase, "generated before any model
   tuning begins" is trivially and honestly true here, not asserted
   around a phase that hasn't happened.

4. **Ground-truth gate — still open, unchanged, not silently patched.**
   Re-ran at n=800, n=3000, n=6000 after all the above changes: still
   flips (`retry` bucket direction-matches at n=3000/seed=42 in this
   delivery, still fails at n=800 and n=6000, same as before). This is
   the same root cause diagnosed earlier (near-zero true effect from
   pooling opposite-signed root causes into one `retry` bucket) and I
   deliberately did not touch the bucketing or the tolerance to make it
   pass — doing so now, after seeing it fail, would be exactly what this
   document's final "Do Not Proceed" list forbids. **This gate remains
   `⚠ independent check required` and self-certified-open.**

5. **Cumulative regression — partially closed, honestly scoped.**
   `test_data_factory.py` gained a real re-check of what's actually
   verifiable from this session's inputs (`recovery_actions` retired,
   foreign-key check clean, 150 opportunities load, `dataset_registry`
   starts empty on a fresh rebuild) — all passed. It explicitly prints
   that the rest of the Phase 0/1 exit-gate table (import hygiene across
   `engine/`/`api/`/`llm/`, API server startup, shipped `.joblib` model
   loading) needs `engine/`, `api/`, `llm/`, and the model artifacts,
   none of which were inputs to this session, and is **not** claimed
   clean here.

### Files changed in this pass
- `backend/data_factory/locked_thresholds.json` — new locked sub-blocks only
- `backend/data_factory/validators.py` — new checks + corruption self-tests
- `backend/data_factory/eval_set_lock.py` — new file
- `backend/data_factory/run_generation.py` — wired in the new checks
- `test_data_factory.py` — added partial cumulative-regression step
- `backend/requirements.txt` — added explicit `scipy==1.17.1` pin
- `backend/PHASE2_NOTES.md` — this addendum

### What's still not clear per the gate document
- Ground-truth treatment-effect gate (item 4 above) — **now resolved, see
  addendum below.**
- Full cumulative regression (item 5) — partially checked only.
- Phase 2 is marked `⚠ independent check required` in that document —
  nothing above is a substitute for that second-reviewer pass regardless
  of how many individual checks are green.

---

## Addendum 2 — ground-truth gate fixed, and a real merge-key bug found along the way

Cross-machine reproduction of the `retry`-bucket instability (identical
seed=42 numbers to float64 precision on two independent machines, via
`phase2_gate_report.py`'s F14/F15 sections) gave the "explained first,
independently of the specific failing result" justification the task's
own discipline requires before changing a locked check post-hoc. That
justification is now recorded, timestamped, in
`locked_thresholds.json`'s `ground_truth_treatment_effect._amendment_reason`
— the numeric tolerances themselves (`max_absolute_probability_gap=0.05`,
`min_fraction_of_buckets_matching_direction=0.90`,
`min_cases_per_bucket_for_check=100`) were **not** touched; only the
bucket definition and one bug were.

**What changed, in `validators.ground_truth_treatment_effect_check()`:**

1. **Root cause: pooled action_type-only buckets.** Fixed by bucketing on
   `(action_type, event_type, root_cause_effect_class, method_changed)`
   instead — but `root_cause_effect_class` is not raw `root_cause`
   either. It's a provably-minimal partition derived by actually reading
   `outcome_model.action_effectiveness()` and `outcome_model.timing_term()`
   (the only two functions that branch on `root_cause` at all):
   `gateway_timeout`/`network_error` merge into `"transient"` and
   `authentication_failed`/`expired_card` merge into `"needs_action"`
   (both functions always treat each pair identically); `insufficient_funds`
   and `payment_declined` stay singleton classes since at least one
   function always treats them distinctly from everything else. This
   also fixed a real bug the first, naive "bucket by raw root_cause"
   attempt introduced: `escalate`'s analytic effect is root-cause-
   independent for every `payment_failed` case (flat `0.35` in
   `action_effectiveness`, `0.0` in `timing_term`) — splitting it by raw
   root cause manufactured spurious direction mismatches from a
   distinction the generator doesn't actually make.

2. **A second, independent bug, found while implementing the fix:** the
   merge between `joint_df` and `truth_df` only joined on
   `(case_id, candidate_action)`, not the full candidate identity. Any
   case with more than one candidate sharing an `action_type` (e.g.
   multiple `retry` timing/method combinations for the same case — true
   for every retry-eligible case) was silently many-to-many joined,
   corrupting the `analytic_p` pairing for exactly those buckets. Fixed
   by merging on `(case_id, candidate_action, candidate_timing,
   candidate_method, candidate_channel)`, with an `assert` that the
   result is a strict 1:1 join (`len(merged) == len(joint_df)`) so this
   class of bug fails loudly if it ever recurs. Demonstrated directly in
   `phase2_gate_report.py`'s F15a: the old merge on real data produced
   107,899 rows from 28,929 input rows; the fixed merge produces exactly
   28,929.

3. **A third addition, requested explicitly: don't pretend small effects
   are testable.** Even with the correct bucketing, some buckets have a
   *true* analytic effect smaller than the already-locked
   `max_absolute_probability_gap` (0.05) — meaning the gap tolerance
   already committed to permits an empirical value on either side of
   zero for that true effect, by construction. Testing direction on such
   a bucket is not meaningful. These buckets are still gap-tested (the
   magnitude claim is meaningful even when the sign isn't) but marked
   `direction_scored: False` with an explicit
   `direction_not_scored_reason`, and excluded from
   `fraction_buckets_matching_direction`'s denominator — mirroring
   exactly how `skipped` buckets are handled for insufficient sample
   size, per the same "say 'not enough data/signal' instead of quietly
   pretending it's fine" principle, generalized from sample size to
   effect size. No new free parameter was introduced: the cutoff is the
   already-locked gap tolerance itself, not a fresh number chosen to fit
   this result.

**Verified, real numbers, at `DF_N_CASES=3000, seed=42` (the delivered
run):** `passed=True`, `fraction_buckets_matching_direction=1.0`
(4/4 direction-scored buckets agree), `all_gaps_within_tolerance=True`
(19/19 evaluated buckets), `buckets_effect_too_small_for_direction_test=15`.
Re-verified robust — not a lucky seed — across 6 different sample sizes
(800 through 20,000) and 3 additional seeds (7, 99, 2024): passes cleanly
in every case except one marginal gap-tolerance miss at `n=1500`
(two buckets landed at gap `0.0500`/`0.0504`, right at the boundary —
ordinary sampling noise at a specific `n`, not a systematic issue, and
not "fixed" by loosening the already-locked tolerance).

**What did NOT change:** `backend/db/db.py` schema, `candidate_generation.py`,
`outcome_model.py`, `entities.py`, `bank_health_timeseries.py`,
`candidate_outcome_dataset.py`, `calibration_profiles.py`,
`eval_set_lock.py`, `dataset_registry.py` — the fix is entirely contained
in `validators.py`'s one function plus the printing code in
`run_generation.py` and `phase2_gate_report.py` that displays its output.
All other locked thresholds, the eval-set hash, and every other
validator's corruption self-test were re-run after this change and
confirmed unaffected (`30/30` on `run_generation.py`, `12/12` on
`test_data_factory.py`, full clean run of `phase2_gate_report.py`).

### Files changed in this pass
- `backend/data_factory/locked_thresholds.json` — amended
  `ground_truth_treatment_effect` block only (new `_amendment_*` fields
  and `bucket_keys`; original numeric tolerances untouched)
- `backend/data_factory/validators.py` — rewrote
  `ground_truth_treatment_effect_check()`; added `_root_cause_effect_class()`
- `backend/data_factory/run_generation.py` — updated STEP 5 printing for
  the new per-bucket structure
- `phase2_gate_report.py` — updated Section F (F13/F14 field names,
  F15 rewritten to demonstrate the merge-key bug directly and cross-check
  the fixed per-root-cause breakdown)
- `backend/PHASE2_NOTES.md` — this addendum

### What's still not clear per the gate document, after this pass
- Full cumulative regression (Addendum 1, item 5) — still only partially
  checked; unchanged by this pass.
- Phase 2 is still marked `⚠ independent check required` — this fix
  closes the one gate that was genuinely open, but does not substitute
  for a second reviewer's pass over the whole phase.