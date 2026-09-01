# Phase 4 → Phase 5 Handoff

Standalone; read with `SoT.md`, `EXECUTION_PLAN.md`, `STATE_AND_DECISIONS.md`,
`FILE_INVENTORY.md`, `backend/PHASE4_NOTES.md`, `backend/PHASE3_HANDOFF.md`.

## 1. Status: CONDITIONALLY CLEARED — 13 of 14 gates

One outstanding disclosed gap: **G10 latency**. G7 (ranking correctness)
passes at 0.966 / 0.967 on frozen seed-42 / seed-43 Phase 3 data against a
0.90 bar; an earlier recorded G7 failure at 0.389 was **retracted** as a
Phase 4 test-methodology error (`PHASE4_NOTES.md` §8.0).

## 2. What Phase 5 consumes

`backend/engine/optimize.py`:

```python
optimize_opportunity(conn, opportunity_id, now=None, persist=True) -> dict
```

Returns `{"error", "opportunity_id", "ranked", "unscored", "pruned",
"candidate_count", "latency_ms"}`. `ranked` is descending by `predicted_eiv`
with a deterministic tiebreak, `rank` 1-indexed, `do_nothing` always present
and always exactly `0.0`.

    EIV = expected_recovered_amount(candidate)
        − expected_recovered_amount(do_nothing)
        − intervention_cost(candidate)

Phase 5 passes the **full ranked list** to `decide_action()`, not just the
top pick, so a blocked top candidate can fall through to the next compliant
one. The optimizer is advisory: it never sets `allowed`, never marks
`selected` (every row it writes carries `selected=0` — Phase 5 sets it after
adjudication), and writes only `recovery_candidates`.

## 3. What Phase 5 must carry forward

**Rupee-space ranking sensitivity — read this before trusting the order.**
Phase 5's rule engine consumes optimizer output ranked in **rupee space**
(EIV). That ranking has a **measured ~16% pair-order sensitivity** (997 of
6,140 live candidate pairs) to candidate-dependent noise in the model's
`E[amount|recovered]` head — noise the generator does not have.
**Not a blocker**, but Phase 5 must **not** assume the rupee-space ranking is
more reliable than the underlying probability signal; where the two
disagree, the probability ordering is the better-evidenced one. Full entry:
`PHASE4_NOTES.md` §8.6.

**Near-tie confidence disclosure.** Every ranked row carries
`eiv_confidence` (`high`/`low`), `eiv_confidence_reason`
(`near_tie` / `phase3_flagged_bucket` / both) and `eiv_gap_to_next`. This is
**display and downstream-consumption metadata only** — it does not change the
ranking and must not become a compliance input. On the real corpus it comes
out 18 high / 1230 low, because the model is saturated at p50 = 0.909 and
most candidate pairs are within noise of each other.

**`method_change` is evaluable, never executable.** Alternate-payment-method
candidates are scored and ranked normally. Phase 5 must either fall through
to the next executable-and-compliant candidate or route to manual review —
never dispatch one. The executor has no code path for it and must not gain
one.

**Latency.** ~640 ms per opportunity, p95 ~858 ms, against a declared 250 ms
budget that is **NOT MET**. 99.7% is single-row model inference; batching was
measured at 6.6× but requires touching frozen `ml/inference.py` and re-running
Phase 3's parity gate. Phase 5 should not wire the optimizer into a
latency-sensitive synchronous path without addressing this.

**Network health is unavailable at serving time.** `bank`/`psp` do not exist
on the live `payments` table, so `network_health_known = 0.0` on every live
scoring. Phase 3 parity-tested this regime; it is safe, and it was tested and
shown *not* to be the cause of any ranking shortfall. Phase 6/7 closure item.

## 4. Frozen inputs — reuse unmodified, do NOT edit in Phase 5

| Path | Role |
|---|---|
| `backend/engine/optimize.py` | The optimizer. Advisory only; zero execution authority, enforced by 6 mechanical checks in `test_permanent_gates.py`. |
| `backend/engine/optimizer_config.py` | Declared bounds: `MAX_CANDIDATES`, `NEAR_TIE_BAND_FRACTION`, `LATENCY_BUDGET_MS`, `PHASE3_LOW_CONFIDENCE_COMBINATIONS`. |
| `backend/engine/intervention_cost.py` | The cost term — synthetic placeholders; the only non-model term in EIV. |
| `backend/data_factory/candidate_generation.py` | Still frozen. Phase 5 must not add eligibility rules here; compliance belongs to the rule engine. |
| `backend/ml/inference.py`, `outcome_features.py` | Still frozen. Still the only scoring path. |

## 5. Known-failing test Phase 5 will see

`test_higher_true_incremental_value_ranks_above_lower` still encodes the
retracted methodology (probability ground truth vs rupee model output, on
constructed contexts) and therefore still fails. Known cause, recorded in
`PHASE4_NOTES.md` §8.3. Re-implementing it is an open obligation, not a
regression.

Fourteen further suite failures pre-date Phase 4 and are unchanged; they were
verified present in the main checkout before Phase 4 began. One of them,
`test_method_change_has_no_reachable_executor_path`, is a **false positive** —
it substring-matches `"method_change"` inside the legitimate
`"method_changed"` feature key in data-factory modules. Worth tightening in
Phase 5, since Phase 5 re-verifies that same boundary.

## 6. Integrity statement

No threshold was loosened in Phase 4 to obtain a pass. G7 moved from FAIL to
PASS on corrected evidence, not a moved bar; the retracted claim and the
reason it was wrong are recorded in full rather than deleted. One hypothesis
was tested and refuted, one new model property was found and disclosed as
known-and-unfixed, and two self-inconsistencies in Phase 4's own work were
found and recorded rather than silently amended.

**Phase 4 sign-off is a separate step. Phase 5 has not been started.**
