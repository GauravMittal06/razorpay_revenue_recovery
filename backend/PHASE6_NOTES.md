# Phase 6 — Live Experiment Assignment & Outcome Observation

Working record. Written as the phase runs, not reconstructed after it.

    X0  locked bounds declared before the work they govern          DONE
    X1  stale artifacts regenerated; baseline captured              DONE
    X2  assign_experiment_group.py + creation-time hook             DONE
    X3  control suppression in the rule engine                      DONE
    X4  observe_outcome.py as the single ingestion path             DONE
    X5  assignment volume + the two hard gates                      DONE
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

---

## X2 — assignment, at the one place opportunities are created

`engine/trigger_event.py:150` is the only `INSERT INTO opportunities` in the
engine; `db.py`'s `load_opportunities()` is the only other writer and is bulk
world-construction, not creation. `core_loop.run_cycle()` and
`handle_customer_reply()` both read rows that already exist. So there is
exactly one creation point, and the hook goes there.

### Where the call sits, and why each boundary matters

    conn.commit()                       <- opportunity + payment durable
    assign_experiment_group(...)        <- HERE
    run_recovery_pipeline(...)          <- first decision ever made

| Boundary | If it moved | Consequence |
|---|---|---|
| after the commit | before | the FK could not be satisfied; assignment orphaned |
| before the pipeline | after | an opportunity treated *then* assigned — a control-arm row carrying a treatment in its history |
| after both dedup short-circuits | before | a replayed upstream event re-randomizes a live opportunity |

It stays in the entry point rather than moving into `pipeline.py`, for the
reason that module's own docstring gives: an entry point's creation work stays
with the entry point, only the recovery pipeline is shared. The other two
entry points must never assign, and a static gate now enforces that.

### The randomness is not in this module

The draw is `phase6_config.assigned_group()` — a pure function of the id and
the locked salt. `assign_experiment_group.py` is the persistence half only.
That split is what makes an assignment auditable: any row's group is
recomputable from its id plus the committed salt, without this module, without
the database, years later. A test asserts the stored group equals the derived
one across 40 opportunities, so the module cannot start drawing its own
randomness without failing.

`get_assignment()` deliberately does **not** fall back to deriving a group for
a row with no assignment. A reader that did would silently enrol the entire
pre-Phase-6 population into an experiment it was never randomized for, and
every one of those 150 rows would then count toward an incremental number it
has no business informing.

### Assign once, and the guarantee is the schema's

`experiment_assignment.opportunity_id` is the PRIMARY KEY. The SELECT fast path
has a check-then-insert race window and is not the guarantee; the primary key
is, and the `IntegrityError` handler is what makes the two agree under
concurrency. Two constraints reach that handler and need opposite answers —
the primary key means "a concurrent caller won, return the row that won", the
foreign key means "no such opportunity, assign nothing" — so the handler
resolves by re-reading rather than by trusting the exception type.

A repeat call does not move `assigned_at`. The original assignment instant is
what the experiment is anchored to, and the counterfactual gate compares
activity against it.

### Static authority gates

Added to `test_permanent_gates.py`, reusing the existing optimizer machinery
(`FORBIDDEN_AUTHORITY_NAMES`, `FORBIDDEN_AUTHORITY_MODULES`,
`WRITE_STATEMENT`) rather than a parallel rule set that could drift from it:

- the module exists where the checks expect it (else they scan nothing and pass)
- it imports nothing with execution authority
- it writes only `experiment_assignment`
- it never references an outcome column — an assigner able to resolve an
  opportunity could manufacture the very result the experiment measures
- `assign_experiment_group()` is called from no entry point but `trigger_event`

**Verified by mutation, not by assertion.** A probe adding
`UPDATE opportunities SET resolution_type ...` and
`from backend.engine.execute_action import execute_action` to the module was
temporarily appended; all three relevant gates failed, and passed again once
it was removed. A gate never seen to fail is not evidence.

`observe_outcome.py` joins `PHASE6_WRITABLE_TABLES` at X4. Listing it before it
exists would fail the existence check for all of X2 and X3 — noise, not a gate.

### Suite

**444 collected, 425 passed, 12 failed, 7 skipped.** The 12 are the X1
baseline list, matched by name. 407 → 425 is this checkpoint's 18 new tests
(12 behavioural + 5 static + 1 parametrized import check). Zero new failures.

---

## X3 — control suppression, and why it lives in the rule engine

The gate is the **first statement** of `decide_action()`'s hardcoded body.

### Placement is the whole design

`decide_action()` is the only function that can set `allowed: True`, and it is
the single choke point BOTH live paths already pass through:

| Path | Reaches decide_action via |
|---|---|
| batch / trigger_event / customer_reply | `pipeline.run_recovery_pipeline()` |
| the dispatcher | `dispatch_scheduled._still_permitted()` — **not** the pipeline |

A suppression implemented in `pipeline.py` would have left the second path
open, so an action scheduled before assignment could still fire afterwards.
Putting it in the rule engine meant `dispatch_scheduled.py` needed **no code
change at all**: a control opportunity's queued action is abandoned and the
`state_reason` records `suppressed_holdout`. That is the test at
`test_a_scheduled_action_for_a_control_opportunity_is_abandoned`.

Being *first* matters separately. Every branch below either grants permission
or reads state to decide whether to; a suppression running after any of them
would depend on branch ordering rather than on the arm. It also returns before
`_get_history()` and before `_load_ml_model()`, which **narrows closeout C4 but
does not close it** — treatment opportunities still pay the lazy model load.

`action_type` is `None` on purpose: no action was blocked, because none was
ever selected. Naming one would put an action the system never considered into
the audit trail.

The ranked path needed no change either. It recurses into this body for its
authoritative `baseline`, and already returns that baseline unchanged when it
is not-allowed and the outcome is not `blocked_contact_hours`. A suppression is
both.

### A defect this phase shipped at X2 and caught at X3

`phase6_config._check()` runs at import and imported `trigger_event` for a
vocabulary assertion, creating

    trigger_event -> assign_experiment_group -> phase6_config -> trigger_event

`import backend.engine.trigger_event` failed outright in a fresh interpreter,
**while all 425 X2 tests passed** — collection imported `phase6_config` first
every time, and `sys.modules` caching hid it.

Fixed by moving the assertion into
`test_phase6_config.py::test_diagnosis_levels_cover_the_entry_points_accepted_vocabulary`,
which can import an entry point safely. Nothing was weakened; the check moved
to a place that can perform it. A configuration module must not import an entry
point at import time.

New permanent gate: `test_every_engine_module_imports_standalone`, parametrized
over all 18 engine modules, each in **its own subprocess** — because
same-interpreter caching is precisely what masked this. Verified against the
broken state before the fix.

### The parity amendment (dated 2026-09-04)

`test_trigger_event_parity_over_the_fixed_spec_list` began failing — the 13th
failure, a genuine regression rather than an inheritance. Cause: the legacy
side hand-inserts a fixed-id opportunity with **no assignment row**, while the
unified side calls real `trigger_event`, which now assigns. At a 0.5 holdout
roughly half the six specs were suppressed on one side only.

Note the mechanism precisely: the legacy side does **not** hash a fixed id into
an arm. It has no assignment row, so it is `unassigned -> not suppressed`
regardless of what the formula would derive for `opp_legacy_N`. Only one side
consults an assignment. The condition is

    diverged  <==>  the UNIFIED opportunity landed in control

Measured over 5 reps x 6 specs = 30 comparisons: 12 control, 12 diverged,
**diverged set == control set exactly**. Every spec diverged in at least one
rep and none in all reps, so it is not a property of any spec. The legacy ids'
derived buckets (treatment, control, treatment, treatment, control, control)
do not match the divergence pattern — which is the proof they are inert.

Fixed by giving the legacy row the same assignment the unified opportunity
received. Post-fix, over the same 30 comparisons: control arm 18 / 0 diverged,
treatment arm 12 / 0 diverged, control outcomes `['suppressed_holdout']`,
treatment outcomes `['executed']`. Tolerance still 0, same six specs, and the
check now covers both arms where before it only ever saw the treated one.

### Suite

**475 collected, 456 passed, 12 failed, 7 skipped.** The 12 are the X1 baseline
by name. 425 -> 456 is 13 suppression tests + 18 standalone-import checks.
Zero new failures.

---

## X4 — one ingestion path, and the divergence it closed

### There were three writers, and two of them had already drifted

| Writer | Wrote | Guarded? |
|---|---|---|
| `mark_opportunity_recovered()` | recovered / partial | **yes** — compare-and-swap against a concurrent terminal transition |
| `execute_action()` terminal `stop` branch | `recovered_bool=0, partial=0, resolution_type='stopped'` | **no** |
| `db.py load_opportunities()` | all outcome columns | n/a — bulk world construction |

The first two are live code answering the same question under different rules.
That is not a stylistic duplication: Phase 7 computes an incremental figure by
comparing recovery rates across arms, so a second route resolving
opportunities under different rules biases the comparison in a way nothing
downstream can detect — the rows look perfectly well-formed. The concrete
defect: a `stop` racing a recovery could overwrite a recovered case as
unrecovered, corrupting exactly the numerator Phase 7 divides by.
`test_a_stop_cannot_overwrite_an_already_recovered_case` now pins it.

**Unification adopted the guarded implementation unchanged rather than
averaging the two.** `observe_outcome()`'s compare-and-swap is
`mark_opportunity_recovered()`'s, verbatim.

The seed loader stays out **by name**, not by accident: it constructs a world,
it does not observe one. It is an explicit exemption in the static gate.

### What each caller became

- `mark_opportunity_recovered()` — a thin, clearly-labeled wrapper, retained
  exactly as EXECUTION_PLAN Phase 6 requires ("a legitimate, clearly-labeled
  operational utility"). Its legacy return shape is preserved exactly, because
  `api/actions.simulate_recovery` and the console depend on those strings.
  Records `source="manual_confirmation"`.
- `execute_action()`'s stop branch — calls `observe_outcome(...,
  resolution="stopped", source="executor_stop")`. The rule engine's authority
  is untouched: it still decides the case closes by policy, and this only
  records the consequence. The arrow points executor -> observer, never back.
- `mark_payment_recovered.py` — **deleted** (ruling 2026-09-04). It read and
  wrote `payments.recovery_status` / `payments.recovered_at`, neither of which
  exists in the Phase 1 schema, so any call raised `OperationalError`. Its
  name stays in `FORBIDDEN_AUTHORITY_NAMES` so nothing reintroduces a second
  recovery writer under it.

### The property that keeps the experiment honest

`observe_outcome` is **not experiment-aware**, and a static gate enforces it. A
control opportunity can and must be able to recover — that is the entire point
of a control arm. An outcome writer that consulted `experiment_assignment`
could suppress control recoveries and drive the measured incremental effect to
whatever number the system wanted, making the experiment circular.

### Two of the new gates were wrong on first run

Both were false positives from a write-statement regex over English prose:

1. `writes carries` — from the `observe_outcome` docstring sentence "The
   UPDATE carries its own precondition".
2. `trigger_event.py` flagged as a second outcome writer — its creation INSERT
   names the outcome columns to set them NULL. An outcome is by definition a
   later observation about a row that already exists, so a creation INSERT
   cannot record one.

Fixed by scanning source with docstrings and comments stripped
(`_code_without_prose()`), and by restricting the outcome gate to `UPDATE`.
Recorded because the failure mode is instructive: a gate that greps raw source
finds adjectives as readily as SQL.

**Verified by mutation.** Restoring the pre-X4 direct write to
`execute_action` makes `test_exactly_one_module_writes_a_business_outcome`
fail, naming `['recovered_bool', 'partial_recovery_amount',
'resolution_type']`; removing it passes again.

### A test amendment, dated 2026-09-04

`test_phase1_concurrency.py::test_recovery_update_is_guarded_by_the_status_it_read`
read `mark_opportunity_recovered.py`'s source for the compare-and-swap SQL.
That file now delegates, so the test's own `assert updates` fired on finding no
UPDATE at all — the 13th failure. The guarded property did not change; it
moved.

Amended to follow the write to `observe_outcome.py`, plus a second assertion
the original could not make: the wrapper must contain no outcome write of its
own. Strictly stronger — a future edit reintroducing a direct unguarded UPDATE
in *either* file fails here.

### An assumption, flagged rather than buried

`escalated_resolved` has **no producer**. It was named in a Phase 1 column
comment and no code path has ever written it. `STATUS_FOR_RESOLUTION` maps it
to a recovery, which is the natural reading of "the escalation was resolved" —
but a human could equally close an escalation without recovering anything. It
is recorded in-code as an assumption to be confirmed by whoever adds a
producer, not inherited silently from here.

---

## X5 — the two hard gates, and a floor that was locked too low

### The counterfactual gate passed on the first real population

Control 0 on every probe, treatment non-zero, and 1723 suppression rows
proving suppression *logs* rather than returning early. The treatment arm is
measured deliberately: "control shows nothing" is unfalsifiable on its own,
since a broken optimizer or a no-op executor would satisfy a control-only
check perfectly. `test_a_system_that_acts_on_nobody_does_not_pass` pins that.

### The balance gate failed at n=240 — and the failure meant nothing

First run, 240 opportunities: max |SMD| = 0.386, five breaches. The threshold
was **not** touched. Instead the question asked was whether the gate is
*passable* at that n, measured against the **known-correct** randomizer — the
real locked hash, over uuid4-shaped ids, 500 trials per n:

| n | trials | pass rate | 95% lower | median max&#124;SMD&#124; |
|---:|---:|---:|---:|---:|
| 240 | 600 | 0.33% | 0.09% | 0.2335 |
| 500 | 600 | 8.17% | 6.23% | 0.1600 |
| 1000 | 600 | 34.67% | 30.97% | 0.1126 |
| 1500 | 600 | 60.33% | 56.37% | 0.0924 |
| 2000 | 600 | 77.50% | 73.99% | 0.0787 |
| 2500 | 2000 | 88.00% | 86.50% | |
| 3000 | 2000 | 93.75% | 92.60% | |
| **3500** | **2000** | **97.00%** | **96.16%** | |
| 4000 | 2000 | 98.75% | 98.16% | |
| 4500 | 2000 | 99.25% | 98.77% | |

At n=240 a perfect randomizer fails this gate **99.67% of the time**. The cause is
analytic and independent of the result: SE(SMD) ≈ 2/√n, so at n=240 each
level's SMD has SD ≈ 0.13 while the gate takes the maximum over ten such
quantities against a 0.10 bound. **`MAX_ABS_SMD = 0.10` and `MIN_ASSIGNED_N =
200` were both locked at X0 and were mutually incompatible.** Setting the
floor without a power analysis was an error made at X0.

### What was amended, and what was not

`MIN_ASSIGNED_N`: **200 → 3500**. `MAX_ABS_SMD`: **untouched at 0.10**.

The distinction is the whole point. The bound that *judges* the result is
exactly what it was before any result existed. What moved is the precondition
for evaluating the gate at all, and it moved in the **conservative** direction
— more evidence is now required before balance may be certified, not less. It
also makes the constant finally do the job its own comment claimed: at n=240
the gate returned FAIL when the honest verdict was "cannot tell".

3500 is the smallest n whose **95% Wilson lower bound** clears a 95% pass
rate (97.00% observed, 96.16% lower). The lower bound rather than the point
estimate, because a Monte Carlo pass rate is itself an estimate and choosing
on the point estimate would clear the criterion by sampling luck about half
the time. n=3000 does not clear it (93.75% / 92.60%). **Not 4000 by default**
merely because that was the run that happened to pass.

### The analysis was unreproducible, and that had to be fixed first

The floor was very nearly locked on a single noisy run. A first pass at 500
trials put n=3500 at 98.0% (96.4% lower); a second put it at 96.0% (93.9%
lower), which does **not** clear the criterion — so the chosen floor moved
between two runs of the same seed.

The cause: `_draw_rows` minted ids with `uuid.uuid4()`, which reads
`os.urandom` and ignores `random.seed`. The entire Monte Carlo was
unreproducible. A locked threshold justified by a measurement nobody can
replay is not justified, whichever number it lands on.

Id generation is now seeded — `rng.getrandbits(48)`, the same 48 uniformly
random bits `uuid4().hex[:12]` provides, so the assignment hash sees an input
of identical shape and distribution — and verified stable across repeat runs
(185/200 twice at the same seed). The decision points were then re-measured at
**2000 trials**, and 3500 survives.

### The detection curve, and a reading error worth recording

The 95% criterion controls only the false-failure rate, so the same module
measures the other side. At n=3500, 300 trials:

| bias | induced &#124;SMD&#124; | vs bound | detection |
|---:|---:|:---|---:|
| null | 0.0130 | below | 3.7% |
| +0.10 | 0.0827 | below | 36.3% |
| +0.15 | 0.1312 | **ABOVE** | 80.0% |
| +0.20 | 0.1743 | **ABOVE** | 99.7% |
| +0.30 | 0.2671 | **ABOVE** | 100.0% |

**A first reading of this curve was wrong and is retracted.** It was
parameterised by the raw bias knob (probability units) rather than by induced
SMD (the gate's units), and concluded the gate was "nearly blind" to small
bias. It is not: a 0.05 bias induces only ~0.042 SMD and a 0.10 bias ~0.086,
both *below* the 0.10 the gate is told to enforce. Detection near the null
rate there is correct behaviour — a gate firing below its own declared bound
would be enforcing a tighter threshold than the one locked. The giveaway was
detection *falling* as n rose, which is impossible for a real effect: as noise
shrinks, the statistic converges onto a true value that sits under the
threshold. The curve is now reported in induced-SMD units so it cannot be
misread the same way.

The residual caveat is only the definition of the bound: an imbalance below
0.10 SMD passes by construction, so a PASS means "no imbalance beyond the
declared tolerance", never "the arms are identical".

### Packaging

The balance gate carries `@pytest.mark.slow` and is skipped unless `-m slow`.
`pytest.ini` had registered that marker from the start with the comment "opt
in with -m slow", but nothing implemented the skip — a pytest marker *selects*,
it does not *exclude*, so a slow test would have run on every ordinary
invocation. X5 is the first test to carry the marker, so the declared
convention became real here (`pytest_collection_modifyitems` in conftest).

Measured cost of the opt-in run: **77 seconds**, not the ~10 minutes first
estimated from the volume script against the real database. The estimate was
wrong; the packaging decision stands on its own terms.

Raw output for both gates plus both curves is committed to
`tests/evidence/phase6_x5_gate_evidence.txt`, which is the standing record
while the balance gate is opt-in. A test asserts that file exists and contains
both gates' figures, so it cannot quietly drift out of existence.
