# Phase 6 — Live Experiment Assignment & Outcome Observation

Working record. Written as the phase runs, not reconstructed after it.

    X0  locked bounds declared before the work they govern          DONE
    X1  stale artifacts regenerated; baseline captured              DONE
    X2  assign_experiment_group.py + creation-time hook             --
    X3  control suppression in the rule engine                      --
    X4  observe_outcome.py as the single ingestion path             --
    X5  assignment volume + the two hard gates                      --
    X6  close: notes, inventory, carried-forward closeout           --

---

## 1a. PROJECT CLOSEOUT LIST — must be resolved before final sign-off

> **Carried forward verbatim from PHASE5_NOTES.md section 1a.** It is not a
> Phase 6 to-do list. Items here are deferred by explicit ruling, not
> forgotten, and each must be either resolved or formally retired with a
> recorded reason before the project is signed off. An item may leave this
> list in exactly three ways: it is fixed, a ruling retires it and the reason
> is written down, or the finding is withdrawn as erroneous with the diagnosis
> recorded. It may never leave by being absorbed into a "known failures" count
> and stopping being counted.
>
> **Status entering Phase 6, 2026-09-04: three open items (C3, C4, C5),
> inherited unchanged from Phase 5.** See PHASE5_NOTES.md section 1a for each
> item's full text and evidence.

| # | Item | Status | Ruled |
|---|---|---|---|
| C3 | `payment_link` is dispatchable but has no delivery path (`deliver_message.ELIGIBLE_ACTIONS` is `{retry, reminder}`), so a dispatched payment_link produces no customer-visible artifact. | **OPEN**, inherited. Fixing it means deciding what a payment-link message *is* — a product question. Untouched by Phase 6. | 2026-09-03 |
| C4 | The first `opportunity_lock` hold in any process is ~780 ms, not ~6 ms, because `decide_action()` loads the ML model lazily inside the lock. | **OPEN**, inherited. Phase 6 adds a suppression branch that returns *before* `_load_ml_model()` for control opportunities, which narrows but does not close this: treatment opportunities still pay it. Not claimed as a fix. | 2026-09-04 |
| C5 | `test_method_change_has_no_reachable_executor_path` fails with 2 offenders, both display labels in an offline Phase 3 evaluation script with no executor path. | **OPEN by ruling**, inherited. Left failing rather than rescoped to pass. Remains one of the 12 known failures. | 2026-09-04 |

**Phase 6 opens no new closeout items as of X1.**

---

## X0 — the locked bounds

Committed before `assign_experiment_group.py` existed and before a single
opportunity had been assigned. Full parameter set in
`engine/phase6_config.py` and the dated `phase6_experiment_assignment` /
`phase6_counterfactual_consistency` blocks of
`data_factory/locked_thresholds.json`; the two are asserted equal by
`tests/test_phase6_config.py` so neither can drift.

The rulings, and what each rejected:

| Ruling | Chosen | Rejected, and why |
|---|---|---|
| Randomization | R2, blake2b hash bucketing over (salt + opportunity_id) | R4 stratification, as premature — it buys balance there is no evidence of needing. If X5's gate fails, escalating to R4 is the documented response with that failure as evidence. Seeded RNG rejected outright: result depends on call order, so it is wrong under concurrency. `SystemRandom` rejected: unauditable. |
| Holdout | 0.5 | 0.20, revised up for power. No real revenue is sacrificed by a larger control arm in a synthetic system, and 51 of 150 seeded opportunities are already terminal. |
| Suppression outcome | new `suppressed_holdout` in `DECISION_OUTCOMES` | Reusing `flagged_manual_review` (would inject the whole control arm into the manual-review queue); writing no decision row (violates "every declined action is logged with a reason", and leaves the counterfactual gate nothing affirmative to inspect). |
| Unassigned opportunity | not in the experiment, **not** suppressed | Fail-closed. It would freeze all 150 pre-Phase-6 opportunities, none of which was ever randomized. Recorded as a dated, deliberate fail-**open** exception rather than left looking like an oversight. |
| Balance covariates | `amount_at_risk`, `diagnosis` (8 levels), `is_payment_failed` | `root_cause` with a NULL level — collinear with `event_type` and blind to a checkout_abandoned/invoice_overdue split. Full `event_type` — two of its three levels are identical partitions of `diagnosis` levels and would be gated twice. |
| SMD bound | 0.10 | 0.20. The first draft conflated the covariate-balance convention with Cohen's small-**effect-size** convention, which measures a different construct. Corrected before anything was locked and before any balance figure had been computed. |

`is_payment_failed` is the one deliberate overlap between covariates, and it
earns its place on a real property: per-level balance does not imply balance
on a coarsening. Six root-cause levels each off by +0.03 in the same
direction every one clear 0.10 individually while their union is off by 0.18.
`diagnosis` cannot see that; the binary can.

---

## X1 — the stale artifacts, and what regenerating them exposed

### 1. The repository could not build its database from a clean checkout

`python -m backend.db.db` — the documented bootstrap step (EXECUTION_PLAN
Section 11) — failed outright:

    sqlite3.ProgrammingError: You did not supply a value for binding
    parameter :bank.

Commit `a7cd6d2` (2026-09-03, "Wire network health into live scoring") added
`bank`/`psp` to the `payments` INSERT in `db.py` but never regenerated the
tracked `data/payments.json`, which still carried the pre-Phase-5 17-key
shape. Every checkout since has been unable to load its own seed data. It
went unnoticed because nobody rebuilt: the working `db/recovery.db` predated
the commit and the test suite seeds its own fixtures.

Fixed by regenerating, which is what X1 was already required to do. **No code
change was needed** — `generate_seed_data.py` already emits both columns.
`data/bank_health_observations.json` (12,960 rows) is now tracked for the
same reason: without it a clean checkout silently loses network health again,
which is the exact failure mode this section is about.

### 2. A retracted finding — the "missing bank/psp columns" defect

An earlier Phase 6 planning report filed this as a live code defect (`bank`
and `psp` added to `CREATE TABLE` but absent from `ADDITIVE_COLUMNS`, so
network health was silently dead at serving time) and proposed a migration
patch. **That finding was wrong and is withdrawn.**

The database file it was measured against, `backend/db/recovery.db`, had
mtime `2026-09-01T21:18`, roughly 30 hours *older* than the `a7cd6d2` commit
that added the columns. It was a stale artifact being read as evidence about
the code. Corroborating: `.claude/worktrees/phase5-rule-engine`'s database,
created after that commit, has both columns.

Recorded here rather than deleted, per this project's rule that a withdrawn
finding keeps its diagnosis. What survives from it is only the operational
fact in §1 above.

### 3. Verified after regeneration

| Check | Result |
|---|---|
| `payments` has `bank`, `psp` | yes |
| `bank`/`psp` populated | 177 / 177 |
| `opportunities` has `outcome_source` (X0 additive column) | yes |
| `bank_health_observations` loaded | 12,960 |
| `network_health_known` across all in-flight opportunities | **1.0 on all 92** |
| rolling health score | 53 distinct values in [0.4434, 0.9457] |

The 92 and the 53 distinct values reproduce STATE_AND_DECISIONS.md's
network-health closure record exactly. The interval differs from the recorded
`[0.5425, 0.9846]` because `decision_time_hours` is derived from wall-clock
time, so a different slice of the 720-hour series is read on a different day.
The closure claim is corroborated, not contradicted.

### 4. Population baseline, on the rebuilt database

    SELECT status, COUNT(*) FROM opportunities GROUP BY status;
      escalated 7 | open 71 | recovered 37 | recovering 21 | stopped 14   (150)

    SELECT resolution_type, COUNT(*) FROM opportunities GROUP BY resolution_type;
      NULL 99 | recovered 37 | stopped 14

| Bucket | n | Meaning |
|---|---|---|
| Terminal | 51 | outcome written, `resolved_at` set (37 recovered + 14 stopped) |
| In flight | 92 | `open` + `recovering`; the set `core_loop.run_cycle()` scans |
| Escalated, outcome pending | 7 | handed to a human queue; `resolution_type` NULL by design |

An earlier draft of the Phase 6 plan reported "58 resolved", counting the 7
escalated rows as resolved and omitting the 21 `recovering` rows entirely.
Both were errors; 51 is the correct terminal count and `escalate` is
explicitly not a resolution (`execute_action.py`).

`experiment_assignment`, `recovery_decisions`, `recovery_executions` and
`recovery_candidates` are all empty — nothing in the baseline world has been
assigned or acted on, which is what makes X3's "unchanged when unassigned"
claim falsifiable.

### 5. Test baseline

**426 collected, 407 passed, 12 failed, 7 skipped** — identical before and
after regeneration, and the 12 are Phase 5's recorded known failures by name:

    test_compliance_regression.py::test_every_branch_is_reachable_and_distinct
    test_permanent_gates.py::test_exposed_key_history_exposure_is_documented
    test_permanent_gates.py::test_method_change_has_no_reachable_executor_path
    test_permanent_gates.py::test_no_broad_handler_discards_the_failure_silently
    test_permanent_gates.py::test_no_new_silent_swallow_beyond_the_recorded_findings
    test_permanent_gates.py::test_no_syspath_manipulation_remains
    test_permanent_gates.py::test_relative_or_bare_intra_project_imports_are_absent
    test_permanent_gates.py::test_seed_generator_persists_its_own_provenance
    test_permanent_gates.py::test_training_corpus_content_hash_is_recorded_somewhere
    test_phase0_bootstrap.py::test_installed_versions_match_the_pins
    test_phase0_bootstrap.py::test_test_tooling_is_not_mixed_into_runtime_requirements
    test_phase4_optimizer.py::test_end_to_end_latency_against_the_declared_budget

Phase 5 closed at 409 collected. The 17-test rise is X0's 16 plus one
parametrized import check picking up `phase6_config.py`. **Zero new failures
at any point in Phase 6 so far.** This list is the bar every later checkpoint
is measured against; a 13th failure is a Phase 6 regression, not an
inheritance.
