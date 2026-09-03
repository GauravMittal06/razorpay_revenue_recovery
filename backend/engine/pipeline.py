"""
pipeline.py -- Phase 5 / W7. THE shared recovery pipeline.

    classify -> optimize -> authorize -> execute -> message

One function, called by all three entry points: core_loop.py (batch),
trigger_event.py (live console) and handle_customer_reply.py (reply). The
Phase 5 gate requires this be verified *structurally* -- "a single shared
function is called by all three entry points", not merely that the three
produce matching output -- so that a future change to one cannot silently
diverge from the others. Before W7 the three sequenced the same four calls
independently, and they had already drifted: see CLASSIFY, below.


WHAT LIVES HERE, AND WHAT DELIBERATELY DOES NOT
    Here: the five pipeline steps and nothing else.

    Not here, and left in the callers on purpose:

    * `trigger_event`'s argument validation, its duplicate-event dedup
      short-circuit, and its opportunity/payment INSERTs. The dedup path must
      return BEFORE the pipeline runs -- a replayed upstream event must not
      execute an action a second time.
    * `handle_customer_reply`'s conversation-history fetch (which must happen
      before the new message is inserted, so a reply is never part of its own
      history), its intent parse, and its fail-closed message persist.
    * `handle_customer_reply`'s try/except that converts an unexpected failure
      into status="engine_error". That is its own contract; hoisting it here
      would impose it on the other two and change their behaviour, since they
      currently let exceptions propagate.

    The dispatcher (dispatch_scheduled.py) is NOT a caller. It advances an
    already-decided action and must never call execute_action(), which is not
    idempotent at the call level. Enforced by a raise in phase5_config.


CLASSIFY -- the drift W7 had to resolve (ruling W1, 2026-09-04)
    The three entry points disagreed about what feeds classify()'s
    `error_reason`: core_loop passed the latest payment's error_reason,
    handle_customer_reply passed the opportunity's stored root_cause, and
    trigger_event passed the root_cause it had just been given. The
    divergence was introduced by the Phase 1 schema split (7c9fc24) -- before
    it, both loop entry points called the identical `classify(payment)`.

    Unified on the opportunity's stored root_cause, because classify()'s
    output is a COMPLIANCE INPUT on the reply path: decide_action()'s
    intent-mismatch gate compares classification["root_cause"] against the
    LLM's mentioned_reason and can return allowed=False /
    flagged_manual_review. That gate's own message says "Extracted intent
    conflicts with STORED root_cause", wording older than the split
    (e690789). The payment's error_reason remains a fallback when root_cause
    is NULL. Measured identical on all 150 seeded opportunities; structurally
    identical because the only two writers of a payments row both set
    error_reason from the opportunity's own root_cause.


THE LOCK BOUNDARY
    Ranking runs OUTSIDE the lock; authorize+execute run INSIDE it; message
    delivery runs OUTSIDE it.

    The optimizer must never be inside: measured p50 644 ms against a ~6 ms
    hold for decide+execute, a ~110x increase that takes the number of
    workers able to queue against db.BUSY_TIMEOUT_MS from ~850 to about 7.
    A ranking computed outside the lock can be stale, and that is safe by
    construction -- decide_action() re-adjudicates every candidate against
    fresh state inside the lock. Stale ranking can cost optimality; it cannot
    cost compliance.

    Which entry points lock at all is a declared table
    (phase5_config.ENTRY_POINTS_USING_OPPORTUNITY_LOCK), not a per-call
    boolean, so a caller cannot opt out by accident. `trigger_event` is
    deliberately absent: it mints a fresh opportunity_id per call, so
    concurrent calls touch different rows, and duplicate delivery is already
    guarded by the UNIQUE index on ingestion_event_id.


DELIVERY
    This is the ONLY place deliver_recovery_message() is called from the
    recovery path, and it always names the execution via `decision_id`.
    Delivery fails closed without it (ruling A7): a scheduled action must not
    be announced to the customer before it fires, and an unverifiable
    delivery must not guess. Consolidating to one call site is what stops a
    future edit from dropping the argument at one of three places.
"""

from contextlib import nullcontext

from backend.engine import phase5_config as _phase5
from backend.engine.classify import classify
from backend.engine.decide_action import decide_action
from backend.engine.deliver_message import deliver_recovery_message
from backend.engine.execute_action import execute_action
from backend.engine.opportunity_lock import opportunity_lock


def classify_root_cause(opportunity: dict, latest_payment: dict = None):
    """
    The input to classify()'s `error_reason` parameter. Ruling W1.

    The opportunity's stored diagnosis, falling back to the latest payment
    attempt's error_reason only when the opportunity carries none.
    """
    root_cause = (opportunity or {}).get("root_cause")
    if root_cause is not None:
        return root_cause
    return (latest_payment or {}).get("error_reason")


def _lock_for(entry_point: str, conn):
    """
    The lock this entry point runs its decide-then-execute under.

    opportunity_lock is adopted UNCHANGED; entry points outside the declared
    table get a nullcontext so the call shape stays identical either way.
    """
    if entry_point in _phase5.ENTRY_POINTS_USING_OPPORTUNITY_LOCK:
        return opportunity_lock(conn)
    return nullcontext()


def _ranked_candidates(conn, opportunity: dict, entry_point: str):
    """
    The optimizer's full ranked list, or None to run the hardcoded path.

    Called OUTSIDE the lock -- see the module docstring. Imported lazily so
    that an entry point with the optimizer disabled (all four, by default)
    does not pay the ML stack's import cost, and so api/server.py's startup
    is unaffected.

    Fail-soft by design: if ranking errors or produces nothing, the pipeline
    falls back to the hardcoded path rather than failing the opportunity.
    The optimizer is advisory and holds no compliance authority, so its
    absence can cost optimality but never correctness.
    """
    if not _phase5.OPTIMIZER_ENABLED_BY_ENTRY_POINT.get(entry_point, False):
        return None

    from backend.engine.optimize import optimize_opportunity

    result = optimize_opportunity(conn, opportunity["opportunity_id"])
    if result.get("error"):
        return None
    return result.get("ranked") or None


def run_recovery_pipeline(opportunity: dict, conn, *, entry_point: str,
                          latest_payment: dict = None,
                          extracted_intent: str = None,
                          intent_confidence: float = None,
                          mentioned_reason: str = None,
                          dispute_flag: bool = False,
                          as_of: int = None) -> dict:
    """
    Run one opportunity through classify -> optimize -> authorize -> execute
    -> message.

    Returns:
      {"classification": dict, "ranked": list|None, "decision": dict,
       "execution_result": dict, "delivery": dict}

    `entry_point` must be one of
    phase5_config.ENTRY_POINTS_USING_SHARED_PIPELINE. It is required and
    keyword-only because it selects the lock policy and the optimizer policy;
    defaulting it would let a new caller silently inherit another entry
    point's concurrency behaviour.

    The intent arguments are meaningful only for the reply path; the other
    two leave them at their defaults, which is exactly what they passed
    before unification.

    `as_of` is the clock the contact window is judged against (ruling A2).
    None means "use the opportunity's created_at", which is what all three
    entry points want -- they act now, on an event whose own timestamp is the
    right reference. It is threaded through so the shared pipeline can
    express a dispatch-time revalidation without a second code path.
    """
    if entry_point not in _phase5.ENTRY_POINTS_USING_SHARED_PIPELINE:
        raise ValueError(
            f"entry_point {entry_point!r} is not one of the declared shared "
            f"pipeline entry points "
            f"{list(_phase5.ENTRY_POINTS_USING_SHARED_PIPELINE)}")

    # 1. classify
    classification = classify(
        opportunity["event_type"],
        classify_root_cause(opportunity, latest_payment),
    )

    # 2. optimize -- OUTSIDE the lock, always.
    ranked = _ranked_candidates(conn, opportunity, entry_point)

    # 3. authorize + execute -- INSIDE the lock, indivisibly. decide_action()
    #    reads cooldown and execute_action() acts on it; splitting them lets
    #    two workers both read "no recent contact" and both fire.
    with _lock_for(entry_point, conn):
        decision = decide_action(
            opportunity, classification, conn,
            latest_payment=latest_payment,
            extracted_intent=extracted_intent,
            intent_confidence=intent_confidence,
            mentioned_reason=mentioned_reason,
            dispute_flag=dispute_flag,
            ranked_candidates=ranked,
            as_of=as_of,
        )
        execution_result = execute_action(opportunity, decision, conn)

    # 4. message -- OUTSIDE the lock. It is an outbound side effect on an
    #    already-committed decision and it calls the LLM; holding the write
    #    lock across it would serialise the whole batch on message
    #    generation. decision_id names the execution this delivery belongs
    #    to, without which delivery fails closed.
    delivery = deliver_recovery_message(
        opportunity, classification, decision, conn,
        latest_payment=latest_payment,
        decision_id=execution_result["decision_id"],
    )

    return {
        "classification": classification,
        "ranked": ranked,
        "decision": decision,
        "execution_result": execution_result,
        "delivery": delivery,
    }
