"""
Phase 6 / X3 -- control suppression, and the proof it is structural.

"Control opportunities skip the optimizer call" would be a bolt-on flag. What
is actually built is a branch inside `decide_action()` -- the sole authority
that can set `allowed: True` -- placed before every other branch. These tests
exist to show that placement is load-bearing rather than incidental:

* it covers the SHARED PIPELINE (all three entry points) and the DISPATCHER's
  revalidation of an already-scheduled action, because both call the same
  function. A check in pipeline.py would leave the second uncovered.
* it is FIRST, so no earlier branch can return before it.
* removing it is observable -- there is a mutation test that deletes the gate
  and asserts the counterfactual probes go non-zero. A gate never seen to fail
  is not evidence.

Backward compatibility is the other half. Every pre-Phase-6 opportunity has no
assignment row, and an unassigned opportunity is deliberately NOT suppressed,
so decisions for the whole seeded world must be byte-identical to before.
"""

import time

import pytest

from backend.db import db
from backend.engine import phase6_config as cfg
from backend.engine.classify import classify
from backend.engine.decide_action import decide_action
from backend.tests.conftest import make_opportunity

DAY = 86400
HOUR = 3600


def _assign(conn, opportunity_id, group, assigned_at=None):
    """Force a specific arm. Real assignment is random by construction, so a
    test that needs a known arm writes the row directly."""
    conn.execute(
        'INSERT INTO experiment_assignment '
        '(opportunity_id, "group", assigned_at, assignment_method) '
        "VALUES (?, ?, ?, ?)",
        (opportunity_id, group,
         int(time.time()) if assigned_at is None else assigned_at,
         cfg.assignment_method_record()),
    )
    conn.commit()


def _decide(conn, opportunity, **kwargs):
    classification = classify(opportunity["event_type"],
                              opportunity.get("root_cause"))
    return decide_action(opportunity, classification, conn, **kwargs)


# --------------------------------------------------------------------------
# The verdict itself
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.control_suppression")
def test_a_control_opportunity_is_suppressed(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_0001")
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)

    decision = _decide(seeded_db, opp)
    assert decision["allowed"] is False
    assert decision["outcome"] == cfg.SUPPRESSION_OUTCOME
    assert decision["action_type"] is None, (
        "naming an action here would put one the system never considered "
        "into the audit trail")
    assert decision["triggered_by"] == "rule"
    assert decision["reasoning"], "no action may be declined without a reason"


@pytest.mark.gate("phase6.control_suppression")
def test_the_suppression_reason_names_the_assignment_that_caused_it(seeded_db):
    """The invariant is that every declined action is logged with a reason.
    A reason that did not identify the assignment would not let an auditor
    check the decision against the experiment record."""
    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_0002")
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP, assigned_at=12345)

    reasoning = _decide(seeded_db, opp)["reasoning"]
    assert "12345" in reasoning
    assert cfg.ASSIGNMENT_METHOD in reasoning


@pytest.mark.gate("phase6.control_suppression")
def test_the_suppression_outcome_is_in_the_closed_vocabulary(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_0003")
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)
    assert _decide(seeded_db, opp)["outcome"] in db.DECISION_OUTCOMES


@pytest.mark.gate("phase6.control_suppression")
def test_a_treatment_opportunity_is_untouched(seeded_db):
    """Assignment to treatment must change nothing at all -- otherwise the two
    arms differ by more than the intervention and the experiment measures the
    wrong thing."""
    control_free = make_opportunity(seeded_db, opportunity_id="opp_sup_0004")
    baseline = _decide(seeded_db, control_free)

    assigned = make_opportunity(seeded_db, opportunity_id="opp_sup_0005")
    _assign(seeded_db, assigned["opportunity_id"], cfg.TREATMENT_GROUP)
    treated = _decide(seeded_db, assigned)

    assert (treated["action_type"], treated["allowed"], treated["outcome"]) == \
           (baseline["action_type"], baseline["allowed"], baseline["outcome"])


@pytest.mark.gate("phase6.control_suppression")
def test_an_unassigned_opportunity_is_not_suppressed(seeded_db):
    """
    The deliberate fail-OPEN. Failing closed would freeze every pre-Phase-6
    opportunity, none of which was ever randomized.
    """
    assert cfg.UNASSIGNED_IS_SUPPRESSED is False
    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_0006")
    decision = _decide(seeded_db, opp)
    assert decision["outcome"] != cfg.SUPPRESSION_OUTCOME
    assert decision["allowed"] is True


# --------------------------------------------------------------------------
# Suppression beats every other branch, because it runs before them
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scenario,setup", [
    ("already_stopped", lambda c, o: _hist(c, o, "stop")),
    ("already_escalated", lambda c, o: _hist(c, o, "escalate")),
])
@pytest.mark.gate("phase6.control_suppression")
def test_suppression_precedes_the_terminal_branches(seeded_db, scenario, setup):
    """
    Not about which verdict is 'better'. If a branch below could return first,
    then whether a control opportunity is suppressed would depend on its
    history -- and suppression would be a property of branch ordering rather
    than of the arm it is in.
    """
    opp = make_opportunity(seeded_db, opportunity_id=f"opp_sup_pre_{scenario}")
    setup(seeded_db, opp["opportunity_id"])
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)
    assert _decide(seeded_db, opp)["outcome"] == cfg.SUPPRESSION_OUTCOME


def _hist(conn, opportunity_id, action_type):
    from backend.tests.conftest import insert_decision
    insert_decision(conn, opportunity_id, action_type, outcome="executed")


@pytest.mark.gate("phase6.control_suppression")
def test_the_gate_is_structurally_first_in_the_function_body():
    """
    The behavioural tests above sample scenarios; this asserts the ordering
    property itself, so a branch added above the gate in future fails here
    rather than silently un-suppressing whichever cases it happens to catch.
    """
    import ast
    import inspect

    from backend.engine import decide_action as module

    source = inspect.getsource(module.decide_action)
    tree = ast.parse(source.lstrip())
    fn = tree.body[0]

    def _returns_a_verdict(node):
        """Does this statement return a dict literal carrying `allowed`?"""
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Return) or not isinstance(sub.value, ast.Dict):
                continue
            keys = [k.value for k in sub.value.keys
                    if isinstance(k, ast.Constant)]
            if "allowed" in keys:
                return True
        return False

    def _recurses(node):
        """Does it call decide_action() again? The optimizer header does, and
        that recursion re-enters this same body with ranked_candidates=None --
        so it reaches the gate rather than bypassing it. It is the ONE
        statement permitted to precede the gate."""
        return any(isinstance(sub, ast.Call)
                   and getattr(sub.func, "id", None) == "decide_action"
                   for sub in ast.walk(node))

    gate_index = None
    offenders = []
    for i, node in enumerate(fn.body):
        if gate_index is None and "SUPPRESSION_OUTCOME" in ast.dump(node):
            gate_index = i
            continue
        if gate_index is None and _returns_a_verdict(node) and not _recurses(node):
            offenders.append(i)

    assert gate_index is not None, \
        "the suppression gate is not a top-level statement of decide_action()"
    assert not offenders, (
        "a verdict-returning branch precedes the holdout gate at statement "
        f"index {offenders}; suppression must not depend on branch order")

    # The permitted exception must genuinely be the recursion, not any
    # statement that happens to mention the name.
    preceding = [n for n in fn.body[:gate_index] if _returns_a_verdict(n)]
    assert all(_recurses(n) for n in preceding), \
        "a non-recursing verdict branch precedes the gate"
    assert len(preceding) <= 1, \
        f"expected at most the optimizer recursion before the gate, got {len(preceding)}"


# --------------------------------------------------------------------------
# Nothing downstream happens for a control opportunity
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.control_suppression")
def test_control_produces_a_decision_row_but_no_execution(seeded_db):
    """
    The decision row is required -- "every action the system takes or declines
    to take is logged with a reason" -- and is what gives the counterfactual
    gate something affirmative to inspect. The execution row must not exist:
    nothing fired.
    """
    from backend.engine.execute_action import execute_action

    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_0007")
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)
    decision = _decide(seeded_db, opp)
    execute_action(opp, decision, seeded_db)

    decisions = seeded_db.execute(
        "SELECT outcome FROM recovery_decisions WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchall()
    assert [r["outcome"] for r in decisions] == [cfg.SUPPRESSION_OUTCOME]

    executions = seeded_db.execute(
        "SELECT COUNT(*) FROM recovery_executions re "
        "JOIN recovery_decisions rd ON rd.decision_id = re.decision_id "
        "WHERE rd.opportunity_id = ?", (opp["opportunity_id"],)).fetchone()[0]
    assert executions == 0


@pytest.mark.gate("phase6.control_suppression")
def test_control_opportunity_status_is_not_advanced(seeded_db):
    from backend.engine.execute_action import execute_action

    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_0008",
                           status="open")
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)
    execute_action(opp, _decide(seeded_db, opp), seeded_db)

    status = seeded_db.execute(
        "SELECT status FROM opportunities WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone()[0]
    assert status == "open"


@pytest.mark.gate("phase6.control_suppression")
def test_end_to_end_control_gets_no_execution_message_or_candidate(seeded_db):
    """Through the real entry point, not a constructed decision."""
    from backend.engine.trigger_event import trigger_event

    control_id = None
    for i in range(40):
        result = trigger_event("payment_failed", 5000 + i, seeded_db,
                               root_cause="insufficient_funds")
        if result["assignment"]["group"] == cfg.CONTROL_GROUP:
            control_id = result["opportunity"]["opportunity_id"]
            assert result["decision"]["outcome"] == cfg.SUPPRESSION_OUTCOME
            break
    assert control_id, "no control opportunity in 40 real trigger_event calls"

    for table, sql in (
        ("executions",
         "SELECT COUNT(*) FROM recovery_executions re JOIN recovery_decisions rd "
         "ON rd.decision_id = re.decision_id WHERE rd.opportunity_id = ?"),
        ("messages", "SELECT COUNT(*) FROM messages WHERE opportunity_id = ?"),
        ("candidates",
         "SELECT COUNT(*) FROM recovery_candidates WHERE opportunity_id = ?"),
    ):
        n = seeded_db.execute(sql, (control_id,)).fetchone()[0]
        assert n == 0, f"control opportunity accrued {n} {table}"


# --------------------------------------------------------------------------
# The dispatcher path -- the reason the gate is not in pipeline.py
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.control_suppression")
def test_a_scheduled_action_for_a_control_opportunity_is_abandoned(seeded_db):
    """
    THE case that decides where the gate lives. The dispatcher does not go
    through run_recovery_pipeline() -- it revalidates by calling
    decide_action() directly. A suppression implemented in pipeline.py would
    leave this path open, and an action scheduled before assignment would
    still fire afterwards.
    """
    from backend.engine.dispatch_scheduled import run_dispatch_cycle
    from backend.tests.conftest import insert_decision

    now = int(time.time())
    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_sched_1",
                           status="recovering")
    decision_id = insert_decision(seeded_db, opp["opportunity_id"], "reminder",
                                  outcome="executed", timestamp=now - HOUR)
    seeded_db.execute(
        "INSERT INTO recovery_executions (decision_id, state, scheduled_for) "
        "VALUES (?, 'scheduled', ?)", (decision_id, now - 60))
    seeded_db.commit()

    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)

    results = run_dispatch_cycle(now=now, conn=seeded_db)
    mine = [r for r in results if r["opportunity_id"] == opp["opportunity_id"]]
    assert mine, "the due execution was not seen by the dispatcher"
    assert mine[0]["dispatched"] is False
    assert cfg.SUPPRESSION_OUTCOME in mine[0]["reason"]

    state, reason = seeded_db.execute(
        "SELECT state, state_reason FROM recovery_executions WHERE decision_id = ?",
        (decision_id,)).fetchone()
    assert state == "cancelled"
    assert cfg.SUPPRESSION_OUTCOME in reason, \
        "a declined dispatch must record why, not just that"


# --------------------------------------------------------------------------
# The optimizer half
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.control_suppression")
def test_the_optimizer_is_not_run_for_a_control_opportunity(seeded_db,
                                                            monkeypatch):
    """
    Suppression from the optimizer pathway, which the plan asks for
    explicitly. This is an optimization rather than the guarantee -- the
    guarantee is the decide_action gate -- so it is asserted by observing that
    ranking never happens, not by trusting that it would not matter.
    """
    from backend.engine import pipeline

    monkeypatch.setitem(
        pipeline._phase5.OPTIMIZER_ENABLED_BY_ENTRY_POINT, "batch", True)

    called = []
    import backend.engine.optimize as optimize_module
    monkeypatch.setattr(optimize_module, "optimize_opportunity",
                        lambda *a, **k: called.append(a) or {"ranked": []})

    opp = make_opportunity(seeded_db, opportunity_id="opp_sup_opt_1")
    _assign(seeded_db, opp["opportunity_id"], cfg.CONTROL_GROUP)
    assert pipeline._ranked_candidates(seeded_db, opp, "batch") is None
    assert not called, "the optimizer ran for a control opportunity"

    other = make_opportunity(seeded_db, opportunity_id="opp_sup_opt_2")
    _assign(seeded_db, other["opportunity_id"], cfg.TREATMENT_GROUP)
    pipeline._ranked_candidates(seeded_db, other, "batch")
    assert called, "the optimizer did not run for a treatment opportunity"
