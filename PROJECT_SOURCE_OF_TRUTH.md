# Razorpay AI Buildathon — Source of Truth
*Paste this entire document into any new chat to restore full project context.*

---

**Status markers used throughout this document:** **[LOCKED]** = decided, in force. **[PENDING]** = locked in principle, exact parameters await verification. **[DEFERRED]** = explicitly out of current scope, requires separate approval to begin. **[CONCEPTUAL]** = named for documentation/framing purposes only, not implemented or planned in the current build.

---

## 0. EVENT CONTEXT
- Razorpay AI Buildathon — student-only, hiring AI Builder Interns
- Solo build, no team
- Stipend if selected: ₹75,000/month, 6 or 12 months, Bangalore, in-person from September
- Application needs: name, college, grad year, in-person availability, resume, track, project name, problem solved, public GitHub repo, 5-min pitch video, "what broke and how you got out"
- No aptitude test, no GD — shortlisted go straight to panel interview
- Judging criteria: Problem taste, Build quality (does it run, is it structured, would you trust it), AI judgment (right tool right place, and where you chose not to use one), Failure recovery
- No dataset/sandbox provided by Razorpay for Track 03 — must generate own synthetic data

## 1. TIMELINE
- 14 days total
- Testing + submission complete by **Sept 4**
- Application closes **Sept 5**
- No competing commitments — full solo effort, heavy AI-assisted coding

## 2. TRACK (LOCKED)
**Track 03 — AI Revenue Recovery**
Official: "Build an agent that detects revenue at risk, determines the right intervention, and executes a bounded recovery workflow — from payment failures and checkout abandonment to overdue receivables."
The Bar: measured money recovered across a batch, compliant escalation, stopping rules, audit trail.

## 3. SCOPE (LOCKED)
Full loop — three connected recovery paths, **one shared recovery engine** (not three separate systems):
1. Checkout abandonment
2. Payment failure
3. Overdue invoice (B2B receivables)

**Architectural rule (hard, non-negotiable):** all three event types flow through the SAME `classify()`, `decide_action()`, and `execute_action()` functions and write to the SAME `recovery_actions` table. Do not build three parallel `if event_type == X` pipelines with duplicated logic — this is the single highest scope-fragmentation risk for a solo 14-day build. The only legitimate per-path differences:
- `root_cause` is only meaningful for payment_failed (null for the other two)
- Escalation timing input differs: `days_overdue` for invoices, event age (created_at) for the other two
Everything else — action selection, compliance checks, logging, LLM messaging, audit trail — is shared code.

**Payment failure root causes (6):** insufficient funds, card declined, bank timeout/gateway error, 3DS auth failure, expired card, network failure

**Explicitly excluded:** fraud/DDOS/spam detection (Track 02's territory — do not drift into it)

**Data volume:** 150 synthetic records (demo/evaluation dataset).

**ML training corpus distinction:** Stage 2 ML model training used a **separate, simulator-generated training corpus** — approximately 8,000 simulated cases / 22,016 candidate-action rows — with grouped train/test separation (17,605 train rows / 6,400 cases; 4,411 test rows / 1,600 cases). This corpus is distinct from and does not replace the 150-record demo/evaluation dataset above; the 150 records remain the fixed demo/evaluation set, unchanged.

**[PENDING] Root-cause retry-eligibility gate:** Within the existing shared `decide_action()` function, `retry` will be gated as ineligible for root causes where a retry cannot succeed by definition (candidate: `expired_card`; others to be confirmed). This does not add a new root cause, a new pipeline, or a new function — it is a conditional restriction on which `action_type` the existing rule engine may select for a given `root_cause`, evaluated alongside existing compliance checks. The exact non-retryable mapping is locked only after a read-only audit of current `decide_action()` behavior and explicit approval of the mapping.

**[CONCEPTUAL] Lifecycle framing (documentation only, no architecture change):** The existing shared engine is described end-to-end as: **Prevent → Diagnose → Retry/Recovery Action → Communicate → Escalate**. Implementation status per stage: `Diagnose` (`classify()`) and `Retry/Recovery Action` (`decide_action()` + `execute_action()`) are implemented as of the current Stage 1/2 build. `Communicate` (LLM messaging, Section 5) is not yet implemented — it is the locked Stage 3 build. `Escalate`'s underlying rule-engine logic (auto-stop/escalation rules, Section 7) already exists as of Stage 1; the human-facing escalation console and full context presentation (Section 9a) is not yet implemented — it is the locked Stage 4 build. **`Prevent` is explicitly [CONCEPTUAL]** — no upstream failure-prevention capability (e.g. pre-expiry card warnings) is implemented or planned in the current build; it is named only to accurately describe the lifecycle position of the other four stages.

## 4. DIFFERENTIATION STRATEGY
Most competitors will build: LLM sends a reminder on failure, no real diagnosis, no stopping rules, clean fake data, no audit trail.

Our edge:
1. Hybrid architecture — rules for compliance-critical decisions, ML for prediction, LLM for language only. Directly answers the "AI judgment — right tool right place" criterion.
2. Root-cause-specific recovery logic, not one-size-fits-all retry.
3. Compliant stopping rules — most solo builders won't model this at all.
4. Honest metrics — recovery rate, ₹ recovered vs at risk, false-positive cost, not a vanity number.
5. First-class audit trail — every decision logged with reasoning.
6. One deliberately engineered failure + graceful recovery, directly answering the form's "what broke" question.

Execution quality, the pitch video narrative, and panel interview performance matter as much as the architecture — plan alone does not guarantee top-tier ranking.

## 5. AI JUDGMENT SPLIT (locked)

| Function | Method |
|---|---|
| Failure root cause classification | Rule-based, ML-assisted |
| Recovery action selection (retry/wait/escalate/stop) | Rule engine (final authority) |
| Stopping rule enforcement | Hard-coded rules |
| Risk/recovery-probability scoring | ML (XGBoost / logistic regression) — informs rules, never overrides |
| Customer-facing message generation | LLM (Gemini API) — root-cause-specific, action-oriented recovery communication; LLM generates language only and never selects, triggers, or overrides a recovery action |
| Reply/intent parsing (promise-to-pay, dispute, payment_method_updated, Hinglish) | LLM, structured JSON output only |

Rule engine is always final authority on compliance. LLM never makes control decisions — only generates language or extracts structured intent.

Root-cause-specific messaging is a content requirement on the existing LLM function, not a new AI method or new decision authority — the sentence above already governs it.

## 6. DATA SCHEMA (Razorpay-aligned field names)

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

**Root cause → `error_reason` mapping:**
1. Insufficient funds → `insufficient_funds`
2. Card declined → `payment_declined`
3. Bank timeout/gateway error → `gateway_timeout`
4. 3DS auth failure → `authentication_failed`
5. Expired card → `expired_card`
6. Network failure → `network_error`

## 7. COMPLIANCE / STOPPING RULES (hard-coded, non-negotiable)
- Max 3 retry attempts per transaction
- Minimum 24hr cooldown between contact attempts
- Auto-stop after 7 days no response → escalate to human queue
- No contact outside 9am–8pm (simulated)
- Every action logged with a reason — no silent actions

## 8. METRICS TO SHOW
- Recovery rate (%) by root cause
- ₹ recovered / ₹ at risk
- Time-to-recovery distribution
- False-positive cost (control group split)
- Exceptions unresolved (count + reasons)
- External industry benchmark comparison (e.g. published third-party recovery-rate figures) — shown as a clearly labeled contextual reference point only, never framed as a claim of superiority over production payment providers or as a like-for-like comparison (different data, scale, and conditions)

## 9. SURFACES
1. Ops Dashboard (primary) — batch view, filters, metrics panel, case detail with audit trail
2. Case detail message thread — folded into dashboard, not a separate portal
3. **Live Agent Console** (locked feature — part of Stage 4, not optional) — see section 9a
No fourth portal unless days 12–13 have spare time.

## 9a. LIVE AGENT CONSOLE (locked feature)
Purpose: prove the system is genuinely reasoning live, not hardcoded/scripted — for both the recorded pitch video and the panel interview.

**Required components:**
1. Event trigger controls — buttons/dropdowns to create a real event (event_type, root_cause if applicable, amount). On trigger: real DB row inserted, timestamped now.
2. Free-text customer reply box — unconstrained input, any language/phrasing, including Hinglish. Placeholder text explicitly invites adversarial input (e.g. "Try anything — vague, angry, mixed language, nonsense").
3. Agent reasoning panel — shown simultaneously, not hidden:
   - Raw LLM output (JSON: intent, confidence, extracted date, sentiment)
   - Rule engine's resulting decision given that output
   - Compliance check result (e.g. retry count vs limit, cooldown status)
   - **On escalation specifically, this panel must present the full case context bundle: payment history, root cause, ML recovery probability (`ml_recovery_probability`), full message/conversation thread, all prior recovery actions, and a recommended next step.** The recommended next step is not a separate stored field — it is derived from the existing rule-engine decision/action output already produced by `decide_action()`/`execute_action()`, presented in the panel rather than computed by any new logic. This is a completeness requirement on this already-locked reasoning panel — no new subsystem, no new data field, no new model. Payment history (`payment_history_score`/`past_recovery_rate`), root cause (derivable from `error_reason` via `classify()`), `ml_recovery_probability`, and prior recovery actions are already produced/logged as of Stage 1/2 completion. The message/conversation thread depends on Stage 3 (LLM messaging layer, not yet built as of this amendment) — the `messages` table schema already exists, but population is a Stage 3 dependency; this bundle requirement must be satisfied at Stage 4 build time, by which point Stage 3 is complete per the existing build order (Section 11).
4. Live audit trail feed — appends in real time as actions happen (timestamp, action_type, triggered_by, reasoning) — reads directly from the database, not a mock.
5. Metrics that recompute live — aggregate stats (recovery rate, ₹ recovered) update visibly when case status changes, not static/pre-baked.

**Why this exists:** doubles as (a) the strongest anti-"hardcoded" proof mechanism for video + interview, and (b) a legitimately useful product feature (explainable, inspectable agent decisions) — directly supports the "AI judgment" and "would you trust it" judging criteria.

## 9b. PROOF REQUIREMENTS (what must be demonstrably real, not just claimed)
Applies to video submission and panel interview both.

1. **LLM reasoning** — must respond correctly to live/spontaneous, untested input (not just pre-written examples). Show raw JSON output, not just a chat bubble. Do not hide response latency — visible "agent thinking" delay reads as authentic, not as a flaw.
2. **ML risk model** — show real train/test split with precision/recall/confusion matrix, and ideally a calibration check (do 80%-predicted cases actually recover ~80% of the time). A single accuracy number is not sufficient proof.
3. **Rule engine / compliance logic** — deliberately trigger a case that should be blocked (e.g. 4th retry attempt) and show the system refusing, with a logged reason. Only showing successful actions looks curated.
4. **Audit trail** — trace one case fully, start to end, no gaps, in the video/interview. More convincing than aggregate dashboard numbers alone.
5. **Deliberate failure (Stage 5)** — show the actual broken state occurring on screen, then the specific safeguard catching it (e.g. idempotency check on duplicate webhook) — not just a claimed bullet point.
6. **Headline metrics must be reproducible** — every number stated in the pitch should be traceable to something inspectable live (a query, a script run), not just narrated from a slide.

**Video-specific proof tactics** (no live judge interaction possible):
- Type something spontaneous/improvised on camera, say so out loud before typing
- Show raw structured output on screen every time, not just clean chat UI
- Do not cut out latency — visible delay reads as real
- Briefly show the actual code (e.g. the LLM call function) tied to what just happened on screen
- Vary root causes and reply tones across the video — not one rehearsed flow repeated
- State once, factually, in narration: "this is a live API call responding to whatever I type, nothing here is pre-scripted"

**What does NOT need special proof:** data generation realism, frontend visual polish, exact simulated ₹ amounts. Effort should concentrate on proving intelligence, compliance, and honesty — not on polishing things nobody will doubt.

## 9c. REQUIRED AGENT LOGIC (build work — not just demo scenarios)
Three behaviors must be real, working logic in the rule engine / LLM layer, not just demo flourishes:

1. **Confidence threshold handling** — LLM intent extraction returns a confidence score. Below a set threshold (e.g. <0.6), the rule engine does NOT auto-close or auto-schedule — it flags the case for manual review instead. This must be real branching logic, not decorative.
2. **Self-consistency / mismatch check** — before acting on a customer reply, the rule engine cross-checks extracted intent against the case's existing root_cause data. If they conflict (e.g. root_cause = insufficient_funds but reply mentions an expired card), flag the inconsistency in the audit trail instead of proceeding blindly.
3. **`payment_method_updated` structured intent (Stage 3)** — Reply/intent parsing (Section 5's LLM row) recognizes `payment_method_updated` as one additional structured intent value, alongside existing categories (promise-to-pay, dispute, etc.). Recognizing this intent does not itself authorize any action — per Section 5's locked authority rule, the LLM extracts intent only; the existing `decide_action()` rule engine re-evaluates the next eligible action exactly as it does for any other extracted intent, subject to the same compliance checks (cooldown, retry limits, contact hours) already in force. No new pipeline, no new action type, no new decision authority.

## 9d. DEMO SCENARIOS (test cases against existing build — no new architecture)
These exercise the Live Agent Console with harder inputs. Not separate features — just things to type/trigger when demoing or recording:
- Multi-turn conversation (customer replies again after agent's response, e.g. requests installments)
- Customer contradicts or breaks an earlier promise-to-pay — system detects and escalates
- Ambiguous reply ("I'll try") — low confidence, routed to manual review (uses 9c-1)
- Mid-conversation language switching (English ↔ Hinglish)
- Cooldown enforcement demoed live — trigger a second retry within 24hrs, show it blocked with reason
- Off-topic/nonsensical reply — system declines to force a bad extraction, routes to general support instead

## 10. TECH STACK
- Backend: Python + FastAPI
- ML: scikit-learn / XGBoost
- LLM: Gemini API
- DB: SQLite
- Frontend: React + Tailwind + Recharts
- Synthetic data: Python + Faker
- Orchestration: custom state machine (not a heavy agent framework)
- GitHub: public repo required
- No live Razorpay API — all events simulated

## 11. BUILD ORDER (strict, no deviation)
1. Core loop, rules only — event → root cause → action → log. Working end-to-end by day 5.
2. ML layer — risk/recovery-probability model feeds rule engine, rules stay final authority.
3. LLM layer — message generation + reply/intent parsing, structured JSON output.
4. Dashboard — batch view, case detail, metrics panel.
5. Stress test — inject one deliberate edge case (duplicate/late webhook), show clean handling, document in audit trail. This becomes the "what broke" answer.

New ideas mid-build go on a "later" list — don't touch until stage 5 is stable.

**[DEFERRED] Deferred / later list (not part of current build order, not to be started before Stage 5 is stable, requires separate explicit approval to begin):**
- Optimal-retry-**timing** prediction (predicting *when* a retry is most likely to succeed, not just current probability-of-success). Would require: a new ML target definition, a timing representation (e.g. best-window feature/label), retraining and re-evaluating the model, and new integration surface in `decide_action()` beyond the current advisory-only `ml_recovery_probability` signal. Explicitly out of Stage 2's current scope.
- Multi-channel escalation (SMS/in-app layered on top of existing channel)
- LTV/customer-value-based segmentation
- Merchant-specific cohort/baseline layer
- Recovery strategy A/B experimentation
- "Pay and stay" / retention metric
- Multi-PSP / alternate gateway routing
- Literal autonomous "recovery-case agent" framing that would imply the LLM controls or executes actions (narrative language describing the existing pipeline is fine; an actual new decision authority is not — Section 5 stays locked as written)
- Any other adjacent feature not explicitly locked elsewhere in this document

## 12. DAY-BY-DAY PLAN
| Days | Task |
|---|---|
| 1–2 | Schema + synthetic data generator (150 records) |
| 3–5 | Rule engine, core loop end-to-end |
| 6–7 | ML risk model + integration |
| 8–9 | LLM messaging + reply parsing |
| 10–11 | Dashboard |
| 12 | Failure injection + audit trail proof |
| 13 | Testing, bug fixes, metrics check |
| 14 (land by Sept 3–4) | Repo cleanup, README, pitch video, final review |

## 13. FAILURE RECOVERY LOG PROTOCOL
Whenever something breaks, we get stuck, or we hit a real problem during the build:
1. Work through it live in that chat until resolved.
2. Once resolved, confirm with the user before writing anything.
3. On confirmation, generate a standalone `.md` file documenting: what broke, why, what was tried, what finally worked.
4. Save it — this becomes the material for the application form's "what broke, and how you got out" field.
Do this every time a real failure occurs, not just once — multiple logs are fine, pick the strongest one(s) at submission time.

## 14. USER WORKING PREFERENCES
- No documentation/explanatory files/guides unless explicitly requested
- Short, direct, point-to-point answers
- No jargon-heavy paragraphs
- No mid-build scope changes — flag new ideas, don't act on them until locked stages complete
- User has RAG pipeline experience + heavy AI-assisted coding, limited independent tech-stack knowledge — plans must stay explicit and unambiguous

---