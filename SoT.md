# Revenue Recovery Intelligence Engine — Source of Truth
*The single authoritative blueprint for the product. Supersedes all prior SoT versions. This document defines what we are building and why — not a build log, not a hackathon plan.*

---

## 1. Product Thesis

Payment failure, checkout abandonment, subscription lapse, and overdue receivables are not four problems — they are four symptoms of the same underlying condition: **merchant revenue leaking out of the funnel**. Razorpay, and every serious competitor (Stripe, Adyen, Checkout.com, Juspay, Yuno, Gr4vy, Primer), already ships mature point solutions for each symptom individually: smart retries, dunning sequences, intelligent routing, basic reminder agents. That category is crowded and largely commoditized. Building another one adds no defensible value.

The whitespace sits one layer above: a system that treats these symptoms as a single economic surface and answers one question continuously, for every merchant:

> **How much revenue is currently at risk, why, how much of it can actually be recovered, which intervention — for which customer, under which conditions, at what time — maximizes expected incremental recovered ₹, how do we execute that safely, and can we prove the money it actually caused?**

This is the **Revenue Recovery Intelligence Engine**. It is not a smarter retry bot. It is a decision and measurement layer that sits above Razorpay's existing execution capabilities (routing, retries, payment links, WhatsApp/email outreach, collections tooling) and treats them as **tools it calls**, not capabilities it rebuilds.

**Why this matters to Razorpay specifically:** Razorpay's commercial relationship with a merchant scales with that merchant's transaction volume and health. A layer that provably increases a merchant's realized revenue — and can show its work — is a retention and expansion lever, not just a support feature. The product's real currency is a defensible number: *incremental ₹ recovered that would not have been recovered otherwise*, not raw ₹ recovered (which mixes in money that would have come back on its own).

---

## 2. What This Is Deliberately Not

Rejected as primary innovation claims — mature, commoditized, or already owned by Razorpay/competitors:
- Generic smart retry / retry-timing-only engines (Stripe, Adyen, Razorpay itself all ship this)
- Generic dunning / reminder bots
- Payment routing or gateway failover presented as the core idea (Juspay's territory)
- Outage/health detection presented as a novel invention (Juspay, Razorpay already do this)
- A dashboard that reports failures without attaching a ₹ figure and a causal claim to them
- An LLM chatbot that recommends actions but cannot execute, measure, or be audited
- Fraud/DDOS/spam detection (a different problem with a different evaluation regime — do not drift into it)

These capabilities are **treated as execution tools the Engine can call**, not systems to rebuild. The Engine's job is to decide *whether, how, and when* to use them, and to prove what using them was worth.

---

## 3. The Intelligence Loop

The product is organized around one continuous loop, run per merchant, per opportunity, per outcome:

**Detect → Diagnose → Quantify → Predict → Optimize → Act → Observe → Measure Incremental Impact → Learn**

### 3.1 Detect
Continuously scan every revenue-relevant event stream — payment failure, checkout abandonment, subscription/UPI AutoPay lapse, overdue B2B receivable, and (where signal exists) early degradation patterns — and emit a single, unified representation: a **Revenue-at-Risk Opportunity**. All four event families feed the *same* representation and the *same* downstream pipeline. There is exactly one recovery engine, not four parallel ones per event type — this is the single highest-leverage architectural rule in the system and the one most likely to be silently violated by ad-hoc, per-event-type code.

### 3.2 Diagnose
Determine *why* revenue is at risk: root-cause classification for payment failures (insufficient funds, card declined, bank/gateway timeout, 3DS auth failure, expired card, network failure — a deliberately bounded, closed taxonomy), plus context signals for the other event families (abandonment stage, subscription lapse reason, receivable aging bucket). Diagnosis also incorporates **network-level context** where available: bank/issuer/gateway health at the time of failure, so "card declined at 2am during a known issuer degradation window" is diagnosed differently from an isolated decline. This network-health signal is Razorpay's genuine structural advantage — no single-merchant competitor can see it, because it requires cross-merchant visibility.

### 3.3 Quantify
Attach a ₹ figure to the opportunity, not just a probability. Two numbers matter, and they must never be conflated:
- **₹ at risk** — the amount that will be lost if nothing happens.
- **Expected Recovery Value (ERV)** — `amount × P(recovery | intervention) × P(intervention is causally incremental) − intervention_cost`.

ERV, not raw recovery probability, is the ranking key for everything downstream. A ₹50,000 opportunity at 60% probability outranks a ₹1,000 opportunity at 90% probability — this reframes the objective from "maximize hit rate" to "maximize recovered revenue," which is the actual business metric.

### 3.4 Predict
ML estimates the components ERV needs: recovery probability conditioned on a specific candidate action (not a generic "will this ever recover" score), and — where timing genuinely matters — the probability of success at different candidate intervention windows. Root causes do not share a universal optimal timing: a technical/network failure benefits from a fast retry; insufficient funds benefits from waiting for a plausible salary/settlement cycle; an expired card cannot be timed around at all, it requires a method change. This is a real, non-generic modeling problem — it is exactly what Stripe's Smart Retries and Adyen's Auto Rescue solve at their scale, and it is worth solving at ours, honestly labeled as a synthetic-environment result rather than a production claim (see Section 8).

### 3.5 Optimize
Rank every eligible **(action, timing, payment-method)** combination for an opportunity by expected incremental ₹, subject to: compliance eligibility, customer/merchant-specific policy, and intervention cost (contacting a customer has a cost — in channel budget and in fatigue risk — and "do nothing" must always be a live, competitive option, not a fallback of last resort). Customer and merchant context (payment history score, past recovery rate, preferred channel, merchant cohort behavior) shifts the ranking, not the ranking mechanism — this is a policy layer feeding one optimizer, not a second decision system.

The optimizer **proposes**; it does not have authority to act. See Section 5.

### 3.6 Act
The rule engine validates the optimizer's top-ranked proposal against hard compliance and safety constraints, and only then dispatches execution to the appropriate tool: retry, payment link, alternate payment method, outreach (WhatsApp/email/SMS via existing channels), escalation to a human queue, or deliberate no-action. Every dispatch is logged before and after execution — no silent actions.

### 3.7 Observe
Every action and its outcome (recovered / not recovered / partially recovered, time-to-resolution, customer response) is written to an append-only audit trail. This trail is the product's credibility mechanism: every number the Control Tower shows must trace back to a live, inspectable record, never only to narration.

### 3.8 Measure Incremental Impact
This is the differentiator most competitors skip entirely, and the one most defensible against "you just rebuilt Stripe's retry engine." A held-out control population (or, where a live control group is not viable, a calibrated counterfactual/uplift estimate) is maintained continuously, so the system can separate:
- **Baseline recovery** — what would have recovered with no intervention at all.
- **Incremental recovery** — what the intervention actually caused.

The headline metric the product reports to a merchant is **incremental ₹ recovered**, not gross ₹ recovered. This is a harder, more honest number, and it is the number that actually justifies the product's existence versus doing nothing.

### 3.9 Learn
Outcomes flow back into the risk model, the timing/action-value model, and the policy layer — but only through an explicit, versioned retraining/evaluation process (via the Data Factory, Section 6), never by silently mutating a live model mid-operation. The system that decides today's actions is never the same instance that is simultaneously rewriting itself from today's outcomes.

---

## 4. Core Differentiating Capabilities (the reasoned-through set)

Every candidate capability below was evaluated against: uniqueness vs. Razorpay/competitors, business value, technical credibility, demonstrability, defensibility, and coherence with the rest of the architecture. What follows is what survived. Capabilities that were considered and rejected (merchant benchmarking as a standalone engine, a graph database for relationship modeling, a full digital-twin simulation platform, a public recovery-as-a-service API) are noted in Section 10 as future-optional, explicitly not core, because they added surface area without adding a proportional amount of uniqueness or demoability.

**1. Unified Revenue-at-Risk representation.** One opportunity object, one pipeline, across payment failure, abandonment, subscription lapse, and receivables. Nobody in the competitive set unifies these; each is typically a separate tool with a separate dashboard. This is the architectural foundation everything else sits on.

**2. Expected Recovery Value as the ranking objective.** Reframes the problem from classification ("will this recover?") to portfolio optimization ("where does the next rupee of effort produce the most expected return?"). Cheap to implement, high demo impact, directly answers a CFO-level question.

**3. Action × Timing × Payment-Method optimization.** Not a single retry-timing model bolted onto a single action type — a joint ranking over the full eligible action space, cost-aware, with "do nothing" as a first-class competitive option. This is the technically deepest predictive component and the hardest one for a generic competitor to fake in a demo.

**4. Incremental Revenue Attribution.** The control/treatment or uplift-modeling discipline that separates recovered ₹ from caused ₹. This is the single most defensible, least-copied capability in the whole system (rated highest on uniqueness and lowest on competitive overlap in our own research) and is the correct final answer to "prove the financial impact."

**5. Network-level intelligence as a feature, not a product.** Bank/issuer/gateway health signals, calibrated from Razorpay's own cross-merchant visibility, feed the diagnosis and prediction stages as context. This is explicitly *not* pitched as "we invented health-based routing" (Juspay already does this) — it is pitched as the ingredient that makes diagnosis and timing prediction meaningfully better than a merchant-siloed competitor could ever build.

**6. Customer- and merchant-specific recovery policy.** The optimizer's ranking is conditioned on who the customer is and how this merchant's customers have historically behaved — not a universal policy applied identically everywhere.

**7. Intervention-cost and fatigue awareness.** Every proposed action carries an explicit cost (channel cost, contact-fatigue risk), so the optimizer is solving a constrained-budget problem, not a "always intervene" problem. This directly supports "do nothing" being a legitimate, frequently-correct output — a system that always acts is not intelligent, it is noisy.

**8. Autonomous-but-bounded orchestration with explainable decisioning.** The system acts without a human in the loop for every case, but never outside rule-engine-validated bounds, and every decision carries a human-readable reason. This is what makes "autonomous" trustworthy rather than reckless — see Section 5.

---

## 5. Safety, Authority, and Explainability Model — Architectural Invariant

This is non-negotiable and load-bearing for every other capability in this document.

**Authority chain (must never be violated, in this order):**
1. ML models **predict** (recovery probability, timing success probability, action value). They never select an action.
2. The **optimizer ranks** eligible candidates by expected incremental ₹. It never bypasses compliance checks and never executes anything.
3. The **rule engine has final authority.** It validates the optimizer's top proposal against hard compliance constraints and either approves, downgrades, or blocks it. Its decision is final and cannot be overridden by ML or LLM output.
4. The **executor** performs only the rule-engine-approved action.
5. The **LLM's role is strictly language and orchestration**: generating customer-facing messages, extracting structured intent from customer replies (promise-to-pay, dispute, payment-method-updated, multi-language input), and coordinating tool calls. It never selects, triggers, scores, or overrides a recovery action. This split directly targets the hardest AI-judgment question any serious reviewer will ask: *where did you choose not to use a model, and why.*

**Baseline compliance bounds** (configurable defaults, not sacred constants, but the *category* of constraint is a permanent architectural requirement — a production system without hard-coded stopping rules is not shippable): a bounded maximum retry count, a minimum cooldown between contact attempts, an auto-stop-and-escalate rule after a bounded no-response period, a restricted contact-hours window for customer-facing actions (escalation to a human queue is exempt — it is internal routing, not customer contact), and a confidence threshold below which the system defers to manual review rather than auto-acting on ambiguous input.

**Structural non-negotiables:**
- ML is never exposed to a *comparison* across multiple candidate actions for the same case inside the authority-critical decision function — only the single already-selected candidate is scored. Exposing a multi-action comparison to that boundary would create a de facto channel for ML to influence action selection even without an explicit override, which breaches the authority chain in spirit even if not in code.
- Data used to independently verify a customer's stated reason (e.g. what they say caused a failure) must never be shown to the model extracting that statement — feeding the system's existing belief into the extraction step creates anchoring bias and defeats the point of an independent cross-check.
- Every action, blocked or executed, is logged with a reason. A blocked action must be demonstrable on demand — a system that only ever shows successful actions looks curated, not trustworthy.
- Outcome and action-type vocabularies stay small, closed, and auditable. Do not add a new action type without updating execution, compliance, audit logging, and dataset labeling together — partial additions produce a system whose logged history no longer matches its live behavior.

---

## 6. The Data Factory — Enabling Subsystem, Not the Product

The Data Factory exists for exactly one reason: **the Engine's predictive and optimization layers need calibrated, reproducible, counterfactually-valid environments to train and evaluate against**, because no public dataset contains the joint relationships (customer × failure reason × payment method × bank condition × timing × outcome) this problem actually needs, and no hidden judge/production dataset is provided. It is offline infrastructure. It never touches production data, never calls the live decision pipeline, never makes a recovery decision, and its job ends the moment a validated dataset is exported.

**What it does:** ingests public reference sources for realistic calibration (never as ground truth), generates persistent synthetic entities (customers, merchants, banks/PSPs) with genuine hidden state, simulates events and outcomes stochastically (not from deterministic rules), and produces versioned, reproducible, task-specific datasets — recovery-risk, timing/action-value, customer-policy, network-health, and (when explicitly reopened) pre-failure-risk.

**Non-negotiable engineering lessons carried forward** (these were hard-won and apply to every future dataset built here):
- **Grouped, case-level train/test splits.** When one case produces multiple candidate-action or candidate-timing rows, all rows from the same case must stay in the same split — row-level random splitting leaks case-specific hidden state across the boundary and produces misleadingly good validation numbers.
- **Hidden state sampled once per case, reused across every candidate row for that case.** This is what makes multi-candidate comparisons causally valid "what-if" comparisons instead of independently-random rows.
- **Ground truth must be probabilistic, not deterministic.** No hard-coded rule like "a six-hour retry always succeeds" — the simulator produces a probability, then samples an outcome.
- **Every dataset must be reproducible** from source references, calibration profile, simulation configuration, generator version, and seed.
- **Public datasets calibrate; they do not replace production truth.** Nothing generated here is ever presented as real Razorpay data.

The current basic generator becomes one implementation living inside this Data Factory, not a separate thing to maintain.

---

## 7. ML / Optimization Architecture — Role Assignment

| Layer | Responsibility | Technology posture |
|---|---|---|
| Recovery probability | `P(recovery \| features, candidate action)` | Gradient-boosted trees (XGBoost/LightGBM) or logistic regression baseline; calibration checked, not just accuracy |
| Timing / action-value | `P(recovery \| features, candidate action, candidate timing)` → expected ₹ | Same model family, extended feature space; timing windows as a bounded, configurable set, not continuous optimization on day one |
| Network-health signal | Time-dependent bank/issuer/gateway health score | Rolling aggregates / anomaly detection (isolation forest, change-point rules) — no graph database, no streaming platform required for a credible first version |
| Incremental attribution | Causal/uplift estimate of intervention effect | Control/holdout comparison as the baseline method; uplift/CATE modeling as the advanced version once a control group exists |
| Optimizer | Rank eligible (action, timing, method) combinations by expected incremental ₹ minus cost | Deterministic scoring/ranking function first; contextual-bandit or constrained-optimization refinement later, only once the deterministic version is proven |
| Rule engine | Final compliance authority | Explicit, auditable, hard-coded logic — deliberately not a model |
| LLM | Message generation, intent extraction, orchestration | Structured JSON output only; never a control decision |

Prefer the simplest model that is honestly calibrated over the most sophisticated one that cannot be explained. A single accuracy number is never sufficient proof for any of these layers — show train/test methodology and, where the output is a probability, a calibration check (do 80%-predicted cases actually recover ~80% of the time).

---

## 8. Evaluation Methodology

- **Baselines are mandatory** for every optimization claim: a fixed-schedule baseline and a simple root-cause-rule baseline, compared against the ML/optimizer-driven treatment. The headline result is never "our model is accurate" — it is **incremental recovered ₹ versus the baseline policy, without violating compliance rules or increasing unnecessary contact attempts.**
- **Control/treatment discipline** is a first-class requirement, not an afterthought metric — it is the mechanism that makes Section 3.8 possible.
- **Every headline number shown to a merchant or a reviewer must be traceable to a live, inspectable query** — never only narrated. A number that cannot be reproduced live is indistinguishable from a fabricated one.
- **Honesty about synthetic results is permanent.** Published third-party figures (Stripe's reported recovery volumes, Adyen's Auto Rescue results, Razorpay's own current retry-engine numbers) may be shown only as labeled external context, never as a like-for-like comparison against this system's synthetic-environment results. Correct framing: *"the optimized policy produced X% higher recovery than the fixed baseline in our calibrated synthetic environment,"* never *"this recovers X% on real Razorpay data"* unless real Razorpay data is actually used.

---

## 9. Control Tower — The Demo and Product Surface

One coherent surface, not a scattering of portals. It consumes outputs from the Engine; it does not make decisions itself.

**Required views:**
- **Opportunity queue** — every open Revenue-at-Risk opportunity, ranked by Expected Recovery Value, with root cause and ₹ exposure visible at a glance.
- **Headline economics** — ₹ at risk, ₹ recoverable (ERV sum), ₹ recovered, and the number that matters most: **incremental ₹ recovered versus baseline**.
- **Live reasoning panel** — for any opportunity, shows the raw model output, the optimizer's ranked candidates, the rule engine's compliance decision, and the resulting action — simultaneously, not hidden behind a clean chat bubble. This is the primary proof mechanism that the system is genuinely reasoning, not scripted: it must survive an unscripted, adversarial input typed live (ambiguous replies, language-switching, contradicted promises, off-topic noise) and visibly show its work, including a deliberately blocked action with its logged reason.
- **Audit trail** — every decision, per opportunity, start to end, reading directly from the live data store.
- **Live-recomputing metrics** — aggregate numbers update visibly as case status changes; nothing pre-baked.

What does *not* need special proof effort: synthetic data cosmetic realism, frontend visual polish, exact simulated ₹ amounts. Effort concentrates on proving intelligence, compliance, and honesty about what caused what.

---

## 10. Explicit Scope Boundaries

**Permanently out of scope for this product** (a different problem, a different team, a different evaluation regime):
- Fraud, DDOS, and spam detection.
- Multi-PSP/alternate-gateway routing as a core rebuilt subsystem (network-health *signal* is in scope; owning routing is not).

**Deliberately deferred, not rejected — require explicit re-scoping before starting:**
- **Pre-failure prevention** (predicting failure before it happens, acting before revenue is ever at risk). Directly conflicts with the current conceptual boundary of the loop starting at "revenue is already at risk" — real, valuable, but a genuinely earlier stage of the funnel that changes what "detect" means.
- **Autonomous payment-method switching as an executed action** (vs. a scored candidate) — requires explicit action-type, execution-path, and compliance updates together, not partially.
- **Merchant-vs-peer revenue benchmarking** as a standalone product surface — valuable narrative, but adds a second measurement framework (peer cohorts) without adding much technical depth beyond what the Control Tower already shows; revisit only once the core loop's incremental-attribution numbers are proven.
- **Recovery-as-a-Service public API** — a productization decision for later, not an architecture decision now; the existing service layer can expose this without redesign when the time comes.
- **A dedicated relationship graph database or a full "digital twin" simulation platform** — the relationships and hypothetical-outcome comparisons these would provide are achievable from structured tables, rolling aggregates, and the models already in Section 7. Do not introduce new infrastructure classes (graph databases, simulation runtimes) until the structured-data version has demonstrably run out of headroom.
- **LTV-weighted optimization** (optimizing for long-term customer value instead of transaction value) — a real and eventually valuable objective-function change, deferred until the transaction-value objective is proven and trusted.

---

## 11. What Success Looks Like

The system is working when, for any merchant, at any moment, it can answer — live, from a query, not from a slide:
1. How much of this merchant's revenue is currently at risk, and why.
2. How much of that is realistically recoverable, and what it would cost to try.
3. What it did about each opportunity, and why that specific action, timing, and method.
4. What actually happened.
5. **How much of what happened would not have happened without it.**

That fifth answer is the product.
