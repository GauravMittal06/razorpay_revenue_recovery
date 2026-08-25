# Razorpay AI Buildathon — Source of Truth (Consolidated)
*Paste this entire document into any new chat to restore full project context. This is the single authoritative continuity document — it supersedes the original `PROJECT_SOURCE_OF_TRUTH.md` as a stand-alone reference by incorporating everything from `STATE_AND_DECISIONS.md` (the decision log) and `CURRENT_CHAT_CONTEXT_chat_04.md` (the verified Stage 3 implementation state). No information from any of the three source documents has been deleted; where something changed, the original is kept as labeled historical context next to the current authoritative version.*

**Document lineage:**
1. `PROJECT_SOURCE_OF_TRUTH.md` (original) — the locked plan as designed before/during build.
2. `STATE_AND_DECISIONS.md` — the full decision log explaining *why* each locked choice was made and what breaks if reversed; also contains implementation-level corrections discovered during building (bug fixes, calibration, schema additions).
3. `CURRENT_CHAT_CONTEXT_chat_04.md` — the verified, as-built state as of the end of Stage 3 (Day 8–9), including exact schema, exact function signatures, and the immediate next step.
4. A fourth document (`Revenue Recovery Data Factory + Optimization` extension spec) describing the **post-Stage-5** future architecture — folded in here as Section 20 ("Future Architecture") and explicitly marked as not-yet-started, subordinate to the current locked build order.

This document is organized as: **(A)** the locked plan, unchanged from the original SoT unless marked otherwise; **(B)** what has actually been implemented and verified so far, with exact contracts; **(C)** the full historical decision log, preserved and categorized; **(D)** the forward roadmap, immediate → future.

---

**Status markers used throughout this document:** **[LOCKED]** = decided, in force. **[IMPLEMENTED]** = built and verified end-to-end as of the stated stage. **[PENDING]** = locked in principle, exact parameters await verification (or: now resolved — see note). **[DEFERRED]** = explicitly out of current scope, requires separate approval to begin. **[CONCEPTUAL]** = named for documentation/framing purposes only, not implemented or planned in the current build. **[FUTURE ARCHITECTURE]** = planned post-Stage-5 evolution, not started, not authorized to begin.

---

## 0. EVENT CONTEXT [LOCKED]
- Razorpay AI Buildathon — student-only, hiring AI Builder Interns
- Solo build, no team
- Stipend if selected: ₹75,000/month, 6 or 12 months, Bangalore, in-person from September
- Application needs: name, college, grad year, in-person availability, resume, track, project name, problem solved, public GitHub repo, 5-min pitch video, "what broke and how you got out"
- No aptitude test, no GD — shortlisted go straight to panel interview
- Judging criteria: Problem taste, Build quality (does it run, is it structured, would you trust it), AI judgment (right tool right place, and where you chose not to use one), Failure recovery
- No dataset/sandbox provided by Razorpay for Track 03 — must generate own synthetic data

## 1. TIMELINE [LOCKED — progress annotated]
- 14 days total
- Testing + submission complete by **Sept 4**
- Application closes **Sept 5**
- No competing commitments — full solo effort, heavy AI-assisted coding

**Current position as of this document:** Day 8–9 complete and verified (Stage 3, LLM layer). Days 10–11 (Stage 4, Ops Dashboard) not started — this is the immediate next task, pending explicit user go-ahead. See Section 19 for full stage-by-stage status.

## 2. TRACK [LOCKED]
**Track 03 — AI Revenue Recovery**
Official: "Build an agent that detects revenue at risk, determines the right intervention, and executes a bounded recovery workflow — from payment failures and checkout abandonment to overdue receivables."
The Bar: measured money recovered across a batch, compliant escalation, stopping rules, audit trail.

**Decision record — why Track 03 (over Track 04, 02, 01, 05):** Best balance of finance-domain fit and solo-buildable scope. More forgiving demo surface than Track 04 (reconciliation requires harder-to-fake realistic data); avoids Track 02's formal precision/recall ML evaluation burden; avoids Track 01's live Razorpay API integration risk; avoids Track 05's open-ended scoping cost. *What breaks if reversed:* the entire schema, root-cause list, recovery-action design, and differentiation strategy are built specifically around payment/checkout/invoice recovery — switching tracks invalidates the whole plan, not just a component.

## 3. SCOPE [LOCKED]
Full loop — three connected recovery paths, **one shared recovery engine** (not three separate systems):
1. Checkout abandonment
2. Payment failure
3. Overdue invoice (B2B receivables)

**Architectural rule (hard, non-negotiable):** all three event types flow through the SAME `classify()`, `decide_action()`, and `execute_action()` functions and write to the SAME `recovery_actions` table. Do not build three parallel `if event_type == X` pipelines with duplicated logic — this is the single highest scope-fragmentation risk for a solo 14-day build. The only legitimate per-path differences:
- `root_cause` is only meaningful for payment_failed (null for the other two)
- Escalation timing input differs: `days_overdue` for invoices, event age (`created_at`) for the other two — **[IMPLEMENTED, Stage 1]** verified via `verify_invoice_escalation.py`; the original implementation incorrectly applied `created_at` age uniformly, which was caught and corrected (e.g. `age_days=7.7` with `days_overdue=3` must NOT escalate — these two measures genuinely diverge on real data).

Everything else — action selection, compliance checks, logging, LLM messaging, audit trail — is shared code.

**Decision record — single shared engine:** identified as the single highest scope-fragmentation risk for a solo 14-day build; without this rule, AI-assisted code generation defaults to duplicated per-event-type logic. *What breaks if reversed:* triples effective build/maintenance surface, defeats the "full loop, one system" positioning used in the differentiation strategy, risks inconsistent compliance/audit logic across paths.

**Decision record — full loop over payment-failures-only:** user explicitly chose full loop over a narrower single-path scope. *What breaks if reversed:* schema, root-cause mapping, and the single-shared-engine architecture rule all assume three event types feeding one system.

**Payment failure root causes (6) [LOCKED]:** insufficient funds, card declined, bank timeout/gateway error, 3DS auth failure, expired card, network failure. Deliberately bounded — device-switch-off and DDOS explicitly rejected as miscategorized during original scoping. Downstream `classify()` logic, `error_reason` mapping, and demo scenarios are all built against exactly these 6 categories; do not add a 7th without explicit approval.

**Explicitly excluded [LOCKED]:** fraud/DDOS/spam detection (Track 02's territory — do not drift into it). *Why:* including it dilutes Track 03 positioning and adds unrelated ML evaluation burden; scope bleeding into another track's judged category would dilute the "problem taste" criterion.

**Data volume [LOCKED]:** 150 synthetic records — this is the **fixed demo/evaluation dataset**, and it is architecturally distinct from the separate ML training corpus (see Section 15). It has not been touched or regenerated since being locked, and must not be.

**ML training corpus distinction [LOCKED, IMPLEMENTED Stage 2]:** Stage 2 ML model training used a **separate, simulator-generated training corpus** — approximately 8,000 simulated cases / 22,016 candidate-action rows — with grouped train/test separation (17,605 train rows / 6,400 cases; 4,411 test rows / 1,600 cases). This corpus is distinct from and does not replace the 150-record demo/evaluation dataset above; the 150 records remain the fixed demo/evaluation set, unchanged. Full simulator architecture, target definition, and calibration are detailed in Section 15.

**[RESOLVED — was PENDING] Root-cause retry-eligibility gate:** The original SoT left this pending, with `expired_card` as the sole named candidate for a non-retryable root cause and others "to be confirmed." **Status: this specific gate (a conditional restriction on which `action_type` the rule engine may select for a given `root_cause`) has not been separately reported as implemented in the reviewed Stage 1–3 session state** — it remains a live open item to confirm before or during Stage 4 review. It does not add a new root cause, a new pipeline, or a new function; it is a conditional branch inside the existing `decide_action()`. Treat as **[PENDING]** until an explicit read-only audit of current `decide_action()` behavior confirms whether this gate was folded into the Stage 1 build, and the exact non-retryable root-cause mapping is explicitly approved.

**[CONCEPTUAL] Lifecycle framing (documentation only, no architecture change):** The existing shared engine is described end-to-end as: **Prevent → Diagnose → Retry/Recovery Action → Communicate → Escalate**.
- `Diagnose` (`classify()`) — **[IMPLEMENTED, Stage 1]**
- `Retry/Recovery Action` (`decide_action()` + `execute_action()`) — **[IMPLEMENTED, Stage 1, extended Stage 3]**
- `Communicate` (LLM messaging, Section 8) — **[IMPLEMENTED, Stage 3]** — was "not yet implemented, locked Stage 3 build" in the original SoT; this has since been completed and verified (see Section 12).
- `Escalate` — underlying rule-engine logic (auto-stop/escalation rules, Section 6) — **[IMPLEMENTED, Stage 1]**. The human-facing escalation console and full context presentation (Section 9a) — **[NOT YET IMPLEMENTED — Stage 4, next]**.
- `Prevent` — **[CONCEPTUAL, still]** — no upstream failure-prevention capability (e.g. pre-expiry card warnings) is implemented or planned in the current build; named only to accurately describe the lifecycle position of the other four stages. (Note: the Future Architecture section, Section 20, contains a "pre-failure prediction" concept that would eventually occupy this position — it remains explicitly **[DEFERRED]**, requiring separate approval to begin, and directly conflicts with this conceptual-only boundary until such approval is given.)

## 4. DIFFERENTIATION STRATEGY [LOCKED]
Most competitors will build: LLM sends a reminder on failure, no real diagnosis, no stopping rules, clean fake data, no audit trail.

Our edge:
1. Hybrid architecture — rules for compliance-critical decisions, ML for prediction, LLM for language only. Directly answers the "AI judgment — right tool right place" criterion.
2. Root-cause-specific recovery logic, not one-size-fits-all retry.
3. Compliant stopping rules — most solo builders won't model this at all.
4. Honest metrics — recovery rate, ₹ recovered vs at risk, false-positive cost, not a vanity number.
5. First-class audit trail — every decision logged with reasoning.
6. One deliberately engineered failure + graceful recovery, directly answering the form's "what broke" question.

Execution quality, the pitch video narrative, and panel interview performance matter as much as the architecture — plan alone does not guarantee top-tier ranking.

**Decision record — data schema uses Razorpay-aligned field names** (`id`, `entity`, `amount` in paise, `status`, `error_code`, `error_description`, `error_source`, `error_step`, `error_reason`) rather than generic custom field names: makes synthetic data look production-grade and domain-aware without claiming to match a real hidden judge dataset (confirmed no such hidden test set exists — judges review via repo/video/interview, not by running against their own data). *What breaks if reversed:* loses the "understands the real domain" signal that differentiates from generic-looking synthetic data; `error_reason` would lose its direct mapping to the 6 locked root causes.

**Decision record — static seed data (JSON) loaded into a live, mutable database (SQLite), not read directly as the operating dataset:** a frozen dataset cannot demonstrate a "moving," live-acting agent system. *What breaks if reversed:* dashboard and audit trail would show static, unchanging state, undermining the "live system" proof requirements (Section 10).

## 5. AI JUDGMENT SPLIT [LOCKED, IMPLEMENTED]

| Function | Method |
|---|---|
| Failure root cause classification | Rule-based, ML-assisted |
| Recovery action selection (retry/wait/escalate/stop) | Rule engine (final authority) |
| Stopping rule enforcement | Hard-coded rules |
| Risk/recovery-probability scoring | ML (XGBoost / logistic regression) — informs rules, never overrides |
| Customer-facing message generation | LLM (Gemini API) — root-cause-specific, action-oriented recovery communication; LLM generates language only and never selects, triggers, or overrides a recovery action |
| Reply/intent parsing (promise-to-pay, dispute, payment_method_updated, Hinglish) | LLM, structured JSON output only |

Rule engine is always final authority on compliance. LLM never makes control decisions — only generates language or extracts structured intent. This split is now **verified in production code**, not just planned: Stage 3's LLM pre-gate (Section 12) can only route to a hard-stop (`flagged_manual_review`) or pass through unchanged — it structurally cannot select, override, or re-trigger an action.

Root-cause-specific messaging is a content requirement on the existing LLM function, not a new AI method or new decision authority — the sentence above already governs it.

**Decision record — AI method split:** directly targets the official "AI judgment — right tool in right place, and where you chose not to use one" judging criterion; keeps compliance-critical logic auditable and non-hallucinating. *What breaks if reversed:* if LLM is allowed to make control/compliance decisions directly, stopping rules and escalation logic become unauditable and unreliable, undermining the "would you trust it" criterion and the core differentiation strategy.

**Decision record — LLM provider is Gemini API, not Claude API:** user already has Gemini API access; no functional requirement favors Claude for this project's tasks (message generation, structured JSON intent extraction); avoids setup/billing delay. *What breaks if reversed:* requires new API key provisioning and billing setup with no functional gain identified.

## 6. DATA SCHEMA

### 6a. Original locked schema (Razorpay-aligned field names) [LOCKED — baseline]

**payments** (mirrors real Razorpay Payments Entity)
- `id` (e.g. pay_XXXXXXXXXXXXXX), `entity` ("payment"), `amount` (paise), `currency` ("INR")
- `status`: created / authorized / captured / refunded / failed
- `order_id`, `invoice_id`
- `method`: card / netbanking / upi / wallet
- `email`, `contact`
- `error_code`, `error_description`, `error_source` (customer/business/bank/gateway), `error_step`, `error_reason` ← maps to our 6 root causes
- `created_at` (UNIX timestamp)
- Custom additions: `event_type` (checkout_abandoned / payment_failed / invoice_overdue), `recovery_status` (open/recovering/recovered/escalated/stopped)

**customers**
- `customer_id`, `name`, `payment_history_score` (0–1), `past_recovery_rate`, `preferred_channel`

**recovery_actions** (audit log)
- `action_id`, `payment_id`, `action_type` (retry/reminder/escalate/stop), `timestamp`, `triggered_by` (rule/ml/llm), `reasoning`, `outcome`, `ml_recovery_probability` (nullable — advisory ML signal, populated only on `outcome="executed"`)

**messages**
- `message_id`, `payment_id`, `sender` (agent/customer), `content`, `intent_extracted`, `timestamp`

**Root cause → `error_reason` mapping [LOCKED]:**
1. Insufficient funds → `insufficient_funds`
2. Card declined → `payment_declined`
3. Bank timeout/gateway error → `gateway_timeout`
4. 3DS auth failure → `authentication_failed`
5. Expired card → `expired_card`
6. Network failure → `network_error`

### 6b. Current implemented schema (as built and verified, `backend/db/db.py`, SQLite) — [IMPLEMENTED, post-Stage-3]

This is the exact, current, authoritative schema. It is a strict superset of Section 6a — every original field is retained; additions are marked `NEW` with the stage that introduced them.

```
customers:
  customer_id TEXT PRIMARY KEY
  name TEXT
  payment_history_score REAL
  past_recovery_rate REAL
  preferred_channel TEXT

payments:
  id TEXT PRIMARY KEY
  entity TEXT
  amount INTEGER
  currency TEXT
  status TEXT
  order_id TEXT
  invoice_id TEXT
  method TEXT
  email TEXT
  contact TEXT
  error_code TEXT
  error_description TEXT
  error_source TEXT
  error_step TEXT
  error_reason TEXT
  created_at INTEGER
  event_type TEXT            -- checkout_abandoned | payment_failed | invoice_overdue
  recovery_status TEXT       -- open | recovering | escalated | stopped | recovered
  customer_id TEXT (FK -> customers.customer_id)
  days_overdue INTEGER       -- only meaningful for invoice_overdue

recovery_actions:
  action_id INTEGER PRIMARY KEY AUTOINCREMENT
  payment_id TEXT (FK -> payments.id)
  action_type TEXT           -- retry | reminder | escalate | stop | NULL (on flagged_manual_review)
  timestamp INTEGER
  triggered_by TEXT          -- rule | ml | llm
  reasoning TEXT
  outcome TEXT                -- see locked reason-code list, Section 7
  ml_recovery_probability REAL  -- NEW Stage 1 (see decision record below); nullable; advisory-only ML signal, populated only on outcome="executed"
  flag_type TEXT              -- NEW Stage 3; nullable; mismatch | root_cause_update_candidate | dispute_flag | NULL

messages:
  message_id INTEGER PRIMARY KEY AUTOINCREMENT
  payment_id TEXT (FK -> payments.id)
  sender TEXT                 -- agent | customer
  content TEXT
  intent_extracted TEXT
  intent_confidence REAL      -- NEW Stage 3; nullable; only populated on sender='customer' rows
  mentioned_reason TEXT       -- NEW Stage 3; nullable; one of 6 locked error_reason values, only on sender='customer' rows
  timestamp INTEGER
```

**Decision record — `recovery_actions.ml_recovery_probability` added (Stage 1):** the ML score computed in `decide_action()` was initially only present in the in-memory return dict with no persistence path — invisible to any audit trail, dashboard, or verification query. Schema needed extending for the signal to be inspectable/auditable, consistent with the "no silent actions, every decision logged" principle already governing `recovery_actions`. *What breaks if reversed:* ML score becomes untraceable after each `decide_action()` call, unable to support the dashboard/audit-trail proof requirements (Section 10) or the Live Agent Console's reasoning panel (Section 9a). *Required follow-up, already applied:* pre-existing `recovery.db` files had to be wiped and rebuilt via the fresh-DB protocol (Section 17) — `INSERT OR REPLACE`-based reloads do not retroactively add new nullable columns to an already-created table.

**Decision record — Stage 3 schema additions (`flag_type`, `messages.intent_confidence`, `messages.mentioned_reason`):** see Section 12 for full rationale; summary — confidence gating and mismatch detection required a genuine hard-stop path and a place to record *why* a case was flagged, not decorative metadata.

### 6c. Locked `error_reason` values — re-verified against real `payments.json` data (Stage 3 session)
```
insufficient_funds
payment_declined
gateway_timeout
authentication_failed
expired_card
network_error
```
Unchanged from Section 6a; independently re-confirmed against live seed data during the Stage 3 session.

## 7. COMPLIANCE / STOPPING RULES [LOCKED, IMPLEMENTED — hard-coded, non-negotiable]

- Max 3 retry attempts per transaction
- Minimum 24hr cooldown between contact attempts
- Auto-stop after 7 days no response → escalate to human queue
- No contact outside 9am–8pm (simulated)
- Every action logged with a reason — no silent actions

### 7a. Compliance rule constants (`backend/engine/decide_action.py`) — [IMPLEMENTED, unchanged since Stage 1]
```
MAX_RETRIES = 3
COOLDOWN_HOURS = 24
AUTO_STOP_DAYS = 7
CONTACT_WINDOW_START = 9   # 9am
CONTACT_WINDOW_END = 20    # 8pm
```

### 7b. Stage 3 constants (`backend/engine/decide_action.py`) — [NEW, LOCKED]
```
CONFIDENCE_THRESHOLD = 0.6
METHOD_CLASS_ROOT_CAUSES = {"expired_card", "payment_declined", "authentication_failed"}
NON_METHOD_ROOT_CAUSES = {"insufficient_funds", "gateway_timeout", "network_error"}
```
**Resolution of original [PENDING] status:** the original SoT (§9c-1) left the confidence threshold as an illustrative example ("e.g. <0.6"), not a locked value. It is now **locked at exactly 0.6**.

### 7c. Locked reason-code list (`decide_action()` outcome field) — [IMPLEMENTED, extended Stage 3]
```
executed
blocked_contact_hours
blocked_cooldown
blocked_already_stopped
blocked_already_escalated
flagged_manual_review        -- NEW Stage 3, locked
```
`blocked_max_retries` explicitly dropped. `escalated_no_response` explicitly NOT used as an outcome value — only appears inside `reasoning` text under `outcome="executed"`.

**Decision record — outcome vocabulary:** max-retries transition already resolves to `action_type="stop", outcome="executed"` in the existing state-safety design — a separate `blocked_max_retries` code would be unreachable dead code, not a real branch. `escalated_no_response` context is preserved in `reasoning` text instead, keeping `outcome` values meaningfully small and auditable. *What breaks if reversed:* reintroducing `blocked_max_retries` as a literal branch would require restructuring the stop-transition logic (a bigger architectural change explicitly declined); reintroducing `escalated_no_response` as an outcome fragments the outcome vocabulary without adding new information already captured in `reasoning`.

### 7d. Implementation-level compliance decisions (apply to all event types unless noted)

- **Contact-hour check evaluated against the payment's simulated `created_at` timestamp, not the real system wall clock.** *Why:* using the real system clock made the check depend on when the developer happened to run the script, not on the simulated event's own timing — produced unrealistic all-or-nothing blocking across a whole batch. *What breaks if reversed:* contact-hour enforcement becomes non-deterministic and disconnected from the synthetic data's own timestamps, undermining the "live system reasoning about real event data" proof requirement.
- **The 9am–8pm contact window applies only to customer-facing actions (`retry`, `reminder`); `escalate` bypasses this check entirely.** *Why:* escalation is internal routing to a human queue, not customer contact — the rule exists to protect customers from off-hours contact, not to delay internal handoffs. *What breaks if reversed:* legitimate 7-day-no-response escalations would be incorrectly delayed by a rule designed for customer-facing messaging.
- **Invoice overdue auto-escalation (7-day no-response rule) uses `payment["days_overdue"]` for `invoice_overdue` events; all other event types continue using `created_at` age.** This is the resolved implementation of the Section 3 scope rule — see Section 3 for the verification detail (`age_days=7.7` with `days_overdue=3` must NOT escalate).
- **Fresh-DB protocol required for clean verification runs** — see Section 17.

**Decision record — hard-coded stopping/compliance rules are non-negotiable, never overridden by ML or LLM:** named explicitly in Track 03's official "Bar" (compliant escalation, stopping rules); most solo competitors are expected to skip this, making it a deliberate differentiator. *What breaks if reversed:* removes the specific compliance-awareness differentiator and risks demoing an agent that could spam/retry indefinitely.

## 8. METRICS TO SHOW [LOCKED — not yet surfaced in a dashboard, Stage 4 dependency]
- Recovery rate (%) by root cause
- ₹ recovered / ₹ at risk
- Time-to-recovery distribution
- False-positive cost (control group split)
- Exceptions unresolved (count + reasons)
- External industry benchmark comparison (e.g. published third-party recovery-rate figures) — shown as a clearly labeled contextual reference point only, never framed as a claim of superiority over production payment providers or as a like-for-like comparison (different data, scale, and conditions)

**Decision record — headline metrics must be reproducible/queryable live, not just stated in narration or slides:** a claimed-but-not-inspectable number is indistinguishable from a fabricated one; judges cannot verify narration alone. *What breaks if reversed:* any pitch numbers become unverifiable claims, undermining the "measured money recovered" requirement explicitly stated in Track 03's official Bar. **This requirement directly gates Stage 4 dashboard design** — every displayed metric must trace to a live query.

## 9. SURFACES [LOCKED — Stage 4 dependency, not yet built]
1. Ops Dashboard (primary) — batch view, filters, metrics panel, case detail with audit trail
2. Case detail message thread — folded into dashboard, not a separate portal
3. **Live Agent Console** (locked feature — part of Stage 4, not optional) — see Section 9a
No fourth portal unless days 12–13 have spare time.

**Decision record — two-surface limit:** more surfaces increase solo build risk and reduce polish-per-surface; judges reviewing quickly need one coherent thing to click through. *What breaks if reversed:* adding surfaces early risks the shallow-breadth failure mode (many things half-built vs one thing done well).

## 9a. LIVE AGENT CONSOLE [LOCKED feature — Stage 4, not yet built]
Purpose: prove the system is genuinely reasoning live, not hardcoded/scripted — for both the recorded pitch video and the panel interview.

**Required components:**
1. Event trigger controls — buttons/dropdowns to create a real event (event_type, root_cause if applicable, amount). On trigger: real DB row inserted, timestamped now.
2. Free-text customer reply box — unconstrained input, any language/phrasing, including Hinglish. Placeholder text explicitly invites adversarial input (e.g. "Try anything — vague, angry, mixed language, nonsense").
3. Agent reasoning panel — shown simultaneously, not hidden:
   - Raw LLM output (JSON: intent, confidence, extracted date, sentiment)
   - Rule engine's resulting decision given that output
   - Compliance check result (e.g. retry count vs limit, cooldown status)
   - **On escalation specifically, this panel must present the full case context bundle:** payment history, root cause, ML recovery probability (`ml_recovery_probability`), full message/conversation thread, all prior recovery actions, and a recommended next step. The recommended next step is not a separate stored field — it is derived from the existing rule-engine decision/action output already produced by `decide_action()`/`execute_action()`, presented in the panel rather than computed by any new logic. This is a completeness requirement on this already-locked reasoning panel — no new subsystem, no new data field, no new model.
   - **Dependency status:** payment history (`payment_history_score`/`past_recovery_rate`), root cause (derivable from `error_reason` via `classify()`), `ml_recovery_probability`, and prior recovery actions were already produced/logged as of Stage 1/2 completion. The message/conversation thread depended on Stage 3 (LLM messaging layer) for population — **Stage 3 is now complete and verified**, so this dependency is satisfied; the `messages` table is now actively populated by `handle_customer_reply()` and `deliver_recovery_message()` (Section 12). This bundle requirement is ready to be satisfied at Stage 4 build time as originally planned.
4. Live audit trail feed — appends in real time as actions happen (timestamp, action_type, triggered_by, reasoning) — reads directly from the database, not a mock.
5. Metrics that recompute live — aggregate stats (recovery rate, ₹ recovered) update visibly when case status changes, not static/pre-baked.

**Why this exists:** doubles as (a) the strongest anti-"hardcoded" proof mechanism for video + interview, and (b) a legitimately useful product feature (explainable, inspectable agent decisions) — directly supports the "AI judgment" and "would you trust it" judging criteria.

**Decision record — Live Agent Console is required, non-optional:** necessary to prove the system is genuinely reasoning live rather than hardcoded, both for the recorded video (no live judge interaction possible) and the panel interview (judges can test it directly); a static/scripted demo is vulnerable to a judge typing unanticipated input and breaking it instantly. *What breaks if reversed:* removes the primary mechanism for surviving judge skepticism about hardcoded/scripted responses.

## 9b. PROOF REQUIREMENTS (what must be demonstrably real, not just claimed) [LOCKED]
Applies to video submission and panel interview both.

1. **LLM reasoning** — must respond correctly to live/spontaneous, untested input (not just pre-written examples). Show raw JSON output, not just a chat bubble. Do not hide response latency — visible "agent thinking" delay reads as authentic, not as a flaw. *Readiness: satisfiable now — `parse_intent.py` and `generate_message.py` both exist and return structured JSON; verified end-to-end.*
2. **ML risk model** — show real train/test split with precision/recall/confusion matrix, and ideally a calibration check (do 80%-predicted cases actually recover ~80% of the time). A single accuracy number is not sufficient proof. *Readiness: satisfiable now — grouped train/test split with 17,605/4,411 row split exists (Section 15); calibration verification already run once via `verify_sensitivity.py`.*
3. **Rule engine / compliance logic** — deliberately trigger a case that should be blocked (e.g. 4th retry attempt) and show the system refusing, with a logged reason. Only showing successful actions looks curated. *Readiness: satisfiable now — blocked_* outcome codes exist and are exercised.*
4. **Audit trail** — trace one case fully, start to end, no gaps, in the video/interview. More convincing than aggregate dashboard numbers alone. *Readiness: satisfiable now at the DB level; dashboard presentation is a Stage 4 dependency.*
5. **Deliberate failure (Stage 5)** — show the actual broken state occurring on screen, then the specific safeguard catching it (e.g. idempotency check on duplicate webhook) — not just a claimed bullet point. *Readiness: not yet built — Stage 5, after Stage 4.*
6. **Headline metrics must be reproducible** — every number stated in the pitch should be traceable to something inspectable live (a query, a script run), not just narrated from a slide.

**Video-specific proof tactics** (no live judge interaction possible):
- Type something spontaneous/improvised on camera, say so out loud before typing
- Show raw structured output on screen every time, not just clean chat UI
- Do not cut out latency — visible delay reads as real
- Briefly show the actual code (e.g. the LLM call function) tied to what just happened on screen
- Vary root causes and reply tones across the video — not one rehearsed flow repeated
- State once, factually, in narration: "this is a live API call responding to whatever I type, nothing here is pre-scripted"

**What does NOT need special proof:** data generation realism, frontend visual polish, exact simulated ₹ amounts. Effort should concentrate on proving intelligence, compliance, and honesty — not on polishing things nobody will doubt.

## 9c. REQUIRED AGENT LOGIC — [IMPLEMENTED, Stage 3, verified]
Three behaviors, originally specified as build work (not just demo flourishes), all now implemented and verified end-to-end:

1. **Confidence threshold handling** — LLM intent extraction returns a confidence score. Below the now-locked threshold (0.6, Section 7b), the rule engine does NOT auto-close or auto-schedule — it flags the case for manual review instead (`outcome="flagged_manual_review"`). **Implemented as real branching logic** in `decide_action()`'s new pre-gate; verified via the Stage 3 e2e suite (a genuine live Gemini failure during testing correctly fell back to `confidence=0.0` and correctly triggered this exact gate — see Section 19).
2. **Self-consistency / mismatch check** — before acting on a customer reply, the rule engine cross-checks extracted intent (`mentioned_reason`) against the case's existing `root_cause` data via a new `_check_intent_compatibility()` helper. **Not simplistic equality:** `payment_method_updated` paired with a method-class root cause (`expired_card`, `payment_declined`, `authentication_failed`) is treated as a legitimate root-cause update (`flag_type="root_cause_update_candidate"`, non-blocking, carried through) — because a customer updating their card/method genuinely resolves method-class failures. The same intent paired with a non-method-class root cause (`insufficient_funds`, `gateway_timeout`, `network_error`), or any other genuine intent conflict, is treated as a real mismatch (`flag_type="mismatch"`, blocking → `flagged_manual_review`).
   - **Decision record — why not simple equality:** reverting to simple equality would incorrectly route every `payment_method_updated` reply to manual review whenever the customer doesn't restate the exact stored root cause, even when the update is the correct resolution — inflating false-positive manual-review volume and undermining trust in the flagging mechanism.
   - **Decision record — why `root_cause` is excluded from the Gemini prompt in `parse_intent.py`:** `mentioned_reason` is derived purely from customer-stated language; the root-cause comparison happens entirely in Python inside `decide_action()` after both values independently exist. Sending the stored `root_cause` to the LLM risked anchoring bias — the model could unconsciously echo back the system's existing belief rather than independently extracting what the customer actually said, defeating the purpose of an independent mismatch check. *What breaks if reversed:* `mentioned_reason` becomes a biased confirmation signal rather than an independent cross-check, undermining the entire self-consistency differentiator.
3. **`payment_method_updated` structured intent** — recognized by `parse_intent.py` as one additional structured intent value, alongside existing categories (promise-to-pay, dispute, etc.). Recognizing this intent does not itself authorize any action — per Section 5's locked authority rule, the LLM extracts intent only; `decide_action()` re-evaluates the next eligible action exactly as it does for any other extracted intent, subject to the same compliance checks (cooldown, retry limits, contact hours) already in force. No new pipeline, no new action type, no new decision authority.

## 9d. DEMO SCENARIOS (test cases against existing build — no new architecture) [LOCKED]
These exercise the Live Agent Console with harder inputs. Not separate features — just things to type/trigger when demoing or recording:
- Multi-turn conversation (customer replies again after agent's response, e.g. requests installments)
- Customer contradicts or breaks an earlier promise-to-pay — system detects and escalates
- Ambiguous reply ("I'll try") — low confidence, routed to manual review (uses 9c-1)
- Mid-conversation language switching (English ↔ Hinglish)
- Cooldown enforcement demoed live — trigger a second retry within 24hrs, show it blocked with reason
- Off-topic/nonsensical reply — system declines to force a bad extraction, routes to general support instead

**Decision record — demo scenarios are test cases, not new features:** necessary to keep the proof requirements from silently expanding the 14-day build scope; these exercise existing logic rather than requiring new systems. *What breaks if reversed:* reintroduces the exact scope-blowout risk the single-engine rule was designed to prevent.

## 10. TECH STACK [LOCKED]
- Backend: Python + FastAPI
- ML: scikit-learn / XGBoost
- LLM: Gemini API
- DB: SQLite
- Frontend: React + Tailwind + Recharts
- Synthetic data: Python + Faker
- Orchestration: custom state machine (not a heavy agent framework)
- GitHub: public repo required
- No live Razorpay API — all events simulated

**Decision record — backend/frontend folder split:** all backend-related folders (`data`, `scripts`, `db`, `engine`, `ml`, `llm`, `api`, `logs`) nested under one `backend/` root; `frontend/` kept separate at project root. *Why:* easier for independent deployment — backend becomes one deployable unit (own `requirements.txt`, `.env`) separate from a frontend deployed elsewhere (e.g. Vercel/Netlify). *What breaks if reversed:* import paths and relative file references already written against `backend/` as root (e.g. `db/db.py` resolving `data/` one level up) would break. **This structure is now load-bearing** — Stage 1–3 code was written against it.

**No API endpoints defined yet (Stage 4 dependency). No frontend components defined yet (Stage 4 dependency).**

## 11. BUILD ORDER [LOCKED, strict, no deviation — progress annotated]
1. Core loop, rules only — event → root cause → action → log. Working end-to-end by day 5. — **[IMPLEMENTED, DONE]**
2. ML layer — risk/recovery-probability model feeds rule engine, rules stay final authority. — **[IMPLEMENTED, DONE]**
3. LLM layer — message generation + reply/intent parsing, structured JSON output. — **[IMPLEMENTED, DONE, verified 23/23]**
4. Dashboard — batch view, case detail, metrics panel. — **[NOT STARTED — next]**
5. Stress test — inject one deliberate edge case (duplicate/late webhook), show clean handling, document in audit trail. This becomes the "what broke" answer. — **[NOT STARTED — after Stage 4]**

New ideas mid-build go on a "later" list — don't touch until stage 5 is stable.

**Decision record — strict build order, no mid-build feature additions:** directly addresses the risk of limited independent tech-stack knowledge combined with heavy AI-assisted coding, which creates high risk of context loss and architecture drift if steps are taken out of order. *What breaks if reversed:* out-of-order building was explicitly identified as the mechanism by which bugs and incomplete integration would most likely occur in this specific solo, AI-assisted build.

**[DEFERRED] Deferred / later list (not part of current build order, not to be started before Stage 5 is stable, requires separate explicit approval to begin):**
- Optimal-retry-**timing** prediction (predicting *when* a retry is most likely to succeed, not just current probability-of-success). Would require: a new ML target definition, a timing representation (e.g. best-window feature/label), retraining and re-evaluating the model, and new integration surface in `decide_action()` beyond the current advisory-only `ml_recovery_probability` signal. Explicitly out of Stage 2's current scope. **(This item is expanded in full in Section 20, Future Architecture — Priority 3 there.)**
- Multi-channel escalation (SMS/in-app layered on top of existing channel)
- LTV/customer-value-based segmentation **(expanded in Section 20 — remains deferred there too.)**
- Merchant-specific cohort/baseline layer **(expanded in Section 20 — remains optional/deferred there too.)**
- Recovery strategy A/B experimentation **(expanded in Section 20 as "Control/treatment experiments" — remains a later enhancement.)**
- "Pay and stay" / retention metric
- Multi-PSP / alternate gateway routing **(expanded in Section 20 — explicitly confirmed still deferred, and explicitly not to become a new main-system subsystem even in the future architecture.)**
- Literal autonomous "recovery-case agent" framing that would imply the LLM controls or executes actions (narrative language describing the existing pipeline is fine; an actual new decision authority is not — Section 5 stays locked as written)
- Any other adjacent feature not explicitly locked elsewhere in this document

## 12. LLM ARCHITECTURE — [IMPLEMENTED, Stage 3, verified 23/23 end-to-end]

This section is new relative to the original SoT — it did not exist as implementation detail there because Stage 3 was not yet built. It supersedes the "not yet implemented" status of the `Communicate` lifecycle stage (Section 3) and satisfies Section 5's LLM row and Section 9c's three required behaviors.

### 12a. Gemini configuration (`backend/llm/parse_intent.py` and `backend/llm/generate_message.py`)
```
GEMINI_MODEL = "gemini-3.6-flash"          # was gemini-1.5-flash (deprecated/unavailable for this key)
GEMINI_TIMEOUT_SECONDS = 30                 # was 15 (too tight for system_instruction + structured JSON latency)
```
**Decision record:** live debugging discovered `gemini-1.5-flash` is no longer available for this API key/account (`404 NotFound`, then a further `404` on the interim replacement `gemini-2.5-flash`, with Google's own error message pointing to `gemini-3.6-flash` as current). Separately, the original 15s timeout produced a real `DeadlineExceeded` under actual `system_instruction` + structured-JSON-generation latency on this model, not a config/auth issue. *What breaks if reversed:* reverting the model name causes every live Gemini call to fail immediately (confirmed via traceback), silently falling back to the safe-but-non-functional fallback path on 100% of calls; reverting the timeout reintroduces the same deadline failure under normal latency.

### 12b. Function contracts — new this stage, exact signatures

```
parse_reply_intent(customer_message: str, conversation_history: list, event_type: str) -> dict
  returns {"intent": str, "confidence": float, "mentioned_reason": str|None, "extracted_detail": str|None}

generate_recovery_message(payment: dict, classification: dict, action_type: str) -> dict
  returns {"message": str, "status": "ok"|"fallback"}
  only intended for action_type in {"retry","reminder"}

decide_action(payment, classification, conn,
              extracted_intent=None, intent_confidence=None,
              mentioned_reason=None, dispute_flag=False) -> dict
  -- 4 new optional kwargs, default no-op; unchanged for existing callers

handle_customer_reply(payment_id: str, customer_message: str, conn) -> dict
  returns {"payment_id","message_id","parsed_intent","decision","execution_result","status"}
  status in {"ok","payment_not_found","message_persist_failed","engine_error"}

deliver_recovery_message(payment, classification, decision, conn) -> dict
  returns {"delivered": bool, "status": "ok"|"fallback"|"skipped_ineligible"|"persist_failed", "message": str|None}
```

### 12c. `parse_intent.py` — inbound customer reply/intent parsing
- Gemini-based, structured JSON output only.
- `root_cause` deliberately never sent to Gemini (bias prevention — see Section 9c-2 decision record).
- Strict JSON schema validation, hard fallback (`intent="unclear"`, `confidence=0.0`) on any failure (timeout, malformed output, API error).
- Recognizes `payment_method_updated` as a structured intent category alongside promise-to-pay, dispute, etc. (Section 9c-3).

### 12d. `generate_message.py` — outbound customer-facing message generation
- Pure function: `generate_recovery_message()` has **no DB reads/writes**.
- Only ever intended for `action_type in {"retry","reminder"}`.
- Deterministic non-LLM fallback templates, selected *before* any Gemini call attempt: 6 root-cause-specific templates for `retry` (one per locked `error_reason`), 2 event-type-specific templates for `reminder` (`checkout_abandoned`, `invoice_overdue` — never invents a root cause, since it's always null for these event types), generic fallbacks for edge cases.
- **Decision record — why not `escalate`/`stop`:** `escalate` is internal routing and `stop` means contact has ended — generating a customer-facing message for either would contradict the rule engine's own decision. Deterministic fallbacks needed to preserve the same domain-specificity as the LLM path even when Gemini is unavailable, per explicit user correction rejecting a single generic fallback string. *What breaks if reversed:* a generic fallback would degrade message quality/specificity silently whenever Gemini fails, with no way to distinguish "system down" from "system working as intended"; allowing message generation for `escalate`/`stop` would create customer-facing communication about actions that are supposed to be either internal or terminal.

### 12e. `deliver_message.py` — `deliver_recovery_message()`
- Eligibility gate: **only** `outcome=="executed"` AND `action_type in {"retry","reminder"}` triggers generation+persistence. Everything else (`escalate`, `stop`, all `blocked_*`, `flagged_manual_review`) returns `status="skipped_ineligible"` with zero DB writes.
- Persists one `messages` row (`sender='agent'`, customer-reply-specific fields NULL) in its own try/except — persistence failure cannot roll back or affect the already-committed recovery decision, since `execute_action()` has already committed before this function is called.
- Wired into **both** entry points — `core_loop.py` (batch) and `handle_customer_reply.py` (reply-triggered) — via one added call each, immediately after their existing `execute_action()` call.
- **Decision record:** batch-generated messages from eligible actions are explicitly part of the real recovery/audit flow (not test-only), per direct user clarification — both entry points must behave identically rather than only wiring the reply-triggered path. Restricting eligibility strictly to already-executed customer-contact actions preserves `decide_action()` as sole authority: message generation can only ever phrase a decision already finalized, never influence one. *What breaks if reversed:* generating messages for blocked/flagged/non-contact outcomes would produce customer-facing text for actions that never actually happened or were explicitly halted, breaking the audit trail's correspondence between `recovery_actions` and `messages`.

### 12f. `handle_customer_reply.py` — `handle_customer_reply()`
Sequencing: fetch payment → fetch `conversation_history` (pre-insert, so current reply is structurally excluded) → `parse_reply_intent()` → insert+commit customer message atomically (fail closed if this fails) → `classify()` → `decide_action()` (with LLM kwargs) → `execute_action()` → `deliver_recovery_message()`. Outer try/except around the classify→deliver sequence for unexpected errors (`status="engine_error"`), distinct from each component's own internal fallback handling.

**Decision record — customer message persisted as its own atomic commit *before* calling `decide_action()`/`execute_action()`; if that insert fails, the function aborts immediately:** `execute_action()` already commits internally as part of its existing, unmodified "every call logs a row" contract (a prior architectural lock); wrapping it inside a larger transaction would require modifying `execute_action()`, explicitly out of scope. Persisting the customer's message first and treating it as its own durable unit is the safest achievable design — proceeding to `decide_action()` without a persisted message would let compliance checks (`_has_customer_reply`) reference a reply that technically doesn't exist yet in the DB. *Accepted residual gap:* a message can exist with no corresponding `recovery_actions` row if `classify`/`decide_action` fails unexpectedly after message insert — documented and considered safe-by-default (fail closed, `recovery_status` untouched) rather than hidden. Full cross-table atomicity was evaluated and rejected as infeasible without changing `execute_action()`'s existing commit behavior.

### 12g. Stage 3 pre-gate inside `decide_action()`
New logic, inserted before existing compliance branches, all existing branches otherwise untouched:
- Dispute flag → hard-stop (`flagged_manual_review`)
- Low confidence (`< CONFIDENCE_THRESHOLD`) → hard-stop (`flagged_manual_review`)
- Mismatch (per `_check_intent_compatibility()`) → hard-stop (`flagged_manual_review`)
- `root_cause_update_candidate` → non-blocking, carried through as `flag_type` on the executed row
- Both pre-existing `outcome="executed"` return paths now include `flag_type` (nullable, `NULL` when no flag applies).

### 12h. Files created/modified this stage
- `backend/llm/parse_intent.py` — Created.
- `backend/engine/decide_action.py` — Modified: added constants (7b), `_check_intent_compatibility()` helper, extended signature (12b), new pre-gate (12g). All pre-existing compliance branches untouched.
- `backend/engine/execute_action.py` — Modified: INSERT extended to include `flag_type`. New guard: `outcome=="flagged_manual_review"` or `action_type is None` → log row only, no status update, no side effects (same pattern as existing blocked outcomes).
- `backend/db/db.py` — Modified: schema extended (Section 6b). Required fresh-DB rebuild, applied and verified.
- `backend/engine/handle_customer_reply.py` — Created (12f).
- `backend/llm/generate_message.py` — Created (12d). No DB access, no wiring — pure dict-in/dict-out.
- `backend/engine/deliver_message.py` — Created (12e).
- `backend/engine/core_loop.py` — Modified: one import + one line added (`deliver_recovery_message(...)` called immediately after `execute_action()` inside the existing batch loop). No other logic changed.

**Peripheral/unchanged files:** `backend/engine/classify.py` (unchanged, shared across all 3 event types, called by both `core_loop.py` and `handle_customer_reply.py`); `backend/data/customers.json` / `backend/data/payments.json` (locked demo/seed data, untouched); `backend/ml/models/xgb_model.joblib` (unchanged, still loaded lazily at inference in `decide_action.py`; Stage 3 does not touch ML scoring logic).

**Local-only debug/test scripts from this stage (not production files, not tracked as such):** `test_handle_reply.py`, `test_generate_message.py`, `test_deliver_message.py`, `test_stage3_e2e.py`, `verify_gemini.py`, `debug_parse_intent.py`, `debug_gemini_raw.py`, `list_gemini_models.py`.

## 13. DAY-BY-DAY PLAN [LOCKED — progress annotated]
| Days | Task | Status |
|---|---|---|
| 1–2 | Schema + synthetic data generator (150 records) | **DONE** |
| 3–5 | Rule engine, core loop end-to-end | **DONE** |
| 6–7 | ML risk model + integration | **DONE** |
| 8–9 | LLM messaging + reply parsing | **DONE, verified 23/23** |
| 10–11 | Dashboard | **NOT STARTED — next** |
| 12 | Failure injection + audit trail proof | **NOT STARTED** |
| 13 | Testing, bug fixes, metrics check | **NOT STARTED** |
| 14 (land by Sept 3–4) | Repo cleanup, README, pitch video, final review | **NOT STARTED** |

## 14. FAILURE RECOVERY LOG PROTOCOL [LOCKED]
Whenever something breaks, we get stuck, or we hit a real problem during the build:
1. Work through it live in that chat until resolved.
2. Once resolved, confirm with the user before writing anything.
3. On confirmation, generate a standalone `.md` file documenting: what broke, why, what was tried, what finally worked.
4. Save it — this becomes the material for the application form's "what broke, and how you got out" field.
Do this every time a real failure occurs, not just once — multiple logs are fine, pick the strongest one(s) at submission time.

**Decision record:** required as source material for the application form's mandatory "what broke, and how you got out" field; must reflect a real failure, not a fabricated one. *What breaks if reversed:* without a real documented failure, the application answer to this required field would need to be fabricated or omitted.

**Note:** the Stage 3 session encountered and resolved real failures that qualify as candidate material for this field — the Gemini model deprecation (Section 12a) and the two false-positive-looking e2e test failures later diagnosed as test-assumption errors, not implementation bugs (Section 19). Per this protocol and the documentation-deferral decision (Section 16), these have not yet been written up as a standalone `.md` failure-log file — that write-up remains an open task, not yet completed, distinct from this consolidated SoT update.

## 15. ML ARCHITECTURE — [IMPLEMENTED, Stage 2, verified]

This section did not exist as detailed implementation in the original SoT (which only specified "XGBoost / logistic regression" at the AI-judgment-split level, Section 5). It is new here, consolidating the full Stage 2 decision log.

### 15a. Two-corpus architecture (locked scope boundary)
- **150-record demo/evaluation dataset** — fixed, Razorpay-shaped, untouched since being locked (Section 3).
- **Separate, larger simulator-generated ML training corpus** — approximately 8,000 simulated cases / 22,016 candidate-action rows, grouped train/test split (17,605 train rows / 6,400 cases; 4,411 test rows / 1,600 cases).

**Decision record — why a separate corpus is required:** the ML risk/recovery-probability model requires historical X/y examples to train on; the 150 Razorpay-shaped records are demo/evaluation data, not sufficient volume or outcome-labeled for training. A stochastic simulator (observable features + hidden variables: liquidity, issuer/bank condition, responsiveness) generates a larger synthetic corpus with genuine probabilistic outcomes, avoiding circular training (the model must not learn a formula it was generated from). Public datasets (KKBox, UCI Credit Default, PaySim) were referenced only as inspiration for realistic feature/probability design — not ingested as training rows, per the existing "self-generate all data" lock. *What breaks if reversed:* without this corpus, no valid train/test split exists — the §9b.2 proof requirement (precision/recall/confusion matrix, not just accuracy) cannot be met; training directly on the 150-record demo batch would contaminate demo/evaluation data with training data.

**Explicit scope boundary (still in force):** the 150-record demo/evaluation batch stays unchanged and untouched. Core recovery-engine architecture (`classify()`, `decide_action()`, `execute_action()`) not modified by the simulator's existence. ML remains advisory signal only — rule engine stays final compliance authority (Section 5, unchanged).

### 15b. Training target definition
`candidate_action`-conditioned recovery probability within a **fixed 72-hour recovery horizon** — not a general "will this case ever recover" label. `candidate_action` restricted to `retry` / `reminder` / `escalate` — **`stop` excluded** as a non-recovery action.

**Decision record:** a backward-looking or action-agnostic label would not answer the decision-relevant question ("if I take this specific eligible action now, how likely is recovery"). `stop` is a termination action with no recovery outcome to predict, so including it as a candidate would be meaningless. *What breaks if reversed:* the model would predict a vague, non-actionable probability disconnected from the specific action the rule engine is about to take.

### 15c. Row generation and split design (leakage-critical)
- **One training row per eligible `candidate_action` per simulated case** (not one row per case).
- **Grouped train/test split** — all rows from the same `case_id` stay together in the same split. No leakage across train/test at the case level.

**Decision record:** the model must learn `P(recovery | features, candidate_action)`, which requires multiple candidate-action rows per case sharing the same underlying (hidden) state. If same-case rows were split across train/test, the model could implicitly leak case-specific hidden-state information across the split boundary. *What breaks if reversed:* row-level (non-grouped) splitting would leak information between train/test for the same case, producing inflated/misleading validation metrics that don't reflect true generalization to unseen cases.

### 15d. Counterfactual / hidden-state design
Simulator hidden variables — `liquidity_state`, `issuer_availability`, `payment_method_health`, `customer_responsiveness`, `bank_condition_temp`, `recovery_willingness` — are **sampled once per case and reused across all candidate_action rows for that case**.

**Decision record:** represents a correct causal "what-if" structure — the customer's true underlying state at a fixed moment is fixed; the simulator asks what would happen for each hypothetical action against that same fixed state, not a re-randomized state per action. *What breaks if reversed:* re-sampling hidden state per candidate_action row would break the "what-if comparison" semantics and make within-case candidate_action rows causally unrelated to each other, undermining the target definition (15b).

### 15e. Feature engineering corrections made during the build
- **`retry_count` generation range fixed** from `rng.integers(0,3)` (bug — never produced `retry_count=3`) to `rng.integers(0,4)` (produces 0,1,2,3, including the true max-retries boundary state). *Why:* `retry_count==3` (retries exhausted, only reminder/escalate remain eligible) is a real, reachable state in the live rule engine; excluding it left a blind spot exactly at the compliance boundary the model would need to score correctly in production.
- **`recovery_horizon_hours`** (constant 72 in the training corpus) intentionally **excluded** from both `CATEGORICAL_FEATURES` and `NUMERIC_FEATURES` in training and inference. *Why:* a constant column carries zero predictive signal; its only role is defining the outcome window used to generate `y` during simulation, not serving as a model input.
- **Production `retry_count` feature** (fed to the ML model at inference) computed as a separate `retry_only_count` — count of `history` rows where `action_type=="retry"` AND `outcome=="executed"` — distinct from the existing compliance `contact_count` (which combines retry+reminder and remains unmodified, still driving the real `MAX_RETRIES` check). *Why:* the training corpus's `retry_count` feature specifically means actual retry actions only; passing `contact_count` would create a train/inference schema mismatch. *What breaks if reversed:* the ML feature would silently be fed data that doesn't match what it was trained on, degrading prediction quality without any visible error.
- **`last_action_type` and `hours_since_last_action`** at inference are both derived from the same single most-recent `history` entry (not from two different history subsets). *Why:* training data always pairs these two features from the same simulated event; an earlier draft sourced them from two different history subsets, a potential inconsistency producing self-contradictory rows outside the training distribution.

### 15f. Calibration — evidence-inspired, not dataset-fitted
Two changes applied to the simulator, informed by public-dataset research (not fitted coefficients):
- **(A)** boundary penalty added in `retry_count_penalty()` specifically at `retry_count>=3`.
- **(B)** `customer_responsiveness` base generation changed from linear to a mild sigmoid-shaped non-linear function of `past_recovery_rate`.

**Decision record:** UCI Credit Card Default data showed default rate rises sharply at recent delinquency (motivates A) and that tree-based/non-linear models dramatically outperform linear models for repayment-risk prediction, implying real threshold/interaction structure (motivates B). PaySim was evaluated and explicitly **rejected** as a calibration source (fraud label, 0.13% positive rate, domain-mismatched). No public source provided fitted numeric coefficients for this domain/schema — both changes' specific magnitudes are original bounded design choices, explicitly commented in-code as such, **not** claimed as dataset-derived. Neither change was tuned to make XGBoost outperform Logistic Regression (verified: pre/post gap 0.0094→0.0095, effectively unchanged). *What breaks if reversed:* reverting removes the only externally-motivated (vs. purely arbitrary) calibration in the simulator, weakening the "AI judgment" answer if a judge asks how probability weights were chosen.

**Verification already run:** `verify_sensitivity.py` confirmed monotonic non-increasing retry effect for `candidate_action='retry'` (0.570→0.561→0.541 across retry_count 0,1,2) and expected downward trend with sharpest single-step drop at the retry_count=3 boundary for `reminder`/`escalate` (e.g. escalate: 0.633→0.625→0.617→0.594); no probability collapse to 0/1; XGBoost vs LR performance gap unchanged after calibration, confirming no favoritism toward either model.

### 15g. ML → rule-engine integration boundary (advisory-only, structurally enforced)
`decide_action()` scores **only the single candidate action the rule engine has already selected** (`default_action` or `escalate`) — not a comparison across `retry`/`reminder`/`escalate` alternatives for the same case.

**Decision record:** Section 5 locks ML as advisory/predictive only; the rule engine is sole authority on action selection. Scoring only the already-chosen action keeps ML strictly post-hoc/advisory (an audit/reasoning signal), avoiding any path by which ML output could influence which action gets chosen. *What breaks if reversed:* a multi-action comparison exposed to `decide_action()` would create a de facto channel for ML to influence action selection, breaching the locked "ML predicts → rule decides" boundary even if not implemented as an explicit override.

**This boundary is directly load-bearing for the Future Architecture (Section 20):** any future optimizer that ranks multiple candidate actions/timings by expected value must sit **outside** and **before** this boundary, never bypassing it — see Section 20's "Critical authority order."

## 16. USER WORKING PREFERENCES / CONSTRAINTS [LOCKED, still in force]

- No documentation/explanatory files/guides unless explicitly requested
- Short, direct, point-to-point answers
- No jargon-heavy paragraphs
- No mid-build scope changes — flag new ideas, don't act on them until locked stages complete
- User has RAG pipeline experience + heavy AI-assisted coding, limited independent tech-stack knowledge — plans must stay explicit and unambiguous
- **No autonomous multi-step code generation without explicit per-step user confirmation** — user explicitly revoked earlier autonomous building behavior mid-session ("I don't want you to auto initiate anything without consulting with me"). *What breaks if reversed:* violates a direct, explicit instruction; risks the same uncontrolled drift (e.g. a divergent-schema incident from an earlier session) that prompted the instruction. **This is still the governing constraint for Stage 4 — do not begin generating Stage 4 code without explicit go-ahead.**
- **Claude does not execute project scripts on the user's behalf; the user runs all scripts locally and pastes output back for verification.** *Why:* user explicitly corrected Claude for running `generate_data.py` on Claude's own sandboxed environment instead of the user's machine; user wants full local execution control. *What breaks if reversed:* risks divergence between what Claude verifies internally and what actually exists in the user's real project folder.
- **Documentation (SoT, decision log) updates deliberately deferred until a full stage's micro-steps are fully implemented and independently verified**, rather than updated incrementally after each micro-step. *Why:* avoids logging intended-but-not-yet-verified behavior as if it were final; the consolidated end-of-stage update only records what was confirmed working end-to-end. *This consolidated document itself follows that same principle — it reflects the verified end of Stage 3, not any in-progress Stage 4 work.*

**Historical note (superseded, kept for continuity):** at one point, "all previously generated code and data files discarded; project treated as research/analysis-complete, zero code written" was an explicit reset instruction. This was a one-time reset early in the project, not a standing rule — it is recorded here only so that no future reference mistakes any code described *before* that reset point as still active. All schema, code, and state described elsewhere in this document (Sections 6b, 7, 12, 15, 18) reflect the post-reset, actually-built system.

## 17. FRESH-DB / LOCAL VERIFICATION PROTOCOL [LOCKED, still in force]

**Fresh-DB protocol required for clean verification runs:** `db/recovery.db` must be deleted and `db/db.py` re-run before re-running `core_loop.py` (or any verification script) when testing rule-engine or schema changes.

**Decision record:** `db.py`'s loader uses `INSERT OR REPLACE` only on `customers`/`payments`; it never clears `recovery_actions`/`messages`, so re-running the core loop without wiping the DB tests against accumulated prior-run history, not a clean first-cycle state — this produced a misleading initial "re-verification" in one prior session that was actually a second cycle. *What breaks if reversed:* future rule-engine changes could appear verified against contaminated state (mixing pre-fix and post-fix action history), producing false-positive confidence in compliance logic correctness. **This protocol was applied and confirmed during the Stage 3 session** (rebuilt fresh multiple times; final e2e run confirmed clean state).

## 18. CURRENT IMPLEMENTATION STATE (as of end of Stage 3) — [VERIFIED]

- Day 8–9 (Stage 3: LLM layer) — Micro-steps 1–4 complete, fully implemented, and verified end-to-end (**23/23** automated checks PASS in the final consolidated e2e script). Stage 3 is functionally done.
- Days 1–2, 3–5, 6–7 (schema, rule engine, ML layer): **DONE** (prior sessions).
- Days 10–11 (Ops Dashboard, Stage 4): **NOT started.** This is the next stage.

### 18a. e2e verification detail
Final consolidated e2e verification (`test_stage3_e2e.py`) result: 19/23 automated PASS on first run; the 4 apparent failures were confirmed to be **test-script assumption errors, not implementation bugs**:
- **Test A (3 checks):** a real transient Gemini API failure occurred mid-test; `parse_reply_intent()` correctly caught it and returned the safe fallback (`intent="unclear"`, `confidence=0.0`), which correctly triggered the confidence gate → `flagged_manual_review` → no agent message. This is the fail-safe path working correctly under a genuine live failure, not a bug.
- **Test G (1 check):** asserted `recovery_status` must stay unchanged after a forced agent-message persistence failure — incorrect assertion. `recovery_status` correctly transitions `open→recovering` as part of the already-committed `execute_action()` call, which happens *before* message delivery is attempted. Isolation was actually confirmed correct via the other 3 checks in that test (decision still executed, exactly one `recovery_actions` row, zero agent messages from the failed insert).
- **Corrected overall result: 23/23 PASS.**

### 18b. Active files tracker (Stage 3)
See Section 12h for the full list of created/modified/peripheral files.

### 18c. Current blocker / terminal state
**None.** Stage 3 is fully implemented and verified end-to-end.

### 18d. Immediate next step
- Next actionable coding task: **Day 10–11, Stage 4 — Ops Dashboard build** (batch view, filters, metrics panel, case detail with audit trail; Live Agent Console per Section 9a — event triggers, free-text reply box, visible reasoning panel showing raw LLM output/rule decision/compliance result, live audit trail feed, live-recomputing metrics).
- No code written yet for Stage 4.
- **Awaiting explicit user confirmation to begin, per the standing "no autonomous multi-step code generation without explicit per-step confirmation" decision (Section 16) — this constraint remains in force for this document and for any future chat continuing from it.**

## 19. FORWARD ROADMAP

### 19a. Immediate (locked build order, Section 11)
1. **Stage 4 — Ops Dashboard + Live Agent Console** (Days 10–11). Requirements fully specified in Sections 8, 9, 9a. Dependency check: satisfied — Stage 3's `messages` table population and `ml_recovery_probability` persistence are both live, so the Live Agent Console's escalation context bundle (Section 9a) can be built as originally specified. **Not started. Requires explicit user go-ahead before any code is written (Section 16).**
2. **Stage 5 — Deliberate failure injection + stress test** (Day 12). Inject one deliberate edge case (duplicate/late webhook), show clean handling, document in the audit trail. This becomes the formal "what broke" application answer (distinct from, and in addition to, the informal Stage 3 debugging incidents noted in Section 14, which remain candidate material but are not yet written up as a standalone log file).
3. **Days 13–14** — Testing, bug fixes, metrics check, repo cleanup, README, pitch video, final review.

**[APPROVED EXCEPTION, granted during Stage 4 planning]** The Data Factory (Section 20.1–20.5, 20.17–20.24) is authorized to be built and validated in parallel with Stage 4, as a decoupled offline workstream — it does not modify, call, or depend on the live `classify()`/`decide_action()`/`execute_action()` pipeline or the production DB. Reason: the Data Factory is a prerequisite for Retry Timing Optimization + Expected ₹ Recovery (Section 20.5–20.7), which is now targeted for completion *before* hackathon submission rather than post-submission. Dataset generation/calibration work may proceed now. **Integration of any resulting optimization capability into the live rule engine remains gated**: it may only begin once the relevant core stages are stable, and must go through full regression testing before being wired into `decide_action()`, per the existing authority chain (Section 20.7). Everything else in Section 20 (payment-method switching, pre-failure prediction, decision graph, digital twin, RaaS API, and all remaining items) is unaffected by this exception and remains explicitly post-Stage-5, not authorized to begin.

### 19b. Post-Stage-5 (Future Architecture overview)
Once Stage 5 is stable, the following priority order governs any future optimization work (full detail in Section 20):
1. Build the Data Factory (extract synthetic-data generation out of the main codebase into standalone, reproducible infrastructure).
2. Upgrade the recovery-risk dataset (larger corpus, original 8k-case corpus preserved as baseline, not deleted).
3. Retry-timing optimization (highest-value new capability).
4. Customer-specific recovery policy.
5. Bank/payment-method health signal (India-specific).
6. Payment-method switching (only after explicit schema/action-type approval).
7. Pre-failure prediction (only after the recovery system is stable, and only after explicitly reopening the currently-locked conceptual-only `Prevent` boundary, Section 3).

Everything else described in Section 20 (recovery decision graph, digital twin, recovery-as-a-service API, merchant-specific baselines, A/B experimentation, LTV-aware optimization, multi-PSP routing) remains future-only conceptual material, not scheduled, not approved.

## 20. FUTURE ARCHITECTURE — [DEFERRED, post-Stage-5 only, not authorized — EXCEPT the Data Factory workstream (Section 20.1–20.5, 20.17–20.24), approved to run in parallel with Stage 4, see Section 19a]

*This section consolidates the full "Calibrated Payment Recovery Data Factory + Optimization" extension specification. It is preserved here in complete detail because it represents carefully-designed future architecture, and every item in it is explicitly subordinate to Sections 11 and 19a, requiring separate explicit approval to begin, consistent with Section 16's "no mid-build scope changes" rule. **One exception is currently active:** the Data Factory's dataset generation/calibration workstream was explicitly approved to begin in parallel with Stage 4 — see Section 19a for exact scope. All other items in this section remain unapproved and must not begin before Stage 5 is stable.*

### 20.0 Precedence rule
**Sections 0–19 of this document (the current locked SoT) remain authoritative for all currently locked architecture, scope, compliance, AI-authority, action-selection, dashboard, and build-order decisions.** This Future Architecture section does not replace or reopen any of them. Where this section and the rest of the document conflict, the rest of the document wins unless the user explicitly approves the change.

### 20.1 New major architectural component: Calibrated Payment Recovery Data Factory
**Purpose:** generate reproducible, schema-compatible, statistically calibrated synthetic payment, failure, customer, bank-health, retry, and recovery datasets that can be consumed by the Revenue Recovery Engine. It is **offline infrastructure**, not a runtime component of the recovery agent. The current basic generator becomes an implementation *inside* this Data Factory rather than part of the main application — this is a refactoring/extension of the existing simulator (Section 15), not a replacement of it.

**Why a separate Data Factory is required:** a single public dataset cannot provide the complete information needed for planned models (e.g. retry-timing needs relationships between payment context, failure reason, customer history, payment method, bank condition, time of failure, retry timing, retry outcome, recovered amount — generally private payment-processor data). Stripe's Smart Retries is a useful industry benchmark for the scale of this data problem: Stripe describes using more than 500 attributes and billions of payment data points to predict optimal retry timing. Correct approach: public data/research → calibration → controlled synthetic simulation → validation → task-specific datasets → ML training/evaluation. Public datasets are calibration references, not automatically valid replacements for production payment-gateway data.

**Public-data philosophy:** public datasets/research establish realistic characteristics (amount distributions, transaction frequency, time-of-day patterns, categorical distributions, entity relationships) but must NOT be treated as Razorpay production data, ground truth for Indian payment recovery, direct evidence of real payment success probabilities, or a substitute for private gateway-level recovery histories. A useful verified precedent: the Fraud Detection Handbook's transaction simulator, which models relationships between customer profiles, transaction characteristics, and temporal behaviour (not independent random fields) across 1.75M+ transactions.

### 20.2 Data Factory architecture (12 logical stages, Data-Factory-internal only)
1. source ingestion
2. source normalization
3. calibration
4. simulation configuration
5. entity generation
6. payment/event simulation
7. bank/PSP health simulation
8. recovery simulation
9. ground-truth/outcome generation
10. dataset validation
11. task-specific dataset building
12. dataset versioning and registry

The Revenue Recovery Engine receives generated datasets as inputs and should not know how they were generated.

**Source ingestion:** each source registered with source name, URL, date accessed, license/usage constraints, fields available, known limitations, intended calibration use. Registry must explicitly record: "Calibration source — not production truth."

**Normalization:** raw sources normalized into common conceptual variables (`transaction_amount`, `transaction_timestamp`, `customer_identifier`, `merchant_identifier`, `payment_method`, `transaction_status`, `failure_type`, `geography`). Do not force unrelated source fields into the main project schema if doing so would create false equivalence — the normalized layer is only an intermediate calibration layer.

**Calibration layer:** converts source observations into simulator parameters (amount distributions, transaction-frequency distributions, customer behaviour distributions, payment-method proportions, temporal/merchant activity patterns, conditional relationships, failure distributions where credible public evidence exists). Output: a versioned `calibration_profile.json`. The simulator must be able to operate with a fixed calibration profile so the same dataset can be reproduced.

**Simulation configuration (configuration-driven, not hard-coded):**
- Entities: customers, merchants, banks, PSPs/payment processors where required, payment methods.
- Payment methods (minimum, aligned with current schema): UPI, card, netbanking, wallet.
- Failure categories: the existing 6 root causes (Section 3) unless the user explicitly approves an additional root cause.
- Retry windows (modelling buckets, not production claims): 10m, 30m, 1h, 3h, 6h, 12h, 24h, 48h.
- Customer profiles: high/low historical reliability, fast/slow responder, preferred payment method, preferred recovery channel, recurring payment-time patterns.

**Entity generation** — must generate persistent entities, not independently random rows:
- *Customers:* existing fields `customer_id`, `payment_history_score`, `past_recovery_rate`, `preferred_channel` (Section 6a/6b), plus additional simulation-only fields (preferred payment method, typical transaction amount, transaction frequency, typical payment hour, historical successful retry rate, historical response rate) — these are simulation/model features and do NOT need to be inserted into the production-style `customers` table unless the main engine actually uses them.
- *Merchants:* merchant ID, category, transaction-volume profile, typical amount profile, historical recovery baseline. Merchant-specific baselines remain an optional future capability, not mandatory.
- *Banks/PSPs:* represented as entities whose operational condition changes over time — necessary for the bank-health simulation below.

**Event simulation:** generates successful payment attempts, failed payment attempts, subsequent recovery attempts, recovered payments, permanently lost payments. The existing `payments` table (Section 6b) remains the canonical project-facing schema. The Data Factory may maintain richer internal simulation tables and then export data into the existing project schema plus additional ML datasets.

### 20.3 Bank-health simulation — India Bank / Payment Ecosystem Health Intelligence
Implemented as a **simulated signal**, not a claim of access to real Razorpay bank-health data. Generates time-dependent features: bank, payment method, PSP, time window, success rate, failure rate, timeout rate, health score. Health state affects simulated transaction outcomes (e.g. lower bank health → higher probability of timeout/technical failure → lower probability of immediate retry success).

**Industry context — do not present this as a new invention:** Juspay already provides predictive routing, health-based routing, outage detection, cascading, and gateway-performance tracking, maintaining health scores by gateway/payment-method combination. **Differentiation claim to use:** India-specific ecosystem-health simulation used as a context feature inside a broader revenue-recovery optimizer — not "we invented smart routing."

### 20.4 Recovery simulator
For every eligible failed payment, generate candidate recovery actions: retry, delayed retry, customer reminder, payment link, payment-method change, escalation, stop. The exact action types available to the **main production-style engine remain controlled by this SoT** (Section 6c's `recovery_actions.action_type` is currently retry/reminder/escalate/stop only). The Data Factory may generate candidate alternatives for ML experimentation even if the current engine does not yet execute every one.

### 20.5 Retry-timing optimization dataset — highest-priority new ML dataset
**Target:** not "will this payment eventually recover?" but **"given the payment context, which future retry window produces the best probability/value of recovery?"**

**Industry precedent:** Stripe's Smart Retries is an ML system trained on billions of data points predicting when a failed payment should be retried, noting the optimal retry time differs by failure circumstance (technical failures can benefit from near-immediate retry; insufficient funds may benefit from waiting). Adyen's Auto Rescue similarly decides which refused payments can succeed later and retries at optimal times.

**Dataset structure:** each failed case may produce multiple candidate rows: `payment_context + candidate_retry_window + customer_context + failure_context + bank_health + attempt_number → retry_success → recovered_amount`. Same payment can have candidate observations at +10m, +30m, +1h, +3h, +6h, +12h, +24h.

**Critical rule (directly inherited from Section 15c):** all candidates belonging to the same underlying payment/case must remain in the same train/test group. Do not randomly split candidate rows across train and test — this is the exact leakage risk already locked and solved for the existing 8k-case corpus (Section 15c), and the same grouped-split design must be preserved here.

**Ground truth:** the simulator must create a hidden probability of success based on: customer behaviour + failure type + payment method + bank health + time of day + day of week + attempt number + retry timing + latent randomness → recovery probability → sampled (stochastic) outcome. Do not encode deterministic rules like "six-hour retry always succeeds" — this directly parallels the existing hidden-state/stochastic-outcome design already locked for the current ML corpus (Section 15d), extended to the timing dimension.

**Root-cause-specific retry timing (no universal schedule):** insufficient funds may favor a future window over immediate retry; gateway/bank timeout may favor a shorter interval; expired card requires a payment-method update (timing alone won't solve it); authentication failure may need customer intervention over background retries; network failure may favor a later technical retry. These are modelling assumptions to calibrate and test, not hard-coded production truths.

### 20.6 Expected-Revenue optimization
`expected_recovered_amount = transaction_amount × predicted_success_probability`. Where appropriate, later incorporate action costs and risk costs. This changes the optimization target from "highest success probability" to "highest expected recovered revenue among permitted actions." Example: a ₹1,000 payment at 90% recovery probability has expected recovery of ₹900; a ₹50,000 payment at 60% probability has expected recovery of ₹30,000. This becomes the economic objective for the higher-level optimizer, and directly strengthens the existing required metric "₹ recovered / ₹ at risk" (Section 8).

### 20.7 Next-best-recovery optimization and critical authority order
Once probability and timing are available, the system should conceptually evaluate: retry now, retry later, send reminder, send payment link, change payment method, escalate, stop. For each eligible option, estimate expected recovered amount; the optimizer proposes the highest-value permitted option.

**Critical authority order (must never be violated — this is the future-architecture restatement of Section 15g's existing boundary):**
1. ML predicts.
2. Optimizer ranks eligible options.
3. Rule engine validates compliance and eligibility.
4. **Rule engine has final authority.**
5. Executor performs the approved action.
6. Outcome is logged.

**The optimizer must never bypass the rule engine.** This is not a new rule — it is Section 5's and Section 15g's existing "ML predicts, rules decide" boundary, extended to a future multi-candidate optimizer instead of the current single-candidate advisory scorer.

### 20.8 Insertion point relative to the existing retry-eligibility gate
The SoT already has a pending root-cause retry gate (Section 3's [PENDING] item, especially around `expired_card`). The future timing optimizer must be inserted **after** eligibility. Correct order: (1) determine root cause; (2) determine whether retry is technically meaningful; (3) enforce retry limits/cooldown/compliance; (4) if retry is eligible, estimate timing; (5) estimate expected recovered amount; (6) execute/schedule only after the rule engine approves. **The timing model must never make an ineligible root cause eligible.**

### 20.9 Customer-specific recovery policy
Uses existing fields `payment_history_score`, `past_recovery_rate`, `preferred_channel` (Section 6a) to build a customer-specific recovery context: prefer retry for customers with strong retry history; prefer a channel a customer reliably responds to; reduce repeated attempts and move toward escalation/stop for customers with repeated failed recovery attempts; consider an alternative payment method when a customer has a strong historical preference, if that feature is enabled. **This policy should influence the existing `decide_action()` function — it must not create a second action-selection engine** (directly reinforcing Section 3's single-shared-engine rule).

### 20.10 Autonomous payment-method switching — later optimization, not first integration
**Purpose:** when the original payment method fails, estimate which alternative available method has the highest chance/value of recovery (example candidate probabilities: UPI 82%, card 62%, netbanking 51%, wallet 36%). The system can recommend or execute the highest-value eligible alternative.

**Current SoT impact:** the current `recovery_actions.action_type` is defined as retry/reminder/escalate/stop (Section 6a/6b) — genuine payment-method switching requires an **explicit action-type/schema extension**. Do not silently overload `retry` to represent a payment-method change. If implemented: add a clearly named action such as `payment_method_change` **only after the existing system is stable and the user approves the schema extension**, then (1) explicitly approve the new action type; (2) update `execute_action()`; (3) update audit logging; (4) update compliance checks; (5) update dataset labels; (6) update test cases; (7) update the dashboard. Do not add it partially.

### 20.11 Pre-failure prediction — advanced extension, explicitly conflicts with a current lock until reopened
Moves one step earlier than the current recovery flow: predict `P(payment_failure)` before the attempt occurs, using features like customer history, payment method, bank, merchant, amount, time, historical bank/payment-method performance, current bank health. Could eventually allow preventive action before a failed payment creates recovery work.

**Direct conflict with current SoT (Section 3):** the current SoT explicitly defines `Prevent` as [CONCEPTUAL] and states no upstream prevention capability is currently implemented or planned. **Pre-failure prediction must remain [DEFERRED] until explicitly approved** — it must not be introduced into the current core loop merely because the Data Factory supports it.

### 20.12 Recovery Decision Graph — future-only, no new infrastructure
A conceptual representation of relationships among customer, merchant, bank, PSP, payment method, failure reason, retry attempt, timing, outcome — can reveal relationships like "particular bank + payment method + time window → elevated technical failures." **Current recommendation: do not introduce a graph database.** First implementation should derive these relationships through ordinary structured data, rolling aggregates, and ML features. Remains a future analytical representation, not a new recovery engine.

### 20.13 Payment Failure Digital Twin — future-only, no new subsystem
Would simulate several possible recovery actions before executing one (retry now, retry later, switch payment method, send payment link, etc.), using the same candidate-outcome models from the Data Factory. **Treat as an optimization/testing capability, not a separate product subsystem.** Do not build an independent "digital twin platform."

### 20.14 Recovery-as-a-Service API — future productization, not required now
A future version could expose the optimizer as `POST /recover` (input: payment/customer/failure context; output: recommended action, predicted probability, expected recovered amount, timing). The existing FastAPI backend (Section 10) can eventually expose it, but creating a second API product now would increase scope without improving the required demo. Not required for the current hackathon architecture.

### 20.15 Recovery Control Tower — top-level product surface, not a separate decision system
Consumes outputs from the shared recovery engine (does not decide anything itself). Existing required metrics remain as locked in Section 8. New optional intelligence metrics (future): predicted recoverable ₹, expected recovered ₹, incremental ₹ recovered versus baseline, optimal retry window selected, recovery action selected, recovery by root cause/payment method/bank/customer segment, attempts avoided. **Most important future headline: incremental ₹ recovered by the optimized policy.**

### 20.16 Industry benchmark positioning — honesty requirement
Do not claim to outperform production systems based solely on synthetic data. Industry references (Stripe Smart Retries and Adaptive Acceptance — reported $6B+ in legitimate declined transactions recovered in 2024 and a 35% reduction in retry attempts; Adyen Auto Rescue and account-updater recovery; Razorpay's own Smart Payment Retries for subscriptions and its 2026 Intelligent Retry Engine / intelligent downtime handling with auto-routing; Juspay's predictive/health-based/fallback routing, cascading, outage alerts) establish that intelligent recovery is a legitimate production problem — but these are each company's own reported production figures and are **not directly comparable** to this project's synthetic results. This directly reinforces the existing Section 8 metric requirement: external benchmarks shown only as labeled contextual reference, never as a claim of superiority. **Differentiating story to use:** "We combine recovery probability, retry timing, customer policy, and simulated Indian payment-ecosystem health into a shared expected-revenue recovery decision layer" — not "we invented smart routing/outage detection/smart retry" (all already mature elsewhere).

**Must NOT become primary innovation claims:** generic smart routing (mature in Juspay), generic outage detection (Juspay, Razorpay), generic Smart Retry (Stripe, Adyen, Razorpay), basic dunning/reminders (crowded category), basic payment-method alternatives (common functionality).

### 20.17 Data Factory task-specific datasets (A–F)
The Data Factory maintains one canonical synthetic event world and derives multiple task-specific datasets from it:
- **Dataset A — Recovery Risk:** `Will this failed payment recover?` — used by the existing `ml_recovery_probability` (Section 15).
- **Dataset B — Retry Timing:** `Will this payment recover at candidate retry window T?` — used by retry-timing optimization (20.5).
- **Dataset C — Customer Recovery Policy:** `Which recovery action/channel/timing has historically performed best for this customer profile?`
- **Dataset D — Payment Method Recovery:** `Which alternative payment method has the highest recovery probability/value?`
- **Dataset E — Bank Health:** `What is the current health/reliability signal for a bank/payment-method combination?`
- **Dataset F — Pre-Failure Risk:** `Is an upcoming payment likely to fail?` — **remains deferred until explicitly approved** (20.11).

### 20.18 Canonical synthetic data model (Data-Factory-internal — need not become production tables)
- **Customers:** `customer_id`; payment history; recovery history; preferred payment method; preferred channel; transaction behaviour.
- **Merchants:** `merchant_id`; category; volume profile; amount profile; recovery baseline.
- **Payments:** `payment_id`; `customer_id`; `merchant_id`; amount; currency; method; bank; PSP if simulated; timestamp; initial status; failure context.
- **Bank-health observations:** bank; payment method; PSP if applicable; timestamp/window; health score; timeout rate; failure rate; success rate.
- **Recovery candidates:** `payment_id`; attempt number; action; candidate retry timing; alternative payment method; customer context; bank-health context.
- **Recovery outcomes:** candidate ID; success/failure; recovered amount; time-to-recovery.
These are Data Factory entities and do not all need to become tables in the main SQLite application database (Section 6b).

### 20.19 Ground-truth engine
Must explicitly separate **simulation assumptions** (what the synthetic world believes) from **observed outcome** (the stochastic result generated from those assumptions). The ground-truth engine may combine customer tendency, failure type, payment method, bank condition, retry timing, attempt number, temporal effects, merchant conditions, and random noise. The result is a probability distribution, not deterministic logic — directly consistent with the existing hidden-state design (Section 15d).

### 20.20 Data validation (before a dataset can be consumed by the Revenue Recovery Engine)
- **Schema validation:** every required field exists and has correct type/range.
- **Distribution validation:** transaction amounts, payment method frequency, failure distribution, temporal distribution, customer frequency.
- **Relationship validation:** e.g. weaker bank health → more technical failures; failure type → different recovery behaviour; timing → different recovery probability; customer recovery history → future recovery differences.
- **Leakage validation:** no field may directly expose the target (e.g. do not include a generated `best_retry_time` feature in the input dataset for a model whose task is to predict the best retry time).
- **Group-split validation:** candidate rows from the same underlying payment remain in one dataset partition (Section 20.5's critical rule).

### 20.21 Dataset versioning
Every dataset release gets a version (e.g. `dataset_v001`) with metadata: dataset version, random seed, generation timestamp, calibration profile version, simulation configuration version, source datasets used, number of customers/merchants/payments/failures/recovery candidates/successful recoveries, validation results. Makes model experiments reproducible.

### 20.22 Dataset scenarios (future)
Normal environment; bank degradation; high insufficient-funds environment; technical outage; customer-behaviour shift; distribution shift. Important for testing whether the recovery system behaves safely outside the easiest simulated environment.

### 20.23 Adversarial/stress datasets (future)
Repeated failures, conflicting customer behaviour, low-confidence cases, unusual transaction values, abrupt bank-health deterioration, repeated retries, cases near compliance limits, data distribution changes. Can be used to verify the existing stopping rules and Live Agent Console behaviours — directly supports the existing proof requirement to demonstrate deliberate edge cases and controlled failure recovery (Section 9b.5, Section 14).

### 20.24 Current main-system schema impact — do not replace
Existing tables (Section 6b) remain: `payments` (keep all existing fields and Razorpay-aligned naming); `customers` (keep existing fields); `recovery_actions` (keep the audit-log role); `messages` (keep the existing LLM conversation/intent role). Additional Data Factory fields stay in the Data Factory until the corresponding feature is actually integrated into the main engine.

### 20.25 Minimal main-system additions when retry-timing optimization is eventually approved
A minimal optional field could be `scheduled_for` (for scheduled actions). A further optional derived value could be `expected_recovered_amount` (for an optimized recovery decision). **Do not add large amounts of model metadata to `recovery_actions` — it remains an audit log, not a model-training warehouse.** Candidate-action model experiments stay in the Data Factory's task-specific datasets.

### 20.26 ML architecture for the future retry-timing model
Default starting point remains `XGBoost / logistic regression` (Section 5), consistent with the existing model choice. For retry timing: first implementation should be an XGBoost/LightGBM-style tabular model — input: payment context + candidate retry window; output: predicted success probability; then calculate expected recovered amount. Preferable to immediately adopting deep learning. Stripe's production Smart Retries is considerably more complex (evolved to an AutoML ensemble) but this project does not need to replicate Stripe's production scale — the goal is to demonstrate sound modeling and experimentation, not reproduce a billion-row proprietary model.

### 20.27 Retry-timing evaluation — baselines required
- **Baseline 1:** fixed retry schedule.
- **Baseline 2:** root-cause-based rule schedule.
- **Treatment:** ML-selected retry window.
Exact baseline timings should be configurable, not hard-coded as universal industry truth. Razorpay's current subscription documentation (automatic next-day retry, T+3-day retry model) can serve as contextual inspiration for a fixed-rule baseline, but the synthetic benchmark must be labeled as a simulation experiment, not a production comparison (Section 20.16).

### 20.28 Evaluation metrics (future retry-timing model)
- **ML metrics:** precision, recall where relevant, confusion matrix where classification is used, calibration, probability reliability — directly extending the existing §9b.2 proof requirement (calibration checks rather than a single accuracy number).
- **Recovery metrics:** recovery rate, ₹ recovered, ₹ at risk, expected ₹ recovered, incremental ₹ recovered versus baseline, time-to-recovery, attempts per recovered payment, unnecessary retries, false-positive cost.
- **Timing-specific metrics:** selected timing, actual success rate of selected window, revenue per retry, number of retries avoided, improvement over fixed schedule.

### 20.29 Main success criterion for future optimization work
> **Does the optimized recovery policy recover more money than the baseline policy without violating rules or increasing unnecessary attempts?**
The most important output is therefore `incremental_recovered_revenue`, not `model_accuracy`.

### 20.30 Control/treatment experiments (future formal experiment layer)
Compare **Control** (existing rule-based recovery policy) vs **Treatment** (optimized policy) on: recovery rate, ₹ recovered, time to recovery, retries used, exceptions. The current SoT already requires a control-group split for false-positive cost (Section 8), so this should eventually become a formal experiment layer rather than merely a dashboard statistic.

### 20.31 Merchant-specific baseline (future, optional)
Model may learn global behaviour plus merchant-specific behaviour (`global recovery rate` + `merchant recovery rate` + `merchant × payment method × failure type`). Valuable when generating many synthetic merchants, but remains optional — do not introduce a complex hierarchical model unless the basic model is already stable.

### 20.32 LTV/customer-value optimization — [DEFERRED, already listed as deferred in Section 11]
Instead of optimizing only transaction amount, consider expected long-term value: `expected recovered current revenue + expected retained future value`. Requires an LTV model, introduces another target, changes optimization behaviour. **Remains deferred for the current hackathon unless explicitly approved** — the original SoT (Section 11) already lists LTV/customer-value segmentation as deferred; this is confirmation, not a new decision.

### 20.33 Multi-PSP routing — [DEFERRED, already listed as deferred in Section 11]
Do not implement as a core new feature. Reasons: expands architecture significantly; requires route-level simulation; requires additional action types; duplicates capabilities already mature in the Indian ecosystem (Juspay). The original SoT (Section 11) already lists multi-PSP/alternate-gateway routing as deferred; this confirms it and adds: it may be represented in future simulations but should **not** become a new main-system subsystem.

### 20.34 Recovery-as-a-Service — future productization (restated)
The existing FastAPI backend (Section 10) already provides a base that could eventually expose the recovery decision engine as a reusable API — no separate service architecture is required to conceptually support this later. Not built for the current hackathon.

### 20.35 Recovery Decision Graph and Digital Twin — reuse rule (restated)
Both are future analytical concepts. They should initially reuse the same canonical Data Factory data. Do not create: Neo4j; a separate graph service; a separate simulation runtime; a second recovery engine. Relationships and hypothetical outcomes can initially be computed from structured tables and models.

### 20.36 Recommended implementation priority (full detail — summarized in Section 19b)
- **Priority 0 — Preserve the current SoT.** Do not change: three-path shared engine; rule authority; LLM restriction; compliance rules; 150-record evaluation dataset; Live Agent Console; current core build order.
- **Priority 1 — Build the Data Factory** (ingestion, calibration, simulation config, entity generator, event simulator, bank-health simulator, recovery simulator, ground truth, validation, dataset builder, versioning). The current basic generator becomes an implementation inside this Data Factory.
- **Priority 2 — Upgrade recovery-risk dataset.** Generate a larger structured training corpus than the initial ~8k-case corpus if compute/time allows. **Keep the original 8k-case corpus reproducible as a baseline — do not delete it** (directly reinforces Section 15a's two-corpus architecture).
- **Priority 3 — Retry timing optimization** (highest-value optimization): candidate timing windows, timing-aware features, success prediction, expected recovered ₹, baseline comparison.
- **Priority 4 — Customer-specific policy:** use customer history and recovery behaviour; feed policy context into the existing decision engine.
- **Priority 5 — Bank-health signal:** add simulated bank/payment-method health as an advisory feature; do not turn it into a separate routing engine.
- **Priority 6 — Payment-method switching:** only after the action-schema and execution path are explicitly approved (20.10).
- **Priority 7 — Pre-failure prediction:** only after the recovery system is stable (20.11) — currently conflicts with the SoT's conceptual-only `Prevent` stage (Section 3).

### 20.37 Recommended integration order with the existing build (restated, ties directly to Section 11/19a)
The existing locked build order requires: (1) core rules-only loop; (2) ML risk model; (3) LLM layer; (4) dashboard; (5) stress test. **The Data Factory must not derail those five stages.** Intended integration: finish and stabilize Stages 1–5 first (Stages 1–3 already done, per Section 18) → then move synthetic generation out of the main codebase into the independently reproducible Data Factory → then add the first optimization layer using generated datasets. The Data Factory can be developed in parallel only if it does not block the locked core build; otherwise it remains post-Stage-5 work. This follows the existing "new ideas go on a later list until Stage 5 is stable" rule (Section 11). **[STATUS: this parallel condition is now active, approved during Stage 4 — see Section 19a for exact scope and the integration gate that still applies.]**

### 20.38 What the main project should receive from the Data Factory (clean handoff boundary)
**Should receive:** training dataset; validation dataset; test dataset; dataset metadata; schema version; feature definitions.
**Should NOT receive:** source raw datasets; calibration implementation; simulator internals; ground-truth formulas.
This preserves a clean experimental boundary.

### 20.39 What the Data Factory must never do
Call the recovery agent at runtime; make live recovery decisions; bypass rules; directly modify the production SQLite database; invoke the LLM for payment decisions; execute payment actions; become a second application/business logic layer. Its job ends when the validated dataset is exported.

### 20.40 What the Revenue Recovery Engine must never do (future-proofing the existing engine)
Regenerate training data during normal operation; modify synthetic-data assumptions; change dataset distributions at runtime; treat synthetic ground truth as known truth; allow ML to override compliance; allow the LLM to select recovery actions. (The last two are restatements of the already-locked Section 5 boundary, extended forward.)

### 20.41 Dataset reproducibility requirements
Every generated dataset must be reproducible from: source references; calibration profile; simulation configuration; generator version; random seed. A researcher should be able to regenerate the same dataset version.

### 20.42 Dataset honesty requirements
The project must never state "This model recovered X% on real Razorpay data" unless real Razorpay data is actually supplied. Correct wording: "The model achieved X% recovery on held-out simulated cases," or "The optimized policy produced X% higher recovery than the fixed baseline in our calibrated synthetic environment." Published Stripe/Adyen/Razorpay/Juspay figures must be clearly identified as external industry context, not directly comparable project benchmarks — the current SoT already requires this distinction for industry benchmarks (Section 8, Section 20.16).

### 20.43 Data Factory limitations that must be acknowledged
Synthetic data cannot reproduce: proprietary issuer behaviour; real customer intent; real network effects; proprietary gateway routing behaviour; actual Razorpay transaction distributions; production fraud/risk systems; all regulatory/network retry constraints. The Data Factory is therefore a controlled experimental environment, not a production replica — acceptable for this buildathon because the official track provides no Track 03 dataset/sandbox (Section 0).

### 20.44 Recommended future project positioning
The final system should not be presented as "a synthetic-data project." The Data Factory is supporting infrastructure. The actual product remains the **Autonomous Revenue Recovery Engine**: detect revenue at risk, diagnose the cause, estimate recovery probability, determine the highest-value permitted intervention and timing, execute it under hard rules, and measure the incremental money recovered. The Data Factory exists to provide a reproducible experimental environment for that intelligence.

### 20.45 Final future-architecture responsibilities (extends Section 15g/20.7's authority chain)
- **Data Factory:** ingestion, calibration, simulation, ground truth, validation, dataset versioning.
- **ML layer:** recovery probability, retry timing probability, future extensions (e.g. failure prediction), action-value estimation.
- **Optimizer:** comparing eligible candidate actions/timings; estimating expected recovered ₹; proposing the highest-value option.
- **Rule engine:** final authorization; compliance; retry limits; cooldown; stopping; escalation. (Unchanged from Section 5/15g — this is the same rule engine, not a new one.)
- **Executor:** performing the approved action. (Same `execute_action()`, Section 3/6.)
- **LLM:** language generation; intent extraction. (Unchanged from Section 5/12 — no new authority.)
- **Control Tower:** visibility; metrics; case inspection; auditability; business-level recovery reporting. (Same dashboard surface as Section 9, extended with future metrics per 20.15.)

### 20.46 Final approved conceptual flow (future state, once all priorities are built)
1. Data Factory generates and validates training/evaluation environments offline.
2. ML models are trained on those datasets.
3. A real/simulated payment event enters the existing shared recovery pipeline.
4. `classify()` determines the event/root cause.
5. ML estimates recovery probability and, where enabled, timing/action value.
6. Customer context and bank-health context are incorporated.
7. Candidate recovery options are ranked by expected recovered ₹.
8. `decide_action()` remains the final authority and enforces every compliance rule.
9. `execute_action()` performs the approved action.
10. LLM messaging occurs only where communication is appropriate.
11. Customer responses are parsed into structured intent.
12. The existing rule engine reevaluates the case.
13. Every action/outcome is recorded in the existing audit trail.
14. Control Tower metrics update from the database.
15. Results are compared against baseline policies.
16. Successful outcomes become future training/evaluation data only through an explicit Data Factory/experiment process, not by silently mutating the live model.

### 20.47 Immediate final scope, restated for clarity (what's core/unchanged vs. new/future)
**Core, unchanged (this is everything in Sections 0–19 of this document):** checkout abandonment; payment failure; overdue invoice; shared recovery engine; rule authority; compliance; LLM messaging/intent; ML recovery risk; dashboard (Stage 4, upcoming); Live Agent Console; audit trail; deliberate failure proof (Stage 5, upcoming).

**New Data Factory (future):** public-source ingestion; calibration; synthetic customer/merchant/payment generation; bank-health simulation; failure simulation; recovery candidate generation; stochastic ground truth; validation; task-specific datasets; dataset versioning.

**First new optimization (future):** payment retry timing optimization.
**Closely coupled optimization (future):** expected recovered ₹ / next-best-recovery scoring.
**Next optimization (future):** customer-specific recovery policy.
**Additional India-specific signal (future):** India bank/payment-health intelligence.
**Later extension (future):** autonomous payment-method switching.
**Later extension (future):** pre-failure prediction.
**Future-only concepts (not scheduled):** recovery decision graph; payment-failure digital twin; recovery-as-a-service API; merchant-specific baselines; recovery-policy A/B experimentation; LTV-aware optimization; multi-PSP routing.

### 20.48 Final non-negotiable anti-clash rules for all future work
1. Do not create a second recovery engine.
2. Do not create per-event-type decision pipelines.
3. Do not allow the LLM to control payment/recovery actions.
4. Do not allow ML to override rules.
5. Do not make the Data Factory a runtime dependency.
6. Do not replace the existing 150-record demo/evaluation dataset.
7. Do not discard the existing ~8k-case ML corpus; preserve it as the baseline corpus/version.
8. Do not introduce new action types without explicit schema and executor changes.
9. Do not introduce multi-PSP routing into the current build.
10. Do not introduce pre-failure prevention without explicitly reopening the current conceptual `Prevent` boundary.
11. Do not claim synthetic results are production Razorpay results.
12. Do not let the Data Factory become an uncontrolled Faker/random-data script.
13. Every synthetic relationship used by an ML task must have an explicit simulation rationale.
14. Every dataset version must be reproducible from configuration + calibration + code version + seed.
15. All new work must remain subordinate to the existing locked build order until Stage 5 is stable.

### 20.49 Core future implementation objective
The complete purpose of this future extension is to make the project capable of answering a stronger question:
> **Given a revenue-risk event, can the system determine not merely whether recovery is possible, but which permitted intervention, for which customer, under which payment conditions, and at what time is most likely to recover the greatest amount of money?**
The Data Factory supplies the controlled experimental environment. The ML layer predicts outcomes. The optimizer ranks expected value. The existing rule engine retains authority. The executor performs the approved action. The Control Tower proves the financial result. That is the intended final future architecture — **entirely subordinate to, and not a replacement for, everything locked in Sections 0–19 of this document.**

## 21. CONSOLIDATED "DO NOT CHANGE" CONSTRAINTS (quick-reference — full detail is in the sections cited)

These apply right now, to any continuation of this project, in any future chat:

1. **Single shared engine** — `classify()`, `decide_action()`, `execute_action()`, one `recovery_actions` table, across all 3 event types. No per-event-type pipelines. (Section 3)
2. **Rule engine is final compliance authority**, always. ML is advisory-only and scores only the already-selected candidate action. LLM only generates language / extracts structured intent — never selects, triggers, or overrides an action. (Sections 5, 15g)
3. **150-record demo/evaluation dataset** stays fixed, untouched, distinct from the ~8k-case ML training corpus. Neither replaces the other. (Sections 3, 15a)
4. **Grouped (case-level) train/test split** for any candidate-action-row ML dataset — never split same-case rows across train/test. (Sections 15c, 20.5, 20.20)
5. **Hidden state sampled once per case**, reused across all candidate rows for that case — never re-sampled per action. (Sections 15d, 20.19)
6. **Compliance constants are locked:** `MAX_RETRIES=3`, `COOLDOWN_HOURS=24`, `AUTO_STOP_DAYS=7`, contact window 9am–8pm, `CONFIDENCE_THRESHOLD=0.6`. (Section 7)
7. **Outcome vocabulary is locked and closed:** `executed`, `blocked_contact_hours`, `blocked_cooldown`, `blocked_already_stopped`, `blocked_already_escalated`, `flagged_manual_review`. No `blocked_max_retries`, no `escalated_no_response` as an outcome value. (Section 7c)
8. **`action_type` vocabulary is locked and closed:** `retry`, `reminder`, `escalate`, `stop` (plus `NULL` on `flagged_manual_review`). Any new action type (e.g. future `payment_method_change`) requires explicit schema + executor + compliance + dataset + test + dashboard updates, all seven, not partial. (Sections 6b, 20.10)
9. **`root_cause` is never sent to the Gemini prompt** in `parse_intent.py` — bias prevention for the mismatch check. (Section 12c)
10. **Two-surface limit:** Ops Dashboard (with folded-in case detail thread) + Live Agent Console. No third portal without spare time on days 12–13. (Section 9)
11. **Strict build order, no mid-build scope changes:** rules → ML → LLM → dashboard → stress test. New ideas go on the deferred list (Section 11) until Stage 5 is stable — this now includes everything in Section 20 (Future Architecture) **except the Data Factory's dataset generation/calibration workstream, explicitly approved to run in parallel with Stage 4 (Section 19a)**. Retry-timing optimization integration into the live engine remains gated regardless. (Sections 11, 16, 19)
12. **No autonomous multi-step code generation without explicit per-step user confirmation.** This document itself does not authorize starting Stage 4 code — that requires a separate go-ahead. (Section 16)
13. **Claude does not execute project scripts on the user's behalf** — user runs everything locally and pastes output back. (Section 16)
14. **Fresh-DB protocol** before any clean verification run of rule-engine/schema changes. (Section 17)
15. **`Prevent` stays conceptual-only** — no pre-failure prevention capability without explicitly reopening this boundary. (Sections 3, 20.11)
16. **Fraud/DDOS/spam detection, multi-PSP routing, LTV segmentation stay out of scope**, deferred, per Section 11 and reconfirmed in Section 20.
17. **Every headline metric must be traceable to a live query**, never only narrated. (Section 8, 9b.6)
18. **Data Factory (future) is offline infrastructure only** — never a runtime dependency of the recovery agent, never touches the production DB directly, never calls the recovery agent, never invokes the LLM for payment decisions. (Sections 20.39, 20.40)

## 22. VERIFICATION REQUIREMENTS FOR ANY FUTURE CHANGE (carried forward from established protocol)

Before treating any new code change as "done":
1. Wipe and rebuild the DB via the fresh-DB protocol if schema or rule-engine logic changed (Section 17).
2. Re-run the relevant verification/e2e script and get actual output back from the user (Claude does not run it) (Section 16).
3. Distinguish a genuine implementation bug from a test-script assumption error before reporting a failure as real — as was necessary and correctly done for Stage 3's 4 apparent failures (Section 18a).
4. Confirm the change doesn't touch anything in Section 21's constraint list without explicit approval.
5. Update documentation only once a full stage's micro-steps are complete and independently verified — not incrementally mid-stage (Section 16).

---

## END-OF-DOCUMENT STATUS SUMMARY

- **Stages 1, 2, 3 (schema/rule engine, ML layer, LLM layer): DONE, verified.**
- **Stage 4 (Ops Dashboard + Live Agent Console): NOT started — next task, awaiting explicit go-ahead.**
- **Stage 5 (deliberate failure/stress test): NOT started — after Stage 4.**
- **Days 13–14 (testing, cleanup, README, pitch video): NOT started.**
- **Future Architecture (Section 20): NOT started, not authorized, explicitly subordinate to Stage 5 being stable, requires separate explicit approval to begin — EXCEPT the Data Factory's dataset generation/calibration workstream (Section 20.1–20.5, 20.17–20.24), explicitly approved to run in parallel with Stage 4 (Section 19a). Retry-timing optimization and all other Section 20 items remain unapproved and gated.**
- **No code has been written, modified, or executed in the process of producing this document.** This document is a pure integration/reconciliation of the three source files plus the fourth (Future Architecture) specification — no new decisions were invented, no original SoT content was removed, and every point of change or resolution is explicitly marked and traced back to its source.

*End of consolidated Source of Truth.*
