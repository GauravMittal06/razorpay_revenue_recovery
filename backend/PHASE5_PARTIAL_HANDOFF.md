# Phase 5 (PARTIAL) → continuation Handoff

Standalone; read with `SoT.md`, `EXECUTION_PLAN.md`, `STATE_AND_DECISIONS.md`,
`FILE_INVENTORY.md`, `backend/PHASE5_NOTES.md`, `backend/PHASE4_HANDOFF.md`.

**Phase 5 is NOT complete.** W6 (scheduled dispatch) and W7 (shared pipeline
unification) remain. This document exists so a new session can resume from
exactly this point without reconstructing anything from chat history.

`PHASE5_NOTES.md` holds the full detail of everything summarised here. This
document points at it rather than duplicating it.

---

## 1. Status: W0–W5 complete, plus unplanned work. W6 and W7 outstanding.

    HEAD                68ff8e5
    Full suite          347 total, 335 passed, 12 failed, 0 skipped
    Failing set vs W0   16 -> 12   (0 new failures at any point)

The W0 baseline (`git 866e478`, evidence
`backend/tests/evidence/gate_report_20260902T043052Z.json`) had 16 failures.
Four were resolved this session:

| Resolved | How |
|---|---|
| `test_phase1_concurrency.py::test_recovery_update_is_guarded_by_the_status_it_read` | compare-and-swap in `mark_opportunity_recovered()` |
| `test_phase1_concurrency.py::test_concurrent_recovery_confirmations_produce_one_winner` | same fix |
| `test_phase1_concurrency.py::test_two_overlapping_batch_cycles_do_not_double_act_on_one_case` | `engine/opportunity_lock.py` |
| `test_phase4_optimizer.py::test_higher_true_incremental_value_ranks_above_lower` | replaced by `tests/test_phase4_ranking_correctness.py` (closeout C1) |

**Closeout list (`PHASE5_NOTES.md` §1a): no open items.** C1 fixed by
re-implementation; C2 withdrawn as a probe artefact. The list's exit rule was
widened from two routes to three — *fixed*, *formally retired with a recorded
reason*, or *the finding withdrawn as erroneous with the diagnosis recorded* —
because C2 left by the third. An item may still never leave by being absorbed
into the known-failure count. Both entries remain in place with their evidence
rather than being deleted.

---

## 2. What is DONE (summary only — detail in `PHASE5_NOTES.md`)

**Planned, W0–W5:**

- **W0/W1** — pre-change baseline captured; 25-scenario golden decision corpus
  frozen with a passing negative control (perturbing `MAX_RETRIES` is caught as
  18 drifted fields, including key-presence changes). §1a, and
  `tests/golden/phase5_decide_action_golden.json`.
- **W2** — `engine/phase5_config.py`: all Phase 5 bounds declared and dated
  *before* the evaluations that check them, enforced by explicit raises rather
  than `assert` (stripped under `python -O`).
- **W3** — `decide_action()` gains `ranked_candidates`. The pre-Phase-5 body is
  **byte-identical** to its original (sha `db42c1528bb2223c`, 6700 chars); the
  ranked path recurses once with `ranked_candidates=None` to obtain the
  authoritative verdict. Six rulings recorded at §1b.
- **W4** — runtime disable proven by a mid-run flip: the whole golden corpus
  reproduces at 0 differing fields with the switch off, against a negative
  control showing 12 of 25 scenarios diverge with it on.
- **W5** — execution lifecycle, `scheduled_for`, `candidate_id`, `selected=1`;
  `payment_link` made dispatchable; `do_nothing` no longer writes a fabricated
  execution row. §1c.

**Unplanned, added this session:**

- **Network health wired live** (§1d–1f) — `payments.bank`/`psp`, a seeded
  health series, and the unix → simulated-hour mapping. `network_health_known`
  moves from `0.0` to `1.0` on every live scoring.
- **C1 resolved** (§ closeout) — ranking correctness re-implemented like-for-like.
- **Two concurrency defects fixed** — see §4 below, which is the part W6 must
  read before writing any code.

---

## 3. What is NOT done

### W6 — scheduled dispatch (`engine/dispatch_scheduled.py`, new)

Scope, from `EXECUTION_PLAN.md` Phase 5: a periodic sweep, *structurally
identical in pattern to the existing batch loop*, and **the only component
permitted to advance a scheduled execution to dispatched/executed**. Gate
requirements (`Phase_Acceptance_Test_Gates.md` Phase 5): a scheduled action due
in the past executes when the dispatcher runs; one due in the future is
untouched; one whose opportunity was stopped or escalated by another path
before its due time is **abandoned rather than blindly fired**; and firing the
same execution twice produces exactly one execution, not a duplicate action.

The write side is already in place — `execute_action()` writes `state='scheduled'`
with `scheduled_for = now + timing_hours*3600` for any non-immediate candidate,
and the closed `EXECUTION_STATES` vocabulary already contains all seven states.
W6 supplies the sweep that advances them.

### W7 — shared pipeline unification

Scope: `core_loop.py`, `trigger_event.py` and `handle_customer_reply.py`
currently each call `classify → decide_action → execute_action` independently.
The gate requires this be **one shared function called by all three**, verified
*structurally* (a single shared function is called by all three entry points),
not merely by matching output, so a future change to one cannot silently
diverge from the others.

---

## 4. HARD CONSTRAINT — read before writing W6

> ### `execute_action()` is NOT idempotent at the call level.
>
> **W6's dispatcher must advance an existing execution row with an `UPDATE`.
> It must NEVER re-call `execute_action()`.**

Measured, not assumed. Calling `execute_action()` twice with the same decision
dict produces **two decisions and two executions**:

    call 1 decision_id      : 1
    call 2 decision_id      : 2
    recovery_decisions rows : 2
    recovery_executions rows: 2

The `UNIQUE` index on `recovery_executions.decision_id` does **not** prevent
this, because each call mints a *new* decision row to hang the execution off.
What the index *does* prevent — verified separately — is a second execution row
for an **existing** decision:

    second execution row for the SAME decision_id: REJECTED
      -> UNIQUE constraint failed: recovery_executions.decision_id

That index is therefore exactly the mechanism the idempotent-dispatch gate can
be satisfied through, **and only if the dispatcher updates rather than
re-executes**. If W6 is ever implemented by re-calling `execute_action()`, an
idempotency key on the decision becomes mandatory and the gate cannot be met
without one. The assumption is written down at
`tests/test_phase5_execution.py::test_calling_execute_action_twice_creates_two_decisions_not_one`,
which asserts the limitation rather than a guarantee that does not exist.

### Second constraint — the optimizer must stay OUTSIDE `opportunity_lock`

Measured per opportunity:

    lock hold as used today (decide_action + execute_action)   p50   5.88 ms
    optimize_opportunity() alone, warm                         p50 644    ms

Putting ranking inside the lock takes the hold time up ~110x. Against
`db.BUSY_TIMEOUT_MS = 5000` that is the difference between ~850 workers able to
queue before one times out and about **7** — i.e. between invisible contention
and the eighth concurrent worker crashing with "database is locked". The
correct shape is in the `opportunity_lock.py` docstring. A ranking computed
outside the lock can be stale, and that is safe by construction: `decide_action()`
re-adjudicates every candidate against fresh state *inside* the lock. A stale
ranking can cost optimality; it cannot cost compliance.

---

## 5. `opportunity_lock.py` was built FOR W7 — adopt it unchanged

`engine/opportunity_lock.py` exists because `core_loop.py` and
`handle_customer_reply.py` both had a proven double-contact race, and because
those two files are due to be unified by W7. It was deliberately written as a
shared helper rather than inline in the batch loop, so **W7's unified pipeline
should call it unchanged rather than reworking, re-implementing or inlining
it.** Putting the atomicity inline would either be moved again by W7 or leave
one entry point exposed.

It adds **no compliance logic** and re-derives no rule. Cooldown stays in
`decide_action()`, which remains sole compliance authority; the lock only makes
that authority's read-then-act indivisible. A guard that re-checked cooldown
inside it would be a second component enforcing the same rule — the authority
drift the invariants forbid.

`trigger_event.py` does **not** use it and does not need it: it mints a fresh
`opportunity_id` per call, so concurrent calls touch different rows, and
duplicate delivery of one upstream event is already guarded by the `UNIQUE`
index on `ingestion_event_id` plus an `IntegrityError` handler that resolves to
the winner. W7 should preserve that asymmetry rather than applying the lock
uniformly for tidiness.

---

## 6. Frozen inputs — reuse unmodified

| Path | Status |
|---|---|
| `backend/engine/optimizer_config.py` | **FROZEN** |
| `backend/engine/intervention_cost.py` | **FROZEN** |
| `backend/data_factory/candidate_generation.py` | **FROZEN.** Compliance belongs to the rule engine; add no eligibility rules here |
| `backend/ml/inference.py` | **FROZEN.** Still the only scoring path |
| `backend/ml/outcome_features.py` | **FROZEN** |
| `backend/ml/models/*.joblib` | **FROZEN.** Read and introspect freely; never modify, retrain or overwrite without an explicit ruling |
| `backend/engine/optimize.py` | **ONE NARROW EXCEPTION, GRANTED AND NOW CLOSED** — see below |

### The `optimize.py` exception, and its exact limits

Granted 2026-09-03 for **three lines only** in `build_optimizer_context()`:
`bank=None`, `psp=None`, `decision_time_hours=0.0` now read the real channel
off the latest payment. It was justified on the verified fact that `bank`,
`psp` and `decision_time_hours` are **not model features** — none appears in
`outcome_features.ALL_FEATURES` — so the change reaches the model through
exactly four `network_health_*` features and nothing else.

**This exception is closed and authorises nothing further.** Any other
modification to `optimize.py` needs its own ruling.
`tests/test_phase5_network_health.py::test_the_frozen_exception_stayed_narrow`
fails if any of the three ever becomes a model feature, because that is the
premise the exception rests on.

---

## 7. Known-failing tests the next session will see (12)

Nothing here is a new regression. All 12 were present at the W0 baseline.

**Environment / provenance (pre-date Phase 4):**

1. `test_phase0_bootstrap.py::test_installed_versions_match_the_pins`
2. `test_phase0_bootstrap.py::test_test_tooling_is_not_mixed_into_runtime_requirements`
3. `test_permanent_gates.py::test_exposed_key_history_exposure_is_documented`
4. `test_permanent_gates.py::test_seed_generator_persists_its_own_provenance`
5. `test_permanent_gates.py::test_training_corpus_content_hash_is_recorded_somewhere`

**Code-convention gates (pre-date Phase 4):**

6. `test_permanent_gates.py::test_no_syspath_manipulation_remains`
7. `test_permanent_gates.py::test_relative_or_bare_intra_project_imports_are_absent`
8. `test_permanent_gates.py::test_no_broad_handler_discards_the_failure_silently`
9. `test_permanent_gates.py::test_no_new_silent_swallow_beyond_the_recorded_findings`

**Known false positive (pre-dates Phase 4):**

10. `test_permanent_gates.py::test_method_change_has_no_reachable_executor_path` —
    substring-matches `"method_change"` inside the legitimate `"method_changed"`
    feature key in `data_factory/` and `ml/`. **Worth understanding before
    "fixing":** there is no `method_change` action type in this system at all.
    A method change is `action_type="retry"` carrying a `method` different from
    the opportunity's current one. Tightening the match to a word boundary
    clears the false positives but the test still proves nothing, because the
    token it searches for structurally cannot appear. The boundary is verified
    for real by the behavioural and structural tests in
    `tests/test_phase5_fallthrough.py`. See `PHASE5_NOTES.md` §0.1.

**Branch-reachability (pre-dates Phase 4):**

11. `test_compliance_regression.py::test_every_branch_is_reachable_and_distinct`

**Disclosed, unresolved:**

12. `test_phase4_optimizer.py::test_end_to_end_latency_against_the_declared_budget` —
    p50 ~760ms / p95 ~890ms against a declared 250ms budget. ~99.7% is
    single-row model inference through frozen `ml/inference.py`; batching was
    measured at 6.6x but requires touching that module and re-running Phase 3's
    parity gate. This is why the optimizer stays OFF at both
    request-synchronous entry points (`OPTIMIZER_ENABLED_BY_ENTRY_POINT`).

---

## 8. Integrity statement

**No threshold was loosened to obtain a pass.** Two gates were amended, both
dated and evidence-backed with the reason recorded in the test's own docstring:

- `test_executor_action_set_matches_the_decider` was pinned to the four
  pre-Phase-5 actions and tripped when `payment_link` was added, exactly as the
  plan predicted. It now asserts `STATUS_MAP` against
  `phase5_config.EXECUTABLE_ACTIONS` — the declared vocabulary — instead of a
  second hardcoded literal. **Strictly stronger than what it replaced:**
  executor and declaration can no longer drift in either direction.
- `test_live_context_has_no_network_health_and_says_so_explicitly` was a Phase 4
  tripwire pinning the very limitation Phase 5 was ruled to close. It failed
  because it was working. Renamed and **inverted rather than deleted**, so the
  property stays pinned in its new direction.

**C1's bar was not moved.** The new test asserts at *two* operating points
(locked floor 0.05 / bar 0.85, and G7's floor 0.12 / bar 0.90) precisely so the
result cannot depend on which bar is chosen. The locked definition it
implements predates Phase 4.

**Two findings were retracted with full evidence rather than deleted.** C2 —
the `payment_history_score` "sign inversion" — was a probe artefact: the probe
swung outside the training corpus's observed range. This is recorded as the
**second** occurrence of that error class, after Phase 4's G7 retraction, in
`STATE_AND_DECISIONS.md` where it is visible across phases rather than buried in
one phase's notes. The mechanical guard is now structural:
`test_everything.py` refuses to run a directional probe outside
`TRAINING_SUPPORT` and re-derives those bounds from the training corpus.

**Errors made and corrected during the session are recorded, not smoothed
over:** a seed-generation change that silently regenerated the entire seed set
before being made strictly additive; an `is`/`==` comparison on ints that
passed by accident at one value and failed at another; a ranking gate whose
health lookup read ambient DB state, so its measured number varied by checkout
(0.8886 vs 0.8890) while passing either way; and a latency figure (22.9ms)
reported from a probe that had escalated its own opportunities, collapsing the
candidate set from ~11.6 to 1. Each is described in `PHASE5_NOTES.md` or the
relevant commit message.

**Verification.** `test_everything.py` at the repository root is the standalone
Phase 0–5 check — run `python test_everything.py` (add `--quick` to skip Phase 2
generation). On a clean checkout with the gitignored model artifacts present it
reports **69 checks, 64 matched, 5 disclosed, 0 skipped, 0 unexpected** in
~65s. It touches no git state and writes nothing outside a temporary directory.

The five disclosed items are: the `'executed'` token shared by both closed
vocabularies; the latency miss; the 12 known pytest failures listed by name;
`execute_action()`'s call-level non-idempotency; and C2 recorded as retracted
so it cannot be re-raised. The script also prints the four tests resolved
during Phase 5, so a reader comparing against the older 16-failure baseline
sees why the count dropped rather than wondering what was silenced.

**Phase 5 sign-off has NOT been given. W6 and W7 have not been started.**
