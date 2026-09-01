# Revenue Recovery Intelligence Engine — Final Execution Blueprint

*A standalone, implementation-ready plan from the current repository to the product defined by the Revenue Recovery Intelligence Engine SoT. Self-contained: no external document is required to follow it.*

---

## 0. Purpose and Product Thesis

Razorpay merchants lose revenue continuously across four related but distinct failure modes: failed payments, abandoned checkouts, lapsed subscriptions, and overdue receivables. Point solutions for each already exist, inside Razorpay and across competitors (Stripe, Adyen, Checkout.com, Juspay, Yuno, Gr4vy, Primer) — smart retries, dunning sequences, routing, basic reminder bots. That category is commoditized.

The system this plan builds answers one question continuously, per merchant, per opportunity: **how much revenue is at risk, why, how much of it can actually be recovered, which intervention — for which customer, under which conditions, at what time — maximizes expected incremental recovered ₹, how do we execute that safely, and can we prove the money it actually caused?**

The engine is organized around one continuous loop: **Detect → Diagnose → Quantify → Generate Candidates → Predict Outcomes → Estimate Incremental Value → Optimize → Authorize (compliance gate) → Execute (bounded) → Observe → Attribute (prove incrementality) → Learn.** Everything in this plan exists to make that loop real, correct, and demonstrable — not to add features for their own sake.

A supporting offline subsystem, the **Data Factory**, exists purely to generate calibrated, reproducible, counterfactually-valid synthetic data so the intelligence layer can be trained and evaluated. It is infrastructure. It is never the product, never touches production data, and never executes anything.

---

## 1. Current Repository — Verified State

This reflects the repository as it actually exists today, confirmed by direct inspection, not assumption.

### 1.1 What exists
A FastAPI backend (`backend/api`), a rule-based recovery engine (`backend/engine`), an LLM layer for customer-reply parsing and message generation (`backend/llm`), and an ML layer (`backend/ml`) with a synthetic-data generator, a training script, and two trained model artifacts (`lr_model.joblib`, `xgb_model.joblib`). A SQLite schema (`backend/db/db.py`) defines four tables: `customers`, `payments`, `recovery_actions`, `messages`. No frontend exists anywhere in the repository. No automated tests exist anywhere in the repository.

### 1.2 It does not currently run from a clean checkout
`backend/requirements.txt` is empty despite the code depending on FastAPI, Pydantic, python-dotenv, scikit-learn, XGBoost, joblib, pandas, numpy, and (optionally) `google-generativeai`. `backend/db/recovery.db` is an empty file with no schema applied. `backend/data/` (expected seed JSON) is empty. `backend/ml/data/` (expected training corpus) is empty — only the two already-trained model artifacts survived. There is also a real inconsistency in how modules resolve imports: `engine/*.py` treats `backend/` itself as the import root via a `sys.path` hack, while `api/*.py` treats the directory containing `backend/` as the root via `backend.<module>`-style imports. Both can coincidentally work together only under specific launch conditions; this is fragile and must be unified.

### 1.3 The recovery pipeline — what it actually does
`classify()` is a pure rule lookup: for `payment_failed` events it returns the already-labeled `error_reason` as `root_cause`; for other event types it returns `None`. `decide_action()` is the rule engine and sole compliance authority — it enforces a maximum retry count, a minimum cooldown between contact attempts, an auto-stop-and-escalate rule after a bounded no-response period, a restricted contact-hours window, and an LLM-confidence gate, in a fixed order, and is the only function permitted to mark a decision `allowed: True`. Its choice of *which* action to take, however, is not learned or ranked — it is a hardcoded lookup by `event_type` (`retry` for payment failures, `reminder` otherwise, `escalate` if an invoice is more than a fixed number of days overdue). The ML model is consulted only to **score the single action already chosen this way** — it is never asked to compare alternatives. This advisory-only, single-candidate-scoring discipline is exactly the authority boundary the product needs; the missing piece is that nothing today generates or ranks *alternatives* for it to be advisory about. `execute_action()` writes the audit log and updates payment status; it is immediate-only, with no concept of scheduling. `core_loop.py`, `trigger_event.py`, and `handle_customer_reply.py` all invoke the exact same four-call sequence (`classify → decide_action → execute_action → deliver_recovery_message`) — this single-shared-pipeline discipline is correct and worth preserving exactly. `mark_payment_recovered.py` is the only source of recovery ground truth today: a manual, demo-oriented "mark as recovered" utility, with no control-group semantics of any kind. This means the system today has zero ability to distinguish revenue that recovered because of an action from revenue that would have recovered anyway.

### 1.4 The LLM layer — already correctly bounded
`parse_intent.py` extracts structured customer intent from replies via Gemini; `generate_message.py` generates customer-facing message text. Both are already exactly the shape the target product needs: `root_cause` is deliberately never included in the intent-extraction prompt (preventing anchoring bias when independently verifying what a customer claims), both fail closed to deterministic fallback behavior on any error, and neither function has any path to select, trigger, or override a recovery action. This boundary is correct as built and should not be re-architected — only extended with new prompt content as new action types are introduced.

### 1.5 The ML layer — a real, correctly-designed piece with real gaps
`simulate_training_data.py` generates one training corpus: for each synthetic case, a hidden-state vector (`liquidity_state`, `issuer_availability`, `payment_method_health`, `customer_responsiveness`, `bank_condition_temp`, `recovery_willingness`) is sampled **once and reused across every candidate action row for that case** — this is exactly the counterfactual-consistency pattern the target system needs, and it is already correctly implemented. `train_risk_model.py` splits train/test using `GroupShuffleSplit` grouped on `case_id`, with a runtime assertion enforcing zero case-ID overlap between splits — a real, enforced leakage guard. `verify_sensitivity.py` performs basic calibration/monotonicity sanity checks. What does **not** exist anywhere in this layer: any timing dimension, any payment-method or channel dimension beyond the single already-chosen action, any merchant concept, any real (surfaced, not just internally-hidden) network-health signal, any cost concept, any potential-outcomes structure (each case-candidate row has exactly one stochastic draw, not a comparison against a "no action" counterfactual), any partial-recovery or time-to-recovery output, and no dataset-versioning manifest beyond a hardcoded random seed.

### 1.6 The schema — functional but conceptually flattened
`payments` already carries a single `event_type` discriminator (`checkout_abandoned` / `payment_failed` / `invoice_overdue`) feeding one shared pipeline — a real, working step toward a unified Revenue-at-Risk representation, worth preserving as the seed of that idea. But the schema has no concept of a **merchant**, no concept of an **opportunity** distinct from a raw payment row (so a payment that gets retried three times looks like the same single row updated in place, with no clean way to reason about lineage, time-to-recovery across attempts, or repeated-event history), and `recovery_actions` conflates three genuinely different concepts into one table: what the system considered as options, what it decided was compliant, and whether that decision has actually been executed yet.

### 1.7 The API layer
`GET /api/cases`, `GET /api/cases/{id}`, `GET /api/metrics`, `POST /api/events/trigger`, `POST /api/cases/{id}/reply`, `POST /api/cases/{id}/simulate-recovery`, `GET /api/audit-feed`. One confirmed defect: `get_metrics()`'s `amount_at_risk_total` sums **all** payments regardless of recovery status, including already-recovered ones — it reports total historical volume, not money currently at risk. The adjacent field `current_amount_exposed` (`SUM(amount) WHERE recovery_status != 'recovered'`) is the one that actually matches "₹ at risk" and should be the basis for the corrected metric. No endpoint or field anywhere computes Expected Incremental Value, incremental ₹, or anything control-group-aware.

### 1.8 What must be preserved unchanged in spirit
The single-shared-pipeline discipline across all entry points; the rule-engine-final-authority pattern and its compliance constants; the LLM's language-only boundary and its bias-prevention design; the hidden-state-sampled-once-per-case and grouped-split-with-assertion patterns from the ML layer; the append-only audit-logging discipline (every action, blocked or executed, is logged with a reason).

---

## 2. Target Architecture

### 2.1 Seven kinds of truth
The single most important discipline this plan enforces is never collapsing these into each other:

| Kind | What it is | Example |
|---|---|---|
| Runtime truth | An actual event that occurred | A payment attempt failed with `gateway_timeout` |
| Synthetic ground truth | A generative fact the Data Factory defines by construction | The simulator's true treatment effect for a synthetic case |
| Model prediction | An estimate from a trained model | Predicted probability and expected recovered amount for a candidate |
| Counterfactual / potential outcome | "What would happen under a different candidate for this same case" | The estimated outcome under `do_nothing` for the same opportunity |
| Experiment assignment | Which real opportunities are held out from automated intervention | `opportunity_id → control` |
| Observed outcome | What actually happened to a real opportunity | Recovered ₹40,000 at t+2 days |
| Derived business metric | A computed-on-demand aggregate | Incremental ₹ recovered this week, with a confidence interval |

### 2.2 The loop, precisely
1. **Detect** — a Revenue-at-Risk **Opportunity** is created or updated from an incoming event (payment failure, checkout abandonment, overdue receivable), deduplicated against any existing open opportunity for the same underlying situation rather than always creating a new one.
2. **Diagnose** — root-cause classification, enriched with network-health context where available.
3. **Quantify** — ₹ at risk is attached to the opportunity.
4. **Generate candidates** — a pruned set of (action, timing, payment method, channel) tuples, always including an explicit "do nothing" option, produced by shared eligibility logic used identically offline and live.
5. **Predict outcomes** — one joint model estimates the probability and expected recovered amount for every candidate in the set, including the "do nothing" baseline.
6. **Estimate incremental value** — the difference between a candidate's predicted outcome and the "do nothing" baseline, net of the candidate's cost, is the Expected Incremental Value (EIV) — this is a derived arithmetic quantity, never a direct model output.
7. **Optimize** — candidates are ranked by EIV.
8. **Authorize** — the rule engine validates the top-ranked proposal (or the next compliant one, if the top choice is blocked) against hard compliance rules. It is the sole authority permitted to approve execution.
9. **Execute** — only rule-engine-approved, narrowly-scoped action types are dispatched, immediately or on a schedule.
10. **Observe** — real outcomes are captured on the opportunity.
11. **Attribute** — a live control/treatment comparison measures how much of the observed recovery was actually caused by intervention, with a confidence interval.
12. **Learn** — outcomes feed versioned retraining, never a silent live model mutation.

### 2.3 Authority matrix — one owner per responsibility, permanently
| Responsibility | Sole owner |
|---|---|
| Structural candidate eligibility (what combinations are even meaningful) | Shared candidate-generation logic, used by both the Data Factory and the live optimizer |
| Compliance eligibility (cooldown, retry caps, contact hours, confidence gating) | The rule engine, exclusively |
| How customer/merchant context shifts expected outcomes | Learned by the joint outcome model from its input features — never a separate hand-tuned bonus layer |
| Ranking by expected incremental value | The optimizer, exclusively, advisory-only |
| When an approved action actually fires | The executor/dispatcher, exclusively — decides timing of an already-approved action, never whether to act |
| Physical execution | The executor, exclusively, against a closed, narrow action vocabulary |
| Language generation and intent extraction | The LLM, exclusively — never selects, triggers, or overrides an action |
| Claiming a number is "incremental ₹" | Only the live attribution module, computed from real randomized/held-out assignment — never inferred from non-randomized data, and never asserted from a synthetic-only result without labeling it as synthetic |

No two components on this list are ever allowed to perform the same responsibility, even redundantly "for safety" — redundant authority is how authority boundaries erode silently.

---

## 3. Data Model

| Entity | Purpose | Key fields |
|---|---|---|
| `merchants` | Multi-tenancy root | `merchant_id`, `name`, `cohort` |
| `customers` | Merchant-scoped customer profile | `customer_id`, `merchant_id`, `payment_history_score`, `past_recovery_rate`, `preferred_channel` |
| `opportunities` | **The economic object the entire loop reasons about** — one row per distinct revenue-at-risk situation, not per payment attempt | `opportunity_id`, `merchant_id`, `customer_id`, `event_type`, `root_cause`, `amount_at_risk`, `status`, `created_at`, `resolved_at`, `recovered_bool`, `partial_recovery_amount`, `recovered_at`, `time_to_recovery`, `resolution_type` |
| `payments` | Transactional/event log — many rows can belong to one opportunity (e.g. repeated retry attempts) | `id`, `opportunity_id`, existing transactional fields |
| `recovery_candidates` | Every candidate the optimizer considered for an opportunity, not just the winner | `candidate_id`, `opportunity_id`, action/timing/method/channel, `predicted_p_treated`, `predicted_p_baseline`, `predicted_expected_amount_treated`, `predicted_expected_amount_baseline`, `cost`, `predicted_eiv`, `rank`, `pruned_stage`, `selected` |
| `recovery_decisions` | Compliance adjudication only — a closed outcome vocabulary (`executed`, `blocked_cooldown`, `blocked_max_retries`, `blocked_contact_hours`, `blocked_already_escalated`, `blocked_already_stopped`, `flagged_manual_review`) | `decision_id`, `opportunity_id`, `candidate_id`, `outcome`, `reasoning`, `triggered_by`, `timestamp` |
| `recovery_executions` | Execution lifecycle state, entirely separate from the compliance outcome above | `execution_id`, `decision_id`, `state` (`pending`/`scheduled`/`dispatched`/`executed`/`cancelled`/`superseded`/`failed`), `scheduled_for`, `executed_at`, `channel` |
| `experiment_assignment` | Live control/treatment holdout | `opportunity_id`, `group`, `assigned_at`, `assignment_method` |
| `bank_health_observations` | Network-health signal source | `bank`, `method`, `psp`, `window_start`, `window_end`, `success_rate`, `timeout_rate`, `health_score` |
| `messages` | Conversation thread, spanning the whole opportunity | `message_id`, `opportunity_id`, `sender`, `content`, `intent_extracted`, `intent_confidence`, `mentioned_reason`, `timestamp` |
| `dataset_registry` | Reproducibility manifest for every Data Factory generation run | dataset name, version, seed, calibration profile, generator version, row/case counts, validator results |

Three deliberate separations are structural, not stylistic: a **payment** (a transactional attempt) is distinct from an **opportunity** (the economic situation it belongs to); a **decision** (was this compliant) is distinct from an **execution** (has it actually fired) is distinct from an **outcome** (did the money come back). Collapsing any of these breaks lineage, breaks time-to-recovery measurement, or breaks the ability to audit what was blocked versus what was merely not-yet-dispatched.

Data-Factory-internal entities — richer synthetic customer/merchant/bank state used only for generation, and calibration-profile definitions — remain entirely offline and are never promoted into this production schema.

---

## 4. Dependency Graph

```
Phase 0  Bootstrap & Environment Repair
   │
   ▼
Phase 1  Schema Foundation
   │
   ▼
Phase 2  Canonical Synthetic World + Joint Candidate-Outcome Dataset
   │
   ▼
Phase 3  Joint Outcome / Treatment-Effect Model
   │
   ▼
Phase 4  Optimizer (candidate generation + Expected Incremental Value ranking)
   │
   ▼
Phase 5  Rule Engine & Bounded Executor
   │
   ▼
Phase 6  Live Experiment Assignment & Outcome Observation
   │
   ▼
Phase 7  Live Incremental Attribution & Reporting
   │
   ▼
Phase 8  Control Tower & Metrics API
   │
   ▼
Phase 9  Testing & Evaluation Hardening
   │
   ▼
Phase 10 Final Demonstration Assembly
```

Each phase's prerequisite is the one directly above it; nothing is planned before the infrastructure it depends on is stable. Phase 3 cannot be trained without Phase 2's dataset; Phase 4 cannot rank without Phase 3's model; Phase 5 cannot validate proposals that don't exist until Phase 4; Phase 6 needs Phase 5 producing real, executable decisions before a control group means anything; Phase 7 needs Phase 6's real assignment and outcome data; Phase 8 needs Phase 7's numbers to be worth displaying.

---

## 5. Phased Execution Plan

### Phase 0 — Bootstrap & Environment Repair
**Objective.** Make the repository run, deterministically, from a clean checkout.
**Components affected.** `requirements.txt`, `db/db.py`, `data/*.json` (seed data), `ml/data/training_corpus.csv`, import statements across `engine/*.py` and `api/*.py`.
**Implementation work.** Populate `requirements.txt` from the actual import graph, pinned. Generate a fresh, schema-valid seed dataset (the originals are not present in the repository; do not claim to reproduce a specific prior dataset). Build the database from the existing DDL. Regenerate the training corpus via the existing generator (deterministic given its fixed seed) and confirm it reproduces model behavior consistent with the two already-trained artifacts. Standardize on one import root across the codebase — recommended: the directory containing `backend/`, matching the API layer's existing convention — and remove the redundant `sys.path` hack from the engine modules.
**Dependencies.** None.
**Validation/tests.** A smoke test: fresh checkout → install → seed → run the batch pipeline → assert non-zero, distributionally sane output (e.g. payment-failure events predominantly produce `retry` decisions, matching the existing hardcoded logic) with zero manual intervention.
**Risks.** Regenerated seed data will not numerically match any previously-observed demo figures — acceptable, since no specific historical numbers are a requirement.
**Definition of done.** Clean checkout boots, seeds, and processes real events end-to-end with no manual patching, and the smoke test's distributional assertion passes.

### Phase 1 — Schema Foundation
**Objective.** Stand up the corrected data model in one deliberate pass, since every subsequent phase depends on entities that do not exist in the current flat schema.
**Components affected.** `db/db.py` DDL; every module that constructs or reads a `payments`/`recovery_actions` row.
**Implementation work.** Introduce `merchants`, `opportunities`, `recovery_candidates`, `recovery_decisions`, `recovery_executions`, `experiment_assignment`, `bank_health_observations`, `dataset_registry` as specified in Section 3. Add `merchant_id` to `customers`. Add `opportunity_id` to `payments` and `messages`. Retire `recovery_actions` as a single overloaded table, splitting its responsibilities across `recovery_decisions` (compliance outcome) and `recovery_executions` (lifecycle state) going forward, while opportunity-level `recovered_bool`/`partial_recovery_amount`/`recovered_at`/`time_to_recovery`/`resolution_type` fields hold the business outcome.
**Dependencies.** Phase 0.
**Validation/tests.** A migration test confirming a fresh DB matches the new DDL. A referential-integrity test: every payment resolves to exactly one opportunity; an opportunity can aggregate many payments (e.g. three retry attempts against the same underlying failure). A structural test proving a decision, an execution, and a business outcome for the same opportunity can never be written to or read from the same row — the three-way separation is enforced by schema, not convention.
**Risks.** Under-specifying now would force repeated migrations later; every field above is justified against a specific later phase's need, not speculative.
**Definition of done.** New DDL applied; a constructed multi-retry test fixture correctly aggregates under one `opportunities` row; the three state-types are structurally distinct.

### Phase 2 — Canonical Synthetic World + Joint Candidate-Outcome Dataset
**Objective.** Build the offline infrastructure that generates calibrated, reproducible, counterfactually-valid training and evaluation data for everything downstream — one coherent synthetic world, not a scattering of independent generators.
**Components affected.** New `data_factory/` package: `entities.py` (persistent synthetic customers, merchants, banks/PSPs — genuinely persistent across simulated time, so a repeat customer's behavior in one opportunity is informed by their history in prior ones), `bank_health_timeseries.py` (time-indexed, evolving bank/method/PSP health series), `candidate_generation.py` (the shared eligibility/pruning logic also used live in Phase 4 — built once, imported by both), `candidate_outcome_dataset.py` (the single joint dataset), `calibration_profiles/` (at least two distinct, named parameter sets — e.g. a baseline and a stress profile — sharing the same generator code but different distributional assumptions), `dataset_registry.py` (writes a manifest per run: dataset name, version, seed, calibration profile, generator version, row/case counts, validator results).
**Implementation work.** For every synthetic case: sample hidden state (liquidity, issuer availability, payment-method health, customer responsiveness, bank condition, recovery willingness) **once**, and reuse that exact draw across every candidate generated for that case — this is the single property that makes cross-candidate comparison causally meaningful, and it is preserved unchanged from the pattern already proven correct in the existing generator. Generate the eligible candidate set (action × timing bucket × payment method × channel, always including an explicit "do nothing" candidate) using the same shared eligibility logic Phase 4 will use live. For every candidate, draw one stochastic potential outcome from a single shared generative function taking the full candidate tuple as input — not from separate per-dimension functions — including whether recovery occurred, the recovered amount (supporting partial recovery, not just a binary outcome), and time-to-recovery. Generalize intervention fatigue across every contact-type candidate, not only retries. Generate the bank-health time series as part of the same world, so it is available as a feature from the very first training run rather than bolted on later. Freeze the current generator's code, unmodified, in a clearly labeled legacy module purely so the two already-shipped model artifacts remain reproducible from source if ever needed — this frozen module is not a constraint on the new generator's design or output shape.
**Dependencies.** Phase 1 (for the target schema the export step needs to know).
**Validation/tests.** The existing grouped-split, zero-case-overlap leakage assertion, generalized to the joint dataset. A temporal-order leakage test: no candidate's features may be computed using information only available after that candidate's own decision point. A customer-level leakage check, now meaningful because customers persist across cases. Distributional sanity checks (amounts, methods, failure types land in plausible ranges) and directional relationship checks (e.g. lower simulated bank health measurably correlates with higher simulated technical-failure rates). A reproducibility test: identical seed and calibration-profile version produce identical output on a second run. A direct sanity check that the dataset's empirical treatment effect (computed by comparing potential outcomes across candidates for the same case) matches the simulator's own generative effect functions by construction — since this is checkable by design in a synthetic world, it must be checked, not assumed. Semantic/statistical equivalence checks against the frozen legacy module confirming the properties that should carry over (hidden-state-once-per-case, the qualitative shape of the retry-count penalty and responsiveness curve) are still present — not a requirement for byte-identical output, which is neither achievable nor meaningful once the schema has genuinely changed.
**Risks.** A dataset's ground truth must never be derived from another model's predictions — every dataset's stochastic outcome function is defined independently from shared hidden state, never bootstrapped from a previously-trained model's output, to avoid the new model simply learning to reproduce an old model's biases. Candidate-space size inside generation itself is bounded by using the same eligibility/pruning logic the live optimizer will use, so the training distribution matches what will actually be queried in production rather than wastefully covering combinations nothing will ever score live.
**Definition of done.** The joint dataset generates successfully under both calibration profiles, passes every validator above, is recorded in the dataset registry, and its empirical treatment effects match the generator's own analytic effect functions within tolerance.

### Phase 3 — Joint Outcome / Treatment-Effect Model
**Objective.** Train the one model the system needs to answer, for any candidate, "what outcome do we expect" — including the "do nothing" baseline needed to compute incremental value — and ship live network-health feature computation in the same phase as training, so there is never a model deployed expecting a feature the serving path doesn't yet supply.
**Components affected.** New `ml/train_outcome_model.py`, new `ml/inference.py` (the single model-loading and feature-computation module, used identically whether called from offline evaluation or the live optimizer — replacing the previously copy-pasted lazy-load pattern and eliminating any risk of training-time and serving-time feature computation silently diverging).
**Implementation work.** Train one model estimating, jointly, the probability of recovery and the expected recovered amount, conditioned on the full candidate tuple (action, timing, method, channel) plus context (customer, merchant, network-health features) — with candidate type itself as a model feature, so the same model can be evaluated at any candidate, including the "do nothing" baseline, and the difference between two evaluations is what yields a treatment-effect estimate. Compute a rolling-aggregate live network-health feature (trailing-window success/timeout rate per bank/method) from `bank_health_observations`, using the exact same computation at training time and at serving time.
**Dependencies.** Phase 2.
**Validation/tests.** Calibration check (predicted-vs-actual outcome rate in quantile bins) on held-out data from the training calibration profile, thresholded as a hard pass/fail, not merely reported. A ground-truth treatment-effect check: because the synthetic environment's true effect function is known by construction, the model's estimated treatment effect is compared directly against it, in direction always and in magnitude within a stated tolerance, across a representative sample of the candidate space — if this fails, the modeling approach must be strengthened (e.g. estimating outcomes for treated and untreated cases with separate models rather than one shared model, to reduce the risk of a single regularized model shrinking a weak treatment signal toward zero) before the model is trusted downstream. A cross-profile generalization check: the model, trained only on the baseline calibration profile, must retain acceptable calibration on the unseen stress profile — this is the check that actually distinguishes a model that learned real structure from one that memorized one profile's specific numbers, and it is treated as more important evidence than repeating training with multiple random seeds. A temporal generalization check: calibration and ranking direction hold on a later, unseen simulated time window of the same profile. Multi-seed robustness as a supporting, secondary check.
**Risks.** A model that estimates outcomes for treated and untreated cases as one shared function can under-estimate true treatment effects when regularization shrinks a weak treatment signal — mitigated by the ground-truth check above being a hard gate, not a diagnostic.
**Definition of done.** One model artifact passes calibration, ground-truth treatment-effect agreement, cross-profile generalization, and temporal generalization checks; the same inference module produces identical output whether invoked from an offline harness or the live optimizer, verified directly by a test that runs the same case through both paths.

### Phase 4 — Optimizer
**Objective.** Build the component that generates a bounded, relevant candidate set for a real opportunity and ranks it by Expected Incremental Value — the capability that does not exist in the current hardcoded-lookup system.
**Components affected.** `data_factory/candidate_generation.py` (reused, not reimplemented), new `engine/optimize.py`.
**Implementation work.** Candidate generation proceeds in two cheap stages before any model is invoked: first, structural eligibility (which action/timing/method/channel combinations are even meaningful given the event type, root cause, and current compliance state — reusing the existing distinction between root causes that call for a method change versus those that don't); second, a lightweight relevance filter (collapsing timing to root-cause-appropriate windows, collapsing channel to the customer's supported/preferred options plus at most one exploratory alternative) that bounds the candidate set to a small constant before any model scoring happens. The surviving bounded set, always including "do nothing," is scored twice per candidate by Phase 3's model — once as proposed, once as the baseline — and Expected Incremental Value is computed as the difference in expected recovered amount between the two, minus the candidate's cost. Candidates are ranked descending by Expected Incremental Value and the full ranked set, not just the winner, is written to `recovery_candidates` for auditability. `optimize.py` has read access to opportunity, payment, and network-health data, and write access only to `recovery_candidates` — an audit/proposal table with no execution authority.
**Dependencies.** Phase 3.
**Validation/tests.** Unit tests per eligibility rule (e.g. no payment-method-change candidate is ever generated for a root cause where a method change is meaningless). A static check enforcing that no code anywhere in the candidate-generation or optimization modules imports or calls anything with execution authority — this is the single highest-severity boundary in the system and is checked mechanically, not left to code-review convention.
**Risks.** Candidate-space size at serving time is bounded by the same two-stage pruning used offline, capping both latency and the risk of over-covering combinations that were never relevant. There is a real temptation to let this component also enforce compliance, since it already reasons about eligibility — it must not: eligibility here concerns which candidates are worth scoring at all (a relevance question), never whether a specific candidate is currently permitted to fire (a compliance question), which remains exclusively the rule engine's concern even though this means the same candidate may be conceptually reconsidered from two different angles by two different components — that overlap is intentional and is what prevents authority drift, not wasted work.
**Definition of done.** For a representative sample of opportunities, the optimizer produces a ranked, Expected-Incremental-Value-ordered candidate list including a correctly zero-valued "do nothing" option, logs the full considered set to `recovery_candidates`, and the static authority-boundary check passes.

### Phase 5 — Rule Engine & Bounded Executor
**Objective.** Extend the existing rule engine to validate optimizer proposals instead of a hardcoded default, and extend execution to support scheduling as a lifecycle state — while keeping the executable action vocabulary deliberately narrower than the candidate space the optimizer is allowed to reason about.
**Components affected.** `decide_action()`, `execute_action()`, new `engine/dispatch_scheduled.py`.
**Implementation work.** `decide_action()` gains an optional parameter accepting the optimizer's full ranked candidate list (not just the top pick), so that if the top-ranked candidate is blocked by a compliance rule, the function can fall through to the next-best compliant candidate rather than defaulting to inaction purely because the single best option happened to violate a rule. When no candidate list is supplied, behavior is unchanged from the existing hardcoded logic — this preserves full backward compatibility and allows the new pathway to be feature-flagged on or off without a code revert. Every existing compliance check (cooldown, max retries, contact hours, already-escalated/stopped, confidence and mismatch gating) continues to run unchanged against whichever candidate is being validated. The function's output is written to `recovery_decisions` using the existing closed compliance-outcome vocabulary, unchanged. Execution is split from decision-making: `execute_action()` writes to `recovery_executions` using a lifecycle state (`pending`, `scheduled`, `dispatched`, `executed`, `cancelled`, `superseded`, `failed`) — scheduling is represented here, as a state transition, never as a new value in the compliance-outcome vocabulary. `dispatch_scheduled.py` runs as a periodic sweep, structurally identical in pattern to the existing batch loop, and is the only component permitted to advance a scheduled execution to dispatched/executed. The executable action vocabulary is deliberately narrow — retry, reminder (with a channel attribute), payment link, escalate, stop — and structurally excludes autonomous payment-method switching: there is no code path anywhere in the executor capable of dispatching a method change. When a payment-method-change candidate is the optimizer's top recommendation, the rule engine either falls through to the next executable-and-compliant candidate or routes to manual review — it is never auto-executed. This is not a scope limitation apologized for; it is a deliberate, permanent boundary, because autonomously changing how a customer pays is a materially different risk category from retrying, reminding, or escalating, and the ability to *recommend* it with visible reasoning is itself a demonstrable capability without needing the ability to *act* on it unsupervised.
**Dependencies.** Phase 4.
**Validation/tests.** A full regression suite proving that decisions produced with the optimizer disabled are identical to the existing hardcoded-lookup behavior. The existing authority tests (only `decide_action`'s `allowed: True` output ever reaches the executor). Scheduling lifecycle tests: a scheduled action due in the past executes correctly when the dispatcher runs; one due in the future is untouched; one whose opportunity is stopped or escalated by another path before its due time is correctly abandoned rather than blindly fired. A structural test confirming that no query can conflate a `recovery_executions` lifecycle state with a `recovery_decisions` compliance outcome. A structural test confirming there is no reachable code path anywhere that dispatches a payment-method-change action.
**Risks.** This is the highest-consequence integration point in the system; mitigated by feature-flagging the optimizer-driven pathway so it can be instantly disabled without a code revert if it misbehaves during testing, and by the mechanical (not conventional) enforcement of the method-change restriction.
**Definition of done.** All entry points produce regression-proven identical decisions with the optimizer disabled, and produce optimizer-driven, Expected-Incremental-Value-ranked, correctly scheduled decisions — with payment-method changes visibly recommended but never auto-executed — when enabled.

### Phase 6 — Live Experiment Assignment & Outcome Observation
**Objective.** Build the mechanism that makes a genuine incremental-₹ claim possible on real decisions: randomized holdout and real outcome capture. This phase exercises an incrementality concept that already exists from Phase 2/3's synthetic design — it does not invent that concept, only validates it against reality.
**Components affected.** New `engine/assign_experiment_group.py`, new `engine/observe_outcome.py`.
**Implementation work.** At opportunity-creation time, each opportunity is randomly (or via a documented, defensible quasi-experimental method) assigned to `treatment` or `control` and recorded in `experiment_assignment`. Control-group opportunities are still diagnosed and quantified — their ₹-at-risk still counts toward reporting — but are suppressed from the optimizer/executor pathway, receiving no automated intervention. A single outcome-ingestion function becomes the sole path by which a `recovered`/`partially recovered`/`lost` business outcome is ever written to an opportunity, regardless of whether the source is a manual confirmation (retained as a legitimate, clearly-labeled operational utility for testing and demonstration) or, in a genuine future integration, a real payment-success event — one code path, never two divergent ones.
**Dependencies.** Phase 5.
**Validation/tests.** A randomization-balance test: treatment and control groups are statistically comparable in ₹-at-risk and root-cause distribution at assignment time — without this, any incremental number computed downstream is untrustworthy regardless of how it's calculated. A counterfactual-consistency test: control-group opportunities never show a selected, executed action past their assignment point.
**Risks.** A holdout percentage that is too small, or an assignment process that is not genuinely random, silently invalidates every incremental number computed afterward — the randomization-balance test is a hard gate specifically because this failure mode is invisible unless checked directly.
**Definition of done.** Real opportunities are randomly assigned, control suppression is verifiably real rather than cosmetic, and outcomes are captured through exactly one ingestion path.

### Phase 7 — Live Incremental Attribution & Reporting
**Objective.** Compute the number the whole system exists to produce: incremental ₹ recovered, live, from real assignment and outcome data — and cross-check it against what the synthetic model predicted for the same population, as an honesty and debugging mechanism.
**Components affected.** New `analytics/incremental_attribution.py`.
**Implementation work.** Compare treatment-group and control-group recovery rates (and, where volume allows, recovered amounts) for a given segment and time window, compute the incremental rate and its confidence interval, and convert to an incremental-₹ figure. Separately, compare this observed figure against what the optimizer's Expected Incremental Value predicted for the same population at decision time, and report the delta honestly — not forcing agreement, but surfacing it as a live diagnostic. No number in this module is ever cached; every figure is computed from a live query at request time.
**Dependencies.** Phase 6.
**Validation/tests.** A live-traceability check: every number this module can report is reachable from an inspectable, reproducible query, never only from narration. A baseline-vs-optimized-policy business experiment: a fixed-schedule baseline and a simple root-cause-rule baseline are compared against the optimizer-driven treatment on incremental ₹ recovered, not on predictive accuracy, since predictive accuracy is not the business objective.
**Risks.** Any published number from this module must be labeled by its source population and time window; a synthetic-environment result and a live result must never be presented as interchangeable or as validating each other beyond the explicit predicted-vs-observed diagnostic this phase defines.
**Definition of done.** A live-queryable incremental-₹ figure with a confidence interval exists for at least one real segment, and the predicted-vs-observed diagnostic is computed and reported.

### Phase 8 — Control Tower & Metrics API
**Objective.** Build the merchant-facing surface the product needs to be demonstrable and useful, on top of metrics that are now actually correct.
**Components affected.** `api/queries.py` (metrics rebuilt), new endpoints for the opportunity queue and case-level reasoning, a new frontend application.
**Implementation work.** Retire the currently-mislabeled "amount at risk" metric (which today includes already-recovered money) in favor of a correctly opportunity-scoped figure. Add merchant-scoped headline economics: ₹ at risk, expected recoverable ₹ (the sum of Expected Incremental Value across open opportunities), ₹ recovered, and incremental ₹ recovered. Add an opportunity queue ranked by Expected Incremental Value, replacing today's unranked case list. Add a case-reasoning endpoint returning the full candidate set from `recovery_candidates` alongside the chosen decision, supporting a live reasoning panel that shows model output, ranked candidates (including any payment-method-change recommendation, explicitly labeled as advisory-only), the compliance decision, and the resulting execution, together — not narrated after the fact. Build exactly the required views and stop there: opportunity queue, headline economics, live reasoning panel, audit trail, and live-recomputing aggregate metrics. Visual polish is explicitly not the priority; proving the reasoning is.
**Dependencies.** Phase 7 (for incremental ₹ to be meaningful to display), Phase 1 (for merchant scoping).
**Validation/tests.** API contract tests for new and changed endpoints. A live-traceability test mapping every number the frontend displays to the specific live query that produces it.
**Risks.** Scope creep into dashboard polish at the expense of the reasoning panel's honesty — mitigated by treating the five required views as the complete scope for this phase.
**Definition of done.** All required views render from live data; the previously mislabeled metric is gone; every displayed number is traceable to a live query.

### Phase 9 — Testing & Evaluation Hardening
**Objective.** Consolidate the validation already required throughout every phase above into one enforced suite, and add whole-system checks that only make sense once the system is complete.
**Components affected.** The full test suite across unit/component, end-to-end system, and experimental/business-evaluation tiers.
**Implementation work.** Unit/component: per-function compliance branches, candidate eligibility rules, scheduling logic, structural authority-boundary checks. End-to-end system: all entry points run against seeded fixtures across a representative event/root-cause/timing matrix; adversarial and corrupted-input tests (malformed fields, out-of-range confidence scores, contradicted promises, language-switching replies, off-topic replies, negative or zero amounts) asserting fail-closed behavior in every case; a full authority-boundary sweep across the finished codebase, not just the modules where it was originally introduced. Experimental/business: the ground-truth treatment-effect check, the cross-profile and temporal generalization checks, and the baseline-vs-optimized-policy experiment are re-run as gating checks on the finished system, not left as one-time checks performed only when their originating phase was built.
**Dependencies.** All prior phases.
**Validation/tests.** This phase is itself the validation layer; its own definition of done is the bar.
**Risks.** Treating testing as a final phase risks it being scoped as an afterthought — mitigated by every individual phase above already specifying its own required tests as a hard gate, so this phase is consolidation and system-wide extension, not the first time validation is considered.
**Definition of done.** All three tiers pass as one enforced suite; no phase's individual gate has silently regressed.

### Phase 10 — Final Demonstration Assembly
**Objective.** Assemble the live proof narrative: an opportunity queue, ranked by Expected Incremental Value; a root-cause and ₹ breakdown; the system's reasoning shown live, including a moment where "do nothing" and a payment-method-change recommendation are both visibly considered and correctly not auto-executed; a deliberately non-compliant action blocked live, with its logged reason visible; execution; observed outcome; and a close on the incremental-₹ figure with its confidence interval and the predicted-vs-observed diagnostic.
**Components affected.** None new — this phase is choreography, environment preparation, and a final honesty pass ensuring no synthetic-environment result is described anywhere as a production claim.
**Dependencies.** All prior phases.
**Validation/tests.** A full dry run from a freshly seeded environment, unscripted, including at least one adversarial input typed live.
**Risks.** Overclaiming synthetic results as production results — the single most damaging credibility failure available at this stage, avoided by explicit labeling throughout, not by omission.
**Definition of done.** A single walkthrough from a fresh seeded environment produces every displayed number live, visibly blocks at least one non-compliant action, and closes on the incremental-₹ figure with its confidence interval.

---

## 6. Data Factory — Scope and Boundaries

The Data Factory exists to answer exactly one question: given the joint recovery/treatment-effect model and the optimizer both need data that does not exist anywhere in the real world (no dataset captures the joint relationship between customer, failure reason, payment method, bank condition, timing, cost, and outcome at the granularity this product needs), how is that data generated in a way that is calibrated, reproducible, and genuinely supports causal comparison rather than merely predictive accuracy?

It is built as **one canonical synthetic world** — persistent entities, an evolving network-health time series, hidden state sampled once per case and reused across every candidate for that case — producing **one joint Candidate-Outcome dataset**, not a scattering of narrowly-scoped independent datasets. Network-health and customer/merchant context are feature streams within this one world, not separate datasets requiring separate models. Every generation run is versioned in a registry recording its seed, calibration profile, and generator version, so any result can be reproduced exactly or investigated when it can't be.

It never touches production data. It never calls the live decision pipeline. It never makes a recovery decision. Its output ends the moment a validated, registered dataset is produced; everything downstream of that point is the Revenue Recovery Intelligence Engine's responsibility, not the Data Factory's.

---

## 7. Modeling and Optimization Design

**One joint model**, not several narrow ones, estimates outcomes (recovery probability and expected recovered amount) as a function of the full candidate tuple — action, timing, payment method, channel — plus context, including network-health and customer/merchant features. Timing and method are input features of this one model, never the subject of separately-trained models later composed together, because composing two independently-trained marginal models to answer a joint question produces statistically invalid estimates.

**Expected Incremental Value is derived, never trained as a direct target.** It is computed as the difference between the model's evaluation at a proposed candidate and its evaluation at the "do nothing" baseline for the same case, using expected recovered amount (not a bare probability) so that partial recovery is correctly reflected, net of the candidate's cost. Because the synthetic environment's true treatment effect is known by construction, the model's estimated effect is checked directly against that ground truth before being trusted — an evaluation practice available specifically because the environment is synthetic, and one this plan makes a hard, non-optional gate rather than an optional diagnostic.

**Customer and merchant context shape outcomes through the model's own learned feature interactions**, not through a separate, hand-tuned adjustment layer applied after the fact. A rule such as "prefer a customer's stated preferred channel" is not asserted as a fixed bonus; it is something the model either does or does not learn to weight from the synthetic world's actual generative structure, and its presence is checkable against ground truth the same way the core treatment effect is. The only context that legitimately bypasses the model entirely is a hard, non-probabilistic business constraint (for example, a merchant-configured rule never to contact customers via a specific channel) — this is a compliance-like constraint, not a preference, and belongs in the rule engine's eligibility logic, not in the optimizer's ranking.

**Candidate generation is staged and bounded**, using the same shared logic offline and live: structural eligibility first (cheap, rule-based), a relevance pre-filter second (cheap heuristics bounding the set to a small constant), and full model scoring only on the bounded survivors. This avoids both an uncontrolled combinatorial explosion and a mismatch between what the Data Factory trains on and what the live optimizer actually queries.

**Payment-method changes are evaluable but never executable.** The model and optimizer can score and rank a method-change candidate and the Control Tower can display it as a recommendation with full reasoning — this is a genuine, demonstrable intelligence capability — but the executor has no code path capable of dispatching it. This is a permanent architectural boundary, not a temporary limitation.

**Live incremental attribution validates and recalibrates, it does not originate incrementality.** The capacity to reason about incremental value exists from the moment the Data Factory and the joint model exist, because a synthetic environment can generate potential outcomes for every candidate on the same case for free. Live control/treatment experimentation, introduced once real decisions are being made, is what confirms that capacity holds up against reality and what eventually recalibrates the model against real, not merely synthetic, evidence — a later, different, and no less important step, but a validation step, not the origin of the concept.

---

## 8. Testing and Evaluation Strategy

| Tier | Scope | Representative checks |
|---|---|---|
| Unit / component | Individual functions in isolation | Every compliance branch in the rule engine; candidate eligibility rules per root cause; scheduling due/not-due/abandoned logic; strict validation of LLM output shape |
| End-to-end system | Full pipeline via every entry point, against seeded fixtures | Representative event×root-cause×timing matrix; adversarial and corrupted-input handling (malformed fields, out-of-range confidence, contradicted promises, language-switching, off-topic replies, invalid amounts); authority-boundary static checks; case-level, temporal, and customer-level leakage checks, each independently, since they are distinct failure modes; reproducibility of dataset generation and experiment results under identical seed/config |
| Experimental / business | Whether the system's core claims actually hold | Calibration on held-out data; direct comparison of estimated treatment effects against known synthetic ground truth; cross-calibration-profile generalization (the primary evidence against simulator-specific overfitting, stronger than repeated-seed testing alone); temporal-holdout generalization; randomization-balance testing for live experiment assignment; baseline-vs-optimized-policy comparison reported on incremental ₹ with a confidence interval, never on predictive accuracy alone |

Every phase's definition of done above asserts a statistical or economic property, not merely that code executes without error or that a test suite returns green — "the model is calibrated," "the estimated effect matches known ground truth," "the policy beats the baseline on unseen data," not "the script ran."

---

## 9. Permanent Invariants

- The rule engine is the sole authority permitted to approve execution; nothing else — not the ML model, not the optimizer, not the LLM — ever gains that authority, directly or through an unchecked side channel.
- The LLM generates language and extracts intent only; it never selects, triggers, or overrides a recovery action, and the information it extracts from a customer is never contaminated by information it would need to independently verify.
- "Do nothing" is always a scored candidate with a defined zero baseline value, never an implicit fallback and never omitted from ranking.
- A payment, an opportunity, a decision, an execution, and an outcome are five distinct concepts with five distinct representations; none of them are ever collapsed into one field or one table for convenience.
- Payment-method changes are never dispatched autonomously, structurally, regardless of how strongly the optimizer recommends one.
- No claim of incremental ₹ is made from anything other than real, randomized (or documented quasi-experimental) assignment; a synthetic-environment result is always labeled as synthetic.
- Every dataset generation run is versioned — seed, calibration profile, generator version — before its output is used to train or evaluate anything.
- Hidden state for a synthetic case is sampled once and reused across every candidate generated for that case, without exception, since this is what makes cross-candidate comparison meaningful at all.
- Every action the system takes or declines to take is logged with a reason; nothing happens silently.

---

## 10. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A single shared model under-estimates true treatment effects due to regularization | Hard gate comparing estimated effects against known synthetic ground truth before the model is trusted; escalate to separately-modeled treated/untreated estimates if the gate fails |
| A model trained with a feature the live serving path doesn't yet supply | Training and live feature computation implemented as one shared code path, shipped together, never split across phases |
| Undetected overfitting to one simulator calibration | Cross-calibration-profile and temporal-holdout generalization gates, treated as the primary generalization evidence |
| Uncontrolled candidate-space growth | Two-stage, shared eligibility/relevance pruning used identically offline and live |
| Autonomous execution of a capability the product deliberately withholds (payment-method switching) | Structural absence of any dispatch code path for it, not a runtime check |
| Conflating a payment with the opportunity it belongs to | Explicit, separate `opportunities` entity from the first schema pass onward |
| Conflating compliance outcome, execution state, and business outcome | Three separate, structurally distinct representations from the first schema pass onward |
| An untrustworthy incremental-₹ figure from a small or non-random holdout | Hard randomization-balance gate before any incremental number is reported |
| Authority drift through well-intentioned redundant safety checks | An explicit, single-owner authority matrix, enforced by static checks per phase |
| A broken or fragile local environment blocking all further work | Bootstrap repair treated as a hard, first, blocking phase |

---

## 11. Exact First Implementation Step

Begin with environment repair, in this order, before any other work: reconstruct the dependency manifest from the actual import graph and pin it; generate a fresh, schema-valid seed dataset since none currently ships with the repository; build the database from the existing schema definition; regenerate the training corpus from the existing generator and confirm it produces behavior consistent with the already-trained model artifacts; unify the codebase on one import convention and remove the redundant path-manipulation pattern from the engine modules; run the full batch pipeline end-to-end against the freshly seeded database and confirm sensible, non-empty output with no manual intervention.

Only once that passes should schema work begin — and the first genuinely new structural decision is standing up the `opportunities` entity and the three-way separation between candidates, decisions, and executions, before a single line of the optimizer or the joint outcome model is written. Building either against the current flattened schema would guarantee exactly the kind of rework this plan exists to prevent.
