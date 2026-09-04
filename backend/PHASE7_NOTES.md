# Phase 7 — Live Incremental Attribution & Reporting

**Scope: AGGREGATE ONLY. Phase 7's acceptance gate is met at aggregate scope,
not fully.** See §4 for exactly what is cut and why. Nothing in this document
claims otherwise.

**Everything here is SYNTHETIC.** The outcomes aggregated below were drawn
from the Data Factory's potential-outcome generator, not observed from any
real payment system — none exists for this project. `outcome_source` records
that per row, and `incremental_attribution` refuses to report at all if a
population mixes synthetic draws with confirmed outcomes.

---

## 1. What Phase 6 was missing, and how it was closed

`Phase_Acceptance_Test_Gates.md` sets a higher bar than EXECUTION_PLAN.md's
Definition of Done, and against that document Phase 6 was **not complete**: it
had assignment and suppression evidence but **no observation evidence at all**,
plus three `[NEW]` rows with no home.

| Gate row | Status now | Where |
|---|---|---|
| Assignment, safe under **concurrent** attempts | 8 racing workers, one row, all agree on the arm, exactly one told "assigned" | `test_phase6_exit_gate.py` |
| **Duplicate-outcome safety** | same event twice → second returns `already_resolved`, no field moves; conflicting second outcome cannot mutate the first; concurrent form has exactly one winner | `test_phase6_exit_gate.py` |
| No retroactive contamination | observing an outcome leaves the assignment and its timestamp untouched | `test_phase6_exit_gate.py` |
| Lineage | every outcome walks back to opportunity → assignment → decision → execution in one join | `test_phase6_exit_gate.py` |
| Exit gate, end-to-end | assignment + suppression + observation on one population, raw numbers printed | `test_phase6_exit_gate.py` |

---

## 2. Three defects found by turning the optimizer on

The ruling to enable the optimizer at `trigger_event` exposed problems that
were invisible while it was off — **the ranked pathway had never executed
against a live database in this project's history.**

### 2a. `candidate_id` was never written back — the link was structurally dead

`optimize._insert_row()` does not read `cursor.lastrowid`, so the in-memory
ranked dicts carried no id. Consequence: `recovery_decisions.candidate_id`
and `recovery_candidates.selected` had **never been populated by anything.**

That also means Phase 6's counterfactual gate probe on
`recovery_candidates.selected = 1` was **vacuous** — it read 0 for the control
arm because nothing in the system could ever set it, not because suppression
worked. It now reads 0 / 1452.

Fixed in `pipeline._attach_candidate_ids()`, not in `optimize.py`: that module
is frozen-adjacent and its one dated exception is explicitly recorded as
closed. Same precedent as `derive_pruned_candidates()`, which reconstructs
rather than modifies the frozen generator it depends on. Matched on `rank`,
which is unique among one run's scored rows, rather than on the attribute
tuple, which is unique only by convention.

### 2b. `trigger_event` never recorded a payment method

Its payment INSERT hardcoded `method: None`. A candidate carrying a concrete
method is treated as a payment-method **change** when the opportunity's own
method is unknown, and method changes are structurally non-executable — so the
rule engine walked past retry, payment_link and reminder alike and fell
through to `escalate`.

**Measured before the fix: 400 of 400 treatment opportunities selected
`escalate`**, an action the optimizer scored at EIV −5088, while a retry it
scored at +4048 sat unexecutable at rank 1. Mean selected EIV was **−5088**.

Fixed by adding an optional `method` parameter (default `None`, so every
existing caller behaves exactly as before) and having the volume generator
supply one from the same four-value vocabulary the seed generator uses.

### 2c. The contact-hours window made the result depend on the clock

`decide_action()` refuses customer contact outside 09:00–20:00, judged against
the opportunity's own `created_at`. Generation runs at wall-clock time, so a
run started at 07:00 had **every** retry, reminder and payment_link blocked as
out-of-window, leaving `escalate` — exempt as internal routing — the only
executable candidate.

This is the compliance rule working correctly, not a defect. But it meant the
treatment arm's policy, and therefore the headline number, depended on what
hour the operator happened to run the script. `generate_experiment_volume` now
pins creation to midday (`DEFAULT_CREATED_AT_HOUR = 12`), which suppresses no
compliance check — every rule still runs — and places the population inside
business hours. Disclosed in the code, the CLI output, and the evidence file.

**After all three fixes**, mean selected EIV is **+7303** and the action mix is
`payment_link 456 / reminder 707 / retry 225 / escalate 64`, with 290 treatment
opportunities selecting nothing executable.

---

## 3. The result

Population: 3500 opportunities created through `trigger_event` with the
optimizer enabled; outcomes realized for all 3500 through `observe_outcome()`.

    balance gate          PASS   max |SMD| = 0.0475 (bound 0.10)
    counterfactual gate   PASS   control 0 on all four probes
    outcome_source        {'synthetic_potential_outcome': 3500}

**Both Phase 6 gates still hold after the optimizer flip.** The counterfactual
gate's treatment-side numbers are now non-zero where two of them were
previously zero — `selected_candidates` 0 → 1452 and `outbound_messages`
0 → 632. That is the fix in 2a landing and real messages being delivered, not
a regression.

### The incremental figure

    recovery rate       treatment 0.8772 (1528/1742)
                        control   0.8572 (1507/1758)

    INCREMENTAL RECOVERY RATE   +0.0199   95% CI [-0.0025, +0.0424]
    INCREMENTAL Rs / OPPORTUNITY  +1,549.72  95% CI [-3,546.99, +6,646.43]
    INCREMENTAL Rs (total)      +2,699,617  95% CI [-6,178,855, +11,578,090]
                                over 1742 resolved treatment opportunities

**The confidence interval includes zero.** The point estimate is positive and
the effect is **not statistically distinguishable from zero at this sample
size**. That is the honest reading and it is stated here rather than left for
someone to notice: a positive point estimate reported without its interval
would be a claim this data does not support.

### Predicted vs observed — a diagnostic, not an agreement check

    predicted EIV / opportunity   +5,430.16   (n=1452 selected candidates)
    observed      / opportunity   +1,549.72
    delta                         -3,880.44
    predicted inside observed 95% CI: True

The optimizer expects roughly 3.5× the per-opportunity value the experiment
measured. The two are different quantities from different sources — predicted
EIV is the optimizer's expectation for the candidate the rule engine selected,
observed is the measured arm difference — and they are reported side by side
so the divergence is visible. Nothing reconciles them. The predicted value
does fall inside the observed interval, but that interval is wide enough to
contain almost any plausible value, so it is weak agreement at best and should
not be read as validation.

### The estimator, named

Difference in proportions with a **Wald** 95% interval for the rate;
difference in means with a **Welch** (unpooled-variance) 95% interval for the
rupee figure. Chosen because it is the simplest estimator that is *correct*
for a completely randomized two-arm experiment: randomization is what makes
the naive difference unbiased, and any covariate adjustment would buy
precision at the cost of an assumption this design does not need.

Unpooled variances on the rupee figure specifically because an intervention
that works shifts the treated arm's spread as well as its centre; a pooled
estimate would assume away the thing being measured.

### Minimum-N — a permanent gate, not an option

`MIN_N_PER_ARM = 30`. Below it the module returns "insufficient data" and
**no number at all** — not a wide interval, not a caveated figure. A CI on a
handful of rows is not a weak result, it is a misleading one: wide enough to
contain anything while still looking like a measurement.

Deliberately **not** tied to `phase6_config.MIN_ASSIGNED_N` (3500). Those
floors answer different questions — 3500 is what the *balance gate* needs to
detect imbalance, 30 is what the *estimator* needs for its normal
approximation to hold — and tying them would make one move silently with the
other.

---

## 4. What is CUT, explicitly and dated

**Ruled 2026-09-04. Phase 7's acceptance gate is met at AGGREGATE SCOPE ONLY.
It is not fully met. This is disclosed, not hidden.**

Two gate rows are **not** satisfied:

| Gate row | Status |
|---|---|
| **Segmentation** — "Merchant/time-window/root-cause filtering does not silently change the estimator assumptions" | **NOT BUILT.** `incremental_attribution` computes one figure over the whole assigned population and takes no filter arguments. |
| **[NEW] Estimator misuse guard** — "a test confirms the system refuses a report request for a *segment/time-window combination* that hasn't cleared the minimum-N threshold" | **PARTIALLY MET.** The refusal mechanism exists and is tested, but at aggregate scope only (`test_an_underpowered_population_reports_insufficient_data`, `test_one_arm_below_the_floor_is_still_a_refusal`). There is no per-segment refusal because there are no segments. |

Consequently the Phase 7 **exit gate** — "at least one valid experimental
segment produces an incremental-Rs estimate with CI and auditable calculation,
and at least one deliberately-underpowered segment is shown correctly refusing
to report a false-confidence number" — is met on its first half and, on its
second half, only in the aggregate form.

Cut for time. Closing it means adding segment filters to
`incremental_attribution`, threading the same minimum-N refusal through them,
and adding the underpowered-segment test. The refusal logic is already
factored so a segment filter would reuse it unchanged.

### One test the clock pinning broke, and why that was the right outcome

`test_the_gate_fails_when_control_shows_an_executed_action` — the
counterfactual gate's own negative control — started reporting PASS where it
demands FAIL.

Cause: every probe is bounded at `assigned_at`, because activity strictly
*before* assignment is legitimately not a violation. The fixture injected its
decision with `insert_decision`'s default timestamp, i.e. the real wall clock,
while the volume generator now pins assignment to midday. The injected
violation therefore landed *before* the assignment it was meant to violate,
and the gate correctly ignored it.

The gate was right and the fixture was wrong. Fixed by stamping the injected
decision at `assigned_at + 1`, which is robust regardless of pinning. Recorded
because it is a negative control doing its job about itself: a fixture that
had quietly stopped testing anything announced that fact by failing.

---

## 5. Test amendments made in this phase, all dated 2026-09-04

Three tests encoded the optimizer's *old* flag state rather than a property,
and enabling it at `trigger_event` broke them. None was deleted; each was
narrowed to what still holds and, where possible, tightened.

| Test | Was | Now |
|---|---|---|
| `test_optimizer_defaults_off_at_every_entry_point` | `not any(...)` — the whole table off | every entry point **not** a recorded exception follows `OPTIMIZER_ENABLED_DEFAULT`; `trigger_event` is the one named exception |
| `test_synchronous_entry_points_stay_off_while_the_latency_budget_is_unmet` | both synchronous entry points off | `customer_reply` still off; `trigger_event` on by ruling; **plus** a new assertion that the 250 ms budget is unchanged — the enablement is justified by *accepting* the miss, not by redefining it away |
| `test_the_switch_defaults_on_so_the_pathway_exists` | `not any(...)` | the exact ruled table, which also fails if `customer_reply` or `dispatch` is ever enabled silently |
| `test_trigger_event_parity_over_the_fixed_spec_list` | compared live `trigger_event` against the frozen pre-W7 sequence | holds the optimizer **off** for the duration — the legacy body cannot take the ranked path, so a divergence would report the policy change rather than a pipeline defect, and the W7 refactor guarantee would quietly stop being checked |

The Phase 4 latency miss is **unchanged and still open**.
`test_end_to_end_latency_against_the_declared_budget` remains one of the
recorded known failures. Nothing about the budget, the measurement or the
disclosure moved.
