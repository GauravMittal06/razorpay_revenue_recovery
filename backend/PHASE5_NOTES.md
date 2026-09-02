# Phase 5 Notes — Rule Engine & Bounded Executor

Working record, written as the phase proceeds. `PHASE5_HANDOFF.md` is a
separate document produced at sign-off and draws on this one.

Read with `EXECUTION_PLAN.md`, `SoT.md`, `STATE_AND_DECISIONS.md`,
`Phase_Acceptance_Test_Gates.md`, `backend/PHASE4_HANDOFF.md`.

---

## 0. Document drift discovered in Phase 5

Two places where the project's own specification documents disagree with each
other. Both are recorded here rather than silently resolved, because in each
case a reader consulting only one document would reach a different conclusion
about what the system is supposed to do.

### 0.1 `method_change` — SoT ↔ EXECUTION_PLAN

| Document | Says |
|---|---|
| `SoT.md:63` | lists "alternate payment method" among the rule engine's dispatchable tools |
| `EXECUTION_PLAN.md:206` | "structurally excludes autonomous payment-method switching: there is no code path anywhere in the executor capable of dispatching a method change" |

**Ruling applied in Phase 5:** `EXECUTION_PLAN.md` governs as the operative
spec — method change is evaluable, never executable. `SoT.md` is the earlier,
more general description; the EXECUTION_PLAN statement is later and more
specific, and is reinforced by a permanent invariant
(`EXECUTION_PLAN.md:301`, "Payment-method changes are never dispatched
autonomously, structurally").

**Left unresolved on purpose:** `SoT.md:63` has not been amended. Anyone
reading SoT alone will still believe the capability is dispatchable. Worth an
explicit amendment at Phase 5 sign-off or later.

**Structural note that matters more than the drift.** There is no
`method_change` action type anywhere in the system. The candidate vocabulary
is `do_nothing | retry | reminder | payment_link | escalate`
(`data_factory/candidate_generation.py:32`). A method change is represented as
`action_type="retry"` carrying a `method` different from the opportunity's
current one, flagged on the candidate dict as `method_changed: True`
(`candidate_generation.py:154`), and emitted only for root causes in
`METHOD_CHANGE_RELEVANT_ROOT_CAUSES` (`expired_card`,
`authentication_failed`).

The consequence is that the boundary currently holds *by absence, not by
enforcement*: `decide_action()` never reads a candidate today — it emits a
bare action string — so no `method` attribute exists anywhere near the
executor. **The moment Phase 5 passes ranked candidates into
`decide_action()`, that attribute becomes reachable, and a method-change retry
would be dispatchable as an ordinary retry.** Phase 5 must therefore construct
the exclusion in the same change that creates the exposure. It also means the
existing gate test, which substring-searches source for the literal
`"method_change"`, is searching for a token that structurally cannot appear —
see section 2.

### 0.2 `blocked_max_retries` — EXECUTION_PLAN ↔ STATE_AND_DECISIONS

| Document | Says |
|---|---|
| `EXECUTION_PLAN.md:103` (before Phase 5) | includes `blocked_max_retries` in the closed outcome vocabulary |
| `STATE_AND_DECISIONS.md:109-111` | "`blocked_max_retries` explicitly dropped", with a recorded rationale and a "what breaks if reversed" clause |

Unlike 0.1, the later and more specific document here is
`STATE_AND_DECISIONS.md`, not `EXECUTION_PLAN.md` — so the precedent from 0.1
does not transfer, and the conflict was escalated rather than resolved by
analogy. Resolved by explicit ruling in favour of
`STATE_AND_DECISIONS.md`; `EXECUTION_PLAN.md:103` was amended to match (that
one line only). Full analysis in section 1.

Also noted: the enumeration at `STATE_AND_DECISIONS.md:109` is itself
partially stale — it names five values and omits `flagged_manual_review`,
which the later Stage 3 LLM confidence gate added. Its *ruling* on
`blocked_max_retries` stands; its *list* does not.

---

## 1. `blocked_max_retries` — analysis and resolution

### 1.1 What was found

`blocked_max_retries` was a declared member of the closed compliance
vocabulary with **no producer anywhere, in any commit in the project's
history**. Checked at every revision:

    294f517  emits=0  mentions=1        078ca79  emits=0  mentions=1
    b512628  emits=0  mentions=1        7c9fc24  emits=0  mentions=1
    e690789  emits=0  mentions=1        866e478  emits=0  mentions=1

The single mention in each revision is `decide_action()`'s docstring. At the
first commit (`294f517:118`) the max-retries branch already returned
`action_type="stop", outcome="executed"`, identical to today.

### 1.2 What the max-retries branch actually does

`decide_action.py`:

```python
if contact_count >= MAX_RETRIES:
    return {"action_type": "stop", "allowed": True,
            "reasoning": f"Max {MAX_RETRIES} contact attempts reached. ...",
            "outcome": "executed", "triggered_by": "rule"}
```

`execute_action.py` then treats `stop` as a terminal business resolution:
`status='stopped'`, `resolved_at`, `recovered_bool=0`,
`partial_recovery_amount=0`, `resolution_type='stopped'`.

### 1.3 Is `stop` doing double duty?

Today, no. `action_type == "stop"` appears twice in `decide_action()`:

- the `already_stopped` guard — `allowed=False`,
  `outcome='blocked_already_stopped'`. A *reflection* of an existing terminal
  state; `execute_action()` writes nothing for it.
- the max-retries branch — `allowed=True`, `outcome='executed'`. **The sole
  producer.**

Nothing in `api/`, `llm/`, or `engine/` elsewhere produces a `stop` decision,
and the optimizer never proposes one (`stop` is not in the candidate
vocabulary). So `WHERE action_type='stop' AND outcome='executed'` is currently
an exact, unambiguous query for "terminated by the retry ceiling". No
information is lost by the absence of a distinct outcome code.

**Forward-looking risk, and why it shapes the W3 design.** The
EXECUTION_PLAN's Phase 5 executable vocabulary includes `stop`. If the
fallthrough loop emits `stop` when it exhausts all candidates, `stop` acquires
a second meaning — "no compliant candidate was available" — and that query
silently becomes wrong, conflating budget-exhausted with nothing-to-do. The
double duty does not exist today, but Phase 5 is the phase that could create
it. **W3 therefore routes candidate exhaustion to `flagged_manual_review`, not
to `stop`.** That choice is load-bearing, not stylistic.

### 1.4 Why the value was removed rather than the branch added

Adding a real `blocked_max_retries` branch would be a live regression, not a
clarification. By the `allowed == (outcome == 'executed')` invariant — enforced
behaviourally at `tests/test_compliance_regression.py:522` — such a branch must
carry `allowed=False`. `execute_action()` then skips its
`if decision["outcome"] == "executed":` block entirely, so:

- no `recovery_executions` row is written,
- **the opportunity is never closed**,
- `core_loop` re-selects it (`WHERE status IN ('open','recovering')`) on the
  next cycle, `contact_count` is still ≥ `MAX_RETRIES`, and it re-emits the
  same outcome **every cycle, indefinitely**.

Making it safe would require a blocked-prefixed outcome that nonetheless
performs a terminal state transition — breaking both the "blocked outcomes
have no side effects" semantic and the `allowed`/`outcome` agreement
invariant. That is exactly what `STATE_AND_DECISIONS.md:111` means by
"would require restructuring the stop-transition logic".

There is also a semantic argument: the other five `blocked_*` codes all mean
*"not now, the opportunity stays open, try later."* Max-retries means *"never
again, the opportunity is closed."* Filing a permanent termination under the
same prefix makes the vocabulary less auditable, not more.

And doing it inside Phase 5 would deliberately break the W1 golden corpus on
two scenarios, contradicting this phase's own backward-compatibility gate.

### 1.5 Dependency sweep before removal

No downstream consumer referenced it as a distinct bucket:

- **Specs:** `SoT.md` 0 occurrences; `Phase_Acceptance_Test_Gates.md` 0. The
  entire `blocked_*` vocabulary appears on exactly one line per document
  (`EXECUTION_PLAN.md:103`, `STATE_AND_DECISIONS.md:109`). No Phase 6/7/8
  section names a compliance outcome bucket at all — every "outcome" mention
  there refers to *business* outcome (recovered / partially recovered / lost).
- **API:** the only hardcoded outcome literal anywhere is
  `flagged_manual_review` (`api/queries.py:191,198`). `get_cases()` takes
  `outcome` as an opaque pass-through query param.
- **Frontend:** the vocabulary is enumerated in exactly two places —
  `frontend/src/components/FiltersBar.jsx:3-11` (filter dropdown) and
  `frontend/src/statusColors.jsx:15-22` (`OUTCOME_STYLES`) — and **both omit
  `blocked_max_retries`**, listing precisely the six emitted values.
- **Tests:** both consumers (`test_compliance_regression.py:515`,
  `test_phase0_bootstrap.py:325`) are membership/subset checks. No exact-set or
  length assertion exists, so removing an unemitted value can only tighten
  them.
- **Data:** `recovery_decisions` is empty; 0 rows carry the value.

Three independent sources agreed the real vocabulary is **six**, against seven
declared: the W1 golden corpus (measured across 25 scenarios), the frontend's
hand-written enumerations, and the `STATE_AND_DECISIONS.md:109` ruling. The
frontend was in fact the most accurate enumeration in the project — more so
than `STATE_AND_DECISIONS.md:109`, which predates `flagged_manual_review`.

### 1.6 What changed

| File | Change |
|---|---|
| `backend/db/db.py` | `blocked_max_retries` removed from `DECISION_OUTCOMES`, with the rationale recorded inline |
| `backend/engine/decide_action.py` | removed from the docstring's `outcome` type-union |
| `EXECUTION_PLAN.md:103` | removed from the closed-vocabulary table (that one line only) |
| `backend/tests/test_phase5_regression.py` | two docstrings corrected; assertions unchanged |

**No behaviour changed.** `decide_action()`'s logic is untouched; the W1
golden corpus passes unmodified. `test_blocked_max_retries_remains_unreachable`
is deliberately retained — the removal is what makes accidental reintroduction
plausible, since nothing now rejects the string at write time.

---

## 2. Open obligations carried into later steps

- **Tighten `test_method_change_has_no_reachable_executor_path`.** It
  substring-matches `"method_change"` inside the legitimate `"method_changed"`
  feature key, producing 14 false-positive offenders across `data_factory/`
  and `ml/`. Fix is a word-boundary match
  (`(?<![0-9A-Za-z_])method_change(?![0-9A-Za-z_])`). But per section 0.1 the
  tightened test still proves nothing, because no such token can exist — so it
  is retained only as a cheap tripwire, and the boundary is re-verified for
  real by new structural tests in W8.
- **Ruling 10 — unaddressed.** Whether re-implementing the retracted G7 test
  (`test_higher_true_incremental_value_ranks_above_lower`) is Phase 5 scope has
  not been ruled on. Untouched; still one of the 16 known failures.
- **`do_nothing` would fabricate an execution row.** A `do_nothing` decision
  reaching `execute_action()` hits the `EXECUTION_STATE_MAP` default and
  INSERTs a `recovery_executions` row with state `executed`, asserting a
  dispatch that never happened. Fix belongs in W5.
- **`"executed"` is a member of both closed vocabularies** —
  `DECISION_OUTCOMES` (compliance) and `EXECUTION_STATES` (lifecycle). Live
  collision; the W8 structural tests pin it rather than rename it.
- **`allowed` is read by no production code.** `execute_action()` branches on
  `outcome`; `allowed` is read only by tests, tied to `outcome` by convention
  asserted at `test_compliance_regression.py:522`. So "only `allowed: True`
  reaches the executor" is proven through a proxy field, not the permission
  bit. Proposed to make mechanical in W3/W8, not yet ruled on.
- **Latency.** p95 measured at 747.9 / 737.5 / 724.3 ms across three runs
  against the declared 250 ms budget (`optimizer_config.LATENCY_BUDGET_MS`).
  Not met, unchanged from Phase 4, and no Phase 5 budget was invented to
  replace it.
