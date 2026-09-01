# Revenue Recovery Intelligence Engine — Phase Acceptance & "Ready to Move On" Test Gates (Hardened)

*This revises the uploaded gate document. Every original gate is preserved. Additions are marked **[NEW]**. Nothing was removed except where a gate was restated more precisely — noted inline.*

---

## How to use this

A phase is NOT complete because the code runs, a script exits with 0, or the tests are green. Declare a phase complete only when every mandatory gate below passes, the evidence is saved in the required form, and no open defect violates a permanent invariant. "Perfectly implemented" means: all required behavior is demonstrated, the intended semantic property is proven, and the phase does not leave a known correctness hole for later.

**[NEW] Evidence must be a specific artifact, not an assertion.** For every gate, "passes" means: a saved test report, log, dataset hash, or config file exists at a known path, is referenced by the phase's sign-off record, and could be independently re-checked by someone who did not implement the phase. A gate marked passed with no such artifact is not passed — it is unverified.

**[NEW] Self-certification is not sufficient for the highest-severity gates.** The implementer may self-certify ordinary unit/component gates. Any gate touching money, compliance, causal claims, or authority boundaries (marked **⚠ independent check required** below) needs a second reviewer — or, at minimum, the same engineer re-reviewing after a real gap in time (not the same sitting) — before sign-off.

**[NEW] Statistical tolerances must be locked before they can be checked, not merely "declared."** Every tolerance, threshold, or balancing-variable list referenced below (calibration error, treatment-effect error, minimum sample size, significance level, candidate-count bound, latency bound) must exist in a committed configuration artifact with a timestamp/version, and that timestamp must **predate** the evaluation run that gets checked against it. A tolerance written or edited after results are seen invalidates the gate, even if the final number happens to pass. This is the single most important rule in this document — the rest of the tolerance-based gates below only work if this one is followed.

---

## Permanent gates for every phase

- No silent failures or swallowed exceptions in the new functionality.
- No duplicate authority: the rule engine remains the only component that can authorize execution.
- No new feature is considered complete without a test that can fail when the feature is broken.
- No synthetic result is described as a production fact.
- All generated datasets and model artifacts have explicit version/seed/config provenance where applicable.
- Existing behavior that is explicitly marked for preservation has regression coverage before it is changed.
- **[NEW] Cumulative regression.** Entering any phase re-confirms every exit gate of every prior phase still passes — not just the new phase's own gates. A phase that breaks an earlier invariant (e.g. a later schema change reopening a closed state-separation) fails immediately, regardless of how well its own new gates pass.
- **[NEW] Idempotency and concurrency safety.** Any operation introduced in this phase that writes state (event ingestion, decision, execution, experiment assignment, outcome capture) is tested against duplicate delivery and concurrent-write scenarios, and produces exactly one correct result, not a duplicate or corrupted one.
- **[NEW] Rollback is demonstrated, not assumed.** If a phase's design includes a disable/rollback mechanism (e.g. a feature flag), that mechanism is exercised by an actual test showing the system reverts to prior behavior — not merely described as available.
- **[NEW] Relevant "Do Not Proceed" conditions (see below) are checked at this phase's own exit, not deferred to final acceptance.** Each phase table below lists which conditions apply to it.
- **[NEW] Credential hygiene is re-checked every phase, not only at Phase 0.** Any new credential, API key, or secret introduced in this phase is confirmed absent from source control and version-history-exposed locations before sign-off.
- **[NEW] Minimum sample size is declared before any confidence interval is computed.** No phase reports a confidence interval, effect size, or "beats baseline" claim on a population below its predeclared minimum-N threshold; below that threshold the correct output is "insufficient data," not a number with a wide interval.

---

## Phase 0 — Bootstrap & Environment Repair

*Do-Not-Proceed conditions relevant here: none of the causal/execution ones apply yet; the credential-hygiene permanent gate is the operative check.*

| Gate | Exact result required |
|---|---|
| Hard result | A clean checkout can be installed and run without manual fixes. |
| Dependencies | `requirements.txt` installs successfully in a brand-new virtual environment; versions needed by shipped artifacts load without compatibility warnings. |
| Seed data | Seed generation succeeds and, with the same seed + fixed clock override, two independent runs produce byte-identical seed files. |
| Database | Database is created from scratch from DDL + seed data; expected tables exist; row counts and foreign keys are valid. |
| ML baseline | Existing shipped models load in the fresh environment; existing training corpus regenerates; no model retraining is done unless explicitly requested. |
| Pipeline | Batch pipeline runs end-to-end from clean state and writes non-empty, semantically sane recovery decisions/actions. |
| API | API starts from the clean environment and `/api/metrics` plus at least one read endpoint return valid responses. |
| Import hygiene | No `sys.path` hacks remain; every Python module imports successfully under the chosen package convention. |
| Security | No active credential is relied on by the test run; any previously exposed API key is rotated before further development. **[NEW]** Confirm whether the exposed key ever reached version-control history (not just the working tree) — if so, history exposure is documented even if full history rewriting is out of scope for this phase. |
| **[NEW] Idempotency** | Running the bootstrap sequence twice in a row from the same clean state produces the same end state, not duplicated rows or a crash on the second run. |
| Exit gate | All items above pass and evidence is captured. Only then proceed to Phase 1. |

---

## Phase 1 — Schema Foundation

*Do-Not-Proceed conditions relevant here: "critical phase has unresolved data lineage, leakage, reproducibility, or state-conflation defects."*

| Gate | Exact result required |
|---|---|
| Hard result | The new data model represents the business correctly before optimizer work begins. |
| Opportunity model | One economic opportunity can own multiple payment attempts; repeated retries remain linked to the same opportunity. |
| State separation | Decision, execution lifecycle, and business outcome are stored separately and cannot be confused by schema or queries. |
| Candidates | `recovery_candidates` can store every considered candidate, including `do_nothing`, treatment/baseline predictions, cost, rank and selection. |
| Merchant scope | Every new opportunity/payment/message resolves to the correct merchant. |
| Experiments | `experiment_assignment` can assign one opportunity to exactly one control/treatment group. **[NEW]** Verified under concurrent-write conditions — two simultaneous assignment attempts for the same opportunity cannot both succeed. |
| Network health | Bank/method/PSP health observations can be stored with time windows. |
| Migration | Existing baseline data is migrated or regenerated without losing any semantically important information. |
| Regression | Existing recovery pipeline still works against the new schema before optimizer functionality is enabled. |
| **[NEW] Uniqueness/race safety** | Schema-level constraints (not just application logic) prevent two decisions or two executions from being written for the same candidate, and prevent duplicate opportunity creation from a duplicate-delivered event. |
| Exit gate | Constructed multi-retry, multi-candidate fixtures pass referential-integrity and state-separation tests, **[NEW] and a concurrent-write fixture (simulated duplicate event, simulated race on assignment) passes without corruption.** |

---

## Phase 2 — Canonical Synthetic World + Joint Candidate-Outcome Dataset ⚠ independent check required (ground truth / tolerance gates)

*Do-Not-Proceed conditions relevant here: "relies on an untested assumption that directly affects... model validity"; "same data used to tune and prove result without genuinely held-out evaluation."*

| Gate | Exact result required |
|---|---|
| Hard result | One coherent synthetic world can generate the full experimental dataset needed by the intelligence layer. |
| Persistent entities | Customers, merchants and network entities persist across simulated time; repeated customers can have history. |
| Hidden state | Hidden state is sampled once per case and reused across every candidate for that case. |
| Joint candidates | Candidates can vary jointly across action × timing × payment method × channel, subject to eligibility rules, and always include `do_nothing`. |
| Potential outcomes | Every eligible candidate has a potential outcome generated from the same underlying case state; outcomes include recovery, recovered amount, partial recovery and time-to-recovery. |
| Network health | Health is time-varying and is part of the same canonical world, not a disconnected generator. |
| Fatigue | Repeated contact/intervention changes future responsiveness — **[TIGHTENED]** quantified by a named statistic (e.g. correlation or regression coefficient between contact count/recency and responsiveness) computed on a minimum-N sample, with the expected sign and a significance threshold predeclared in config, not asserted as "measurable, consistent" without a named test. |
| Profiles | At least two named calibration profiles generate valid data; one profile is not simply a copy with a different seed — **[NEW]** confirmed by showing at least one distributional statistic differs materially between profiles, not just the seed value. |
| Leakage | Case-level, customer-level and temporal leakage tests pass. |
| Reproducibility | Same seed + same profile + same generator version reproduces the dataset deterministically. |
| Ground truth | Empirical treatment effects agree with the simulator's analytic treatment effects within the **predeclared** tolerance (see the locked-tolerance rule above — the tolerance file's commit/timestamp must predate this evaluation run, checked explicitly). |
| Validator robustness | Deliberately corrupted datasets (e.g. hidden state re-sampled per candidate instead of once per case, a candidate with an out-of-range timing bucket, a duplicated case ID across the train/test split) trigger the correct validation failures — **[NEW] at least one corruption test per validator, not a general claim.** |
| **[NEW] Eval-set lock** | The dataset(s) reserved for final unseen evaluation (stress profile, later temporal window) are generated and hashed/committed before any model tuning in Phase 3 begins; that hash is checked at Phase 3 and Phase 9. |
| Exit gate | Dataset is registered with complete provenance and passes every structural, semantic, leakage and reproducibility gate, **[NEW] and the tolerance/eval-set lock artifacts predate their respective evaluation runs.** |

---

## Phase 3 — Joint Outcome / Treatment-Effect Model ⚠ independent check required

*Do-Not-Proceed conditions relevant here: "passes accuracy/AUC tests but its intended economic or treatment-effect property is unproven"; "test is weakened, threshold changed, or dataset size altered only to make a failing gate pass without first explaining the underlying reason."*

| Gate | Exact result required |
|---|---|
| Hard result | A trained model can estimate outcomes for arbitrary eligible candidates using only information available at decision time. |
| Jointness | The same model can score different action/timing/method/channel candidates without composing incompatible marginal models. |
| Baseline | `do_nothing` can be scored for the same case using the same feature contract. |
| Calibration | Predicted outcome probabilities meet the predeclared calibration-error threshold on held-out data. |
| Treatment effect | Estimated treatment effects match the simulator's known ground-truth effects in direction across the tested candidate space and within the predeclared magnitude tolerance. |
| Generalization | Model passes the unseen stress-profile calibration gate and the later temporal-window gate — **[NEW] using the Phase-2 eval-set lock artifact, confirmed unused during any tuning step.** |
| Multi-seed | At least three seeds show stable qualitative conclusions; this is supporting evidence, not the primary generalization proof. |
| Train/serve parity | Exactly the same feature computation/inference path is used offline and live; same case → same score through both paths. |
| Failure behavior | Out-of-distribution/malformed feature cases fail safely and do not silently produce invalid scores — **[TIGHTENED]** specifically: the failure path returns a clearly-flagged null/error result that downstream code is proven not to treat as a valid score (test: inject a malformed case and confirm it never reaches `recovery_candidates` as a normal-looking row). |
| **[NEW] Threshold-change audit trail** | If any calibration/tolerance threshold was changed after this phase's first evaluation attempt, the change is logged with a dated justification unrelated to the specific failing result, reviewed independently — not silently edited to pass. |
| Exit gate | One model artifact passes all hard statistical gates and is loadable through the single inference interface, **[NEW] with the tolerance-lock and eval-set-lock artifacts verified to predate the passing run.** |

---

## Phase 4 — Optimizer ⚠ independent check required (authority gates)

*Do-Not-Proceed conditions relevant here: "optimizer can execute anything without a rule-engine approval"; "`method_change` has any reachable autonomous executor path"; "feature is claimed as implemented when it is only simulated, stubbed, hard-coded, or displayed."*

| Gate | Exact result required |
|---|---|
| Hard result | Given an opportunity, the optimizer produces a bounded, auditable ranking of eligible candidates by Expected Incremental Value. |
| Candidate pruning | Stage A structural eligibility and Stage B relevance filtering reduce the candidate set before model inference; the bound is measured **and a specific numeric ceiling is declared in config and enforced by an assertion**, not just "measured." |
| Joint scoring | Every surviving candidate is evaluated against the same baseline; no separate marginal-model composition is used. |
| EIV calculation | EIV = predicted expected recovered amount under candidate − predicted expected recovered amount under `do_nothing` − intervention cost. |
| Do nothing | `do_nothing` is always present and genuinely competitive; it is not a hidden fallback — **[NEW] demonstrated by at least one constructed scenario where `do_nothing` legitimately wins the ranking.** |
| Method change | `method_change` may be scored and displayed but has no executor path. |
| Auditability | Every considered candidate and its score/rank/cost/baseline comparison is persisted. |
| Authority | Optimizer cannot call execution-capable functions or mutate execution/payment state — **[NEW] verified by a static import/call-graph check, re-run in Phase 9, not just asserted once here.** |
| Correctness test | Constructed scenarios where candidate A has higher true incremental value than B cause the optimizer to rank A above B — **[TIGHTENED]** required as a **suite** covering at least one scenario per major root-cause class and per eligibility class (method-eligible vs. not), not a single scenario. |
| **[NEW] Latency bound** | End-to-end candidate generation + scoring for one opportunity completes within a predeclared latency budget suitable for live/demo use, measured under the enforced candidate-count ceiling above. |
| Exit gate | Ranking correctness (full suite), candidate bounds, `do_nothing` competitiveness, method-change restriction and authority tests all pass, **[NEW] including the static call-graph check.** |

---

## Phase 5 — Rule Engine & Bounded Executor ⚠ independent check required

*Do-Not-Proceed conditions relevant here: same as Phase 4, plus "the same data is used to tune and prove the final business result without a genuinely held-out evaluation" (applies once decisions start feeding back).*

| Gate | Exact result required |
|---|---|
| Hard result | The optimizer is integrated without weakening compliance or execution boundaries. |
| Backward compatibility | With optimizer disabled, decisions match the pre-optimizer behavior. |
| **[NEW] Disable path proven** | The optimizer can be turned off via configuration at runtime and decisions immediately revert to pre-optimizer behavior — exercised by an actual test that flips the flag mid-run, not just documented as possible. |
| Compliance | Every optimizer proposal is independently rechecked by the rule engine. |
| Fallback | If the top candidate is non-compliant, the next compliant candidate is considered correctly. |
| Execution separation | Decision outcome, execution lifecycle and business outcome remain distinct. |
| Scheduling | Scheduled actions have a separate lifecycle; due actions fire, future actions do not, and invalidated/superseded actions are not fired. |
| Executable vocabulary | Only the approved executable action set can reach the executor. |
| Method change | There is no reachable executor path for autonomous payment-method change — **[NEW] re-verified by the same static check from Phase 4, run again here against the now-integrated code.** |
| Shared pipeline | All entry points use the same classify → optimize → authorize → execute → message flow — **[TIGHTENED]** verified structurally (a single shared function is called by all three entry points), not only by matching output, so future changes to one entry point cannot silently diverge from the others. |
| **[NEW] Idempotent dispatch** | Firing the same scheduled/approved execution twice (simulated duplicate dispatcher run) produces exactly one execution, not a duplicate action against the customer. |
| Exit gate | Optimizer-driven decisions work end-to-end, all authority and scheduling regression tests pass, **[NEW] the disable path is proven, and dispatch idempotency is proven.** |

---

## Phase 6 — Live Experiment Assignment & Outcome Observation ⚠ independent check required

*Do-Not-Proceed conditions relevant here: "synthetic ground truth is presented as real-world causal evidence" (not yet applicable — flag for Phase 7); "critical phase has unresolved data lineage... defects."*

| Gate | Exact result required |
|---|---|
| Hard result | The system can create real control/treatment assignments and capture outcomes through one path. |
| Assignment | Every eligible opportunity is assigned once, reproducibly according to the configured experimental policy — **[NEW]** proven safe under concurrent assignment attempts (see Phase 1's uniqueness gate, re-verified here at the application layer). |
| Control isolation | Control opportunities cannot receive automated intervention after assignment. |
| Balance | Treatment and control groups are statistically comparable at assignment for the declared balancing variables — **[TIGHTENED]** the balancing-variable list itself must be declared and locked before assignment begins, per the general tolerance-locking rule, so variables cannot be chosen post hoc because they happen to look balanced. |
| Outcome capture | There is exactly one authoritative outcome-ingestion path; manual demo confirmation is only an input source to that path. |
| No retroactive contamination | Outcome observation never changes historical treatment assignment. |
| Lineage | Every outcome can be traced to its opportunity and relevant execution history. |
| **[NEW] Duplicate-outcome safety** | Submitting the same outcome-observation event twice does not double-count recovery or corrupt the opportunity's outcome fields. |
| Exit gate | A complete controlled experiment can be simulated/exercised end-to-end with valid assignment, suppression and observation evidence, **[NEW] including a concurrency/duplicate-delivery fixture.** |

---

## Phase 7 — Live Incremental Attribution & Reporting ⚠ independent check required

*Do-Not-Proceed conditions relevant here: "synthetic ground truth is presented as real-world causal evidence"; "same data used to tune and prove the final business result without a genuinely held-out evaluation."*

| Gate | Exact result required |
|---|---|
| Hard result | The system produces a defensible incremental-₹ estimate from treatment/control outcomes. |
| Calculation | Incremental recovery is derived from treatment vs control using a documented estimator; no raw recovery-rate number is mislabeled as incremental. |
| Confidence | A confidence interval is reported alongside the estimate. |
| Traceability | Every reported figure can be reproduced from inspectable live queries. |
| Segmentation | Merchant/time-window/root-cause filtering does not silently change the estimator assumptions — **[NEW]** and any segment below the predeclared minimum-N threshold reports "insufficient data" rather than a wide-interval estimate. |
| Prediction vs observation | Predicted EIV and observed incremental ₹ are shown as separate quantities; divergence is reported, not hidden. |
| Synthetic honesty | Synthetic results are explicitly labeled synthetic and never presented as production proof. |
| **[NEW] Estimator misuse guard** | A test confirms the system refuses (or clearly flags) a report request for a segment/time-window combination that hasn't cleared the minimum-N threshold, rather than silently returning a number. |
| Exit gate | At least one valid experimental segment produces an incremental-₹ estimate with CI and auditable calculation, **[NEW] and at least one deliberately-underpowered segment is shown correctly refusing to report a false-confidence number.** |

---

## Phase 8 — Control Tower & Metrics API

*Do-Not-Proceed conditions relevant here: "a feature is claimed as implemented when it is only simulated, stubbed, hard-coded, or displayed."*

| Gate | Exact result required |
|---|---|
| Hard result | The dashboard tells the truth about current revenue risk and optimizer reasoning. |
| Opportunity queue | Opportunities are ranked by the actual optimizer value metric. |
| Headline economics | ₹ at risk, expected recoverable/EIV, recovered ₹ and incremental ₹ are calculated from live data. |
| Reasoning | A user can inspect the complete candidate set, baseline comparison, chosen candidate, compliance decision and execution outcome. |
| Method change | A method-change recommendation is visibly advisory-only and never appears as executed. |
| Audit trail | Decision, execution and business outcome are distinguishable. |
| Live traceability | Every headline number maps to a live query/API field. |
| Metric correctness | The old incorrectly named amount-at-risk calculation is removed/replaced. |
| **[NEW] No hard-coded fallback values** | Every field renders from a live query even in an empty/edge-case state (zero opportunities, zero experiments run) rather than falling back to a placeholder number that could be mistaken for real data. |
| Exit gate | All required views work from live state, with no hard-coded demo numbers, **[NEW] including edge-case/empty-state verification.** |

---

## Phase 9 — Testing & Evaluation Hardening ⚠ independent check required

*Do-Not-Proceed conditions relevant here: all of them — this phase is the system-wide sweep.*

| Gate | Exact result required |
|---|---|
| Hard result | The complete system is validated as one system, not merely as isolated modules. |
| Unit | All core decision, candidate, scheduling, parsing and validation branches have tests. |
| E2E | All entry points pass representative event/root-cause/timing scenarios. |
| Adversarial | Malformed inputs, invalid confidence, contradictory replies, bad amounts and other defined adversarial cases fail closed. |
| Authority | Static + runtime checks prove optimizer/LLM cannot bypass rule-engine authority — **[NEW] re-run against the fully integrated final codebase, not only the modules where each check originated.** |
| Leakage | Case, customer and temporal leakage tests pass for all relevant datasets. |
| Reproducibility | Same seed/config reproduces datasets and experimental results. |
| Generalization | Cross-profile and temporal holdouts pass. |
| Business value | Optimized policy beats the declared baseline on incremental recovered ₹ on unseen evaluation data — **[TIGHTENED]** the improvement's confidence interval must exclude zero; a point-estimate win with a CI spanning zero does not pass. |
| Statistical reporting | Confidence intervals, sample sizes and evaluation populations are always reported with business results. |
| **[NEW] Cumulative regression sweep** | Every phase's exit gate (0–8) is re-verified once against the final integrated system, not assumed still valid from when each phase was originally signed off. |
| **[NEW] Tolerance/eval-set audit** | Every locked tolerance and eval-set-hash artifact used anywhere in the project is confirmed to predate its corresponding evaluation run; any exception is documented and independently reviewed. |
| Exit gate | All three testing tiers pass with no unresolved correctness-critical defect, **[NEW] the cumulative regression sweep is clean, and the tolerance/eval-set audit has no unexplained exceptions.** |

---

## Phase 10 — Final Demonstration Assembly

| Gate | Exact result required |
|---|---|
| Hard result | The full product can be demonstrated from a fresh environment without a pre-recorded or hard-coded story. |
| Live reasoning | The demo visibly shows revenue at risk → candidate alternatives → expected incremental value → selected action → compliance gate → execution. |
| Do nothing | The baseline is visibly considered and can win when appropriate. |
| Method change | A high-value method-change recommendation can be shown without autonomous execution. |
| Failure | At least one deliberately non-compliant or unsafe action is visibly blocked. |
| Outcome | The system observes a recovery outcome and updates the opportunity correctly. |
| Incrementality | The demo distinguishes recovered ₹ from incremental ₹ and shows the confidence interval when the data supports it. |
| Traceability | Displayed numbers come from live application state, not static narration. |
| Honesty | Synthetic assumptions and simulated results are clearly labeled. |
| Exit gate | A fresh end-to-end dry run passes with no manual patching and survives at least one unscripted adversarial input. |

---

## Final "Do Not Proceed" Conditions

*(unchanged from the original list — now cross-referenced above to the specific phase(s) where each must actually be checked, not deferred to this final list alone)*

- A phase relies on an untested assumption that directly affects money, compliance, causality, or model validity.
- A model passes accuracy/AUC tests but its intended economic or treatment-effect property is unproven.
- A feature is claimed as implemented when it is only simulated, stubbed, hard-coded, or displayed.
- The optimizer can execute anything without a rule-engine approval.
- `method_change` has any reachable autonomous executor path.
- Synthetic ground truth is presented as real-world causal evidence.
- The same data is used to tune and prove the final business result without a genuinely held-out evaluation.
- A test is weakened, threshold changed, or dataset size altered only to make a failing gate pass without first explaining the underlying reason.
- A critical phase has unresolved data lineage, leakage, reproducibility, or state-conflation defects.
- **[NEW]** A statistical tolerance, balancing-variable list, or eval-set was locked *after* seeing results it was meant to judge.
- **[NEW]** A state-mutating operation (assignment, decision, execution, outcome capture) has no demonstrated protection against duplicate delivery or concurrent writes.

---

## Final Project-Level Acceptance

The project is ready for the final demo only when Phases 0–10 have passed their mandatory gates, the permanent invariants remain intact, the optimized policy beats the declared baseline on incremental recovered ₹ **with a confidence interval excluding zero** on unseen evaluation data, and the product can show the full economic loop from revenue at risk to measured incremental recovery.

The most important final proof is not model accuracy. It is this chain: identify a real/simulated opportunity → explain why it is at risk → evaluate multiple permitted alternatives → choose the candidate with the highest defensible expected incremental value → authorize it safely → observe the outcome → measure incremental ₹ against the baseline → show the evidence live.

This document is the phase sign-off checklist. If a phase misses any hard gate, the correct status is **"NOT COMPLETE"** — do not silently carry the gap forward. **[NEW]** If a phase passes only because a tolerance, threshold, or dataset was adjusted after seeing a failing result, the correct status is also **"NOT COMPLETE"** — regardless of what the adjusted number now shows.
