"""
Phase 5 / W7 -- two authority invariants made mechanical.

Both were carried in PHASE5_NOTES.md section 2 as work deferred to "W8".
Ruling A10 (2026-09-04) closed that: there is no W8. Both items appear
verbatim in EXECUTION_PLAN.md's own Phase 5 validation list --

    "The existing authority tests (only decide_action's allowed: True
     output ever reaches the executor)."
    "A structural test confirming that no query can conflate a
     recovery_executions lifecycle state with a recovery_decisions
     compliance outcome."

-- so they belong to Phase 5's last step, which is W7.


1. THE `allowed` / `outcome` PROXY
    The permanent invariant is "only decide_action's allowed: True output
    ever reaches the executor". But execute_action() branches on
    `outcome == "executed"`, never on `allowed`. So the invariant was proven
    through a PROXY field, tied to the real permission bit only by
    convention.

    That is fine if and only if the two are provably equivalent. These tests
    establish the equivalence mechanically -- across every branch
    decide_action can reach, and at the executor boundary itself -- rather
    than leaving it asserted in a comment.

2. THE `'executed'` COLLISION
    'executed' is a member of BOTH closed vocabularies: DECISION_OUTCOMES
    (compliance: "this action was permitted") and EXECUTION_STATES
    (lifecycle: "this action has fired"). They mean different things, and for
    a scheduled action they disagree for up to three days -- the decision
    reads 'executed' while the execution sits in 'scheduled'.

    The collision is live and is NOT renamed here: renaming a value in a
    closed vocabulary is a schema change with migration consequences, and the
    W6 delivery-gating defect it contributed to is already fixed at the point
    that mattered. It is pinned instead, so a query that conflates the two
    tables fails a test rather than silently returning a wrong answer.
"""

import pytest

from backend.db.db import DECISION_OUTCOMES, EXECUTION_STATES
from backend.engine.decide_action import decide_action
from backend.engine.execute_action import execute_action
from backend.tests import phase5_scenarios as ps


# --------------------------------------------------------------------------
# 1. allowed <-> outcome, made mechanical
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.single_authority")
def test_allowed_is_exactly_outcome_executed_across_every_branch(empty_db, capsys):
    """
    The equivalence the proxy rests on, checked against every scenario in the
    frozen corpus -- which is the corpus specifically built to reach every
    branch decide_action() has.

    If this ever fails, "only allowed:True reaches the executor" stops
    following from execute_action()'s `outcome == 'executed'` branch, and the
    authority invariant is no longer proven at all.
    """
    decisions = ps.capture_all(empty_db)
    assert decisions, "the scenario corpus produced no decisions"

    violations = [
        (name, d.get("allowed"), d.get("outcome"))
        for name, d in sorted(decisions.items())
        if d.get("allowed") != (d.get("outcome") == "executed")
    ]
    print(f"  {len(decisions)} scenarios checked, {len(violations)} violating "
          f"allowed == (outcome == 'executed')")
    assert not violations, (
        "allowed and outcome disagree, so the executor's outcome-based "
        f"branch no longer implements the allowed-based invariant: {violations}")


@pytest.mark.gate("permanent.single_authority")
def test_the_executor_writes_an_execution_row_exactly_when_allowed(empty_db):
    """
    The same equivalence at the boundary that matters. An execution row is
    the executor acting; it must appear for allowed decisions and for no
    others.

    `do_nothing` is the one allowed action that legitimately writes no
    execution row -- deciding to act by not acting is a decision, not an
    execution -- so it is excluded by the declared
    EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS list rather than by a literal.
    """
    from backend.engine.phase5_config import EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS
    from backend.tests.conftest import make_opportunity, recent_in_window_ts

    cases = [
        ("reminder", "executed", True),
        ("retry", "executed", True),
        ("escalate", "executed", True),
        ("stop", "executed", True),
        ("reminder", "blocked_cooldown", False),
        ("reminder", "blocked_contact_hours", False),
        ("escalate", "blocked_already_escalated", False),
        ("stop", "blocked_already_stopped", False),
        (None, "flagged_manual_review", False),
    ]

    for i, (action, outcome, allowed) in enumerate(cases):
        oid = f"opp_auth_{i:03d}"
        make_opportunity(empty_db, oid, event_type="checkout_abandoned",
                         created_at=recent_in_window_ts(days_ago=0, hour=12),
                         status="open")
        opportunity = dict(empty_db.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
        decision = {"action_type": action, "allowed": allowed,
                    "outcome": outcome, "reasoning": "authority fixture",
                    "triggered_by": "rule"}
        result = execute_action(opportunity, decision, empty_db)

        rows = empty_db.execute(
            "SELECT COUNT(*) c FROM recovery_executions WHERE decision_id = ?",
            (result["decision_id"],)).fetchone()["c"]

        expected = 1 if (allowed and action is not None
                         and action not in EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS) else 0
        assert rows == expected, (
            f"action={action} outcome={outcome} allowed={allowed}: "
            f"{rows} execution rows, expected {expected}")


@pytest.mark.gate("permanent.single_authority")
def test_the_executor_still_branches_on_outcome_not_allowed(source_files):
    """
    Documents the actual state of affairs rather than implying it was fixed.

    execute_action() reads `outcome`, not `allowed`. That is deliberate --
    changing it would be a contract change to every caller -- and it is safe
    only because of the equivalence the two tests above establish. This test
    exists so that a future reader does not assume the permission bit is what
    is checked, and so that if someone DOES switch the executor to `allowed`,
    they are prompted to retire this note rather than leave it lying.
    """
    import re

    path = next(p for p in source_files if p.name == "execute_action.py")
    # Drop only the MODULE docstring (maxsplit=2). Taking the last `"""`
    # segment would discard the function bodies too, since the file has
    # several docstrings.
    body = path.read_text(encoding="utf-8").split('"""', 2)[-1]

    assert re.search(r'decision\["outcome"\]\s*==\s*"executed"', body), (
        "execute_action no longer branches on outcome == 'executed'; the "
        "allowed/outcome equivalence tests above were written against that "
        "branch and need revisiting")
    assert not re.search(r'decision\[.allowed.\]', body), (
        "execute_action now reads `allowed` directly -- good, but the "
        "proxy-equivalence rationale in this module is now stale and should "
        "be retired")


# --------------------------------------------------------------------------
# 2. The 'executed' collision, pinned
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.execution_separation")
def test_executed_is_the_only_token_the_two_vocabularies_share():
    """
    The collision is live and deliberate-by-omission, not renamed. Pinning
    the overlap means a future edit that adds a SECOND shared token -- which
    would make "is this a compliance value or a lifecycle value" genuinely
    ambiguous in more than one place -- fails here.
    """
    overlap = set(DECISION_OUTCOMES) & set(EXECUTION_STATES)
    assert overlap == {"executed"}, (
        f"the two closed vocabularies now share {sorted(overlap)}; only "
        "'executed' is the known, disclosed collision")


@pytest.mark.gate("phase5.execution_separation")
def test_a_decision_outcome_and_its_execution_state_can_legitimately_differ(empty_db):
    """
    The reason the collision matters, made concrete: for a scheduled action
    the decision says 'executed' (compliant, cleared to fire) while the
    execution says 'scheduled' (has not fired). Any query that reads one as
    the other is wrong, and this is the case that proves it.
    """
    import time
    from backend.tests.conftest import make_opportunity, recent_in_window_ts

    oid = "opp_collision_0001"
    make_opportunity(empty_db, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    cur = empty_db.execute(
        """
        INSERT INTO recovery_candidates
        (opportunity_id, action_type, timing, method, channel,
         predicted_eiv, rank, selected, created_at)
        VALUES (?, 'reminder', '4h', 'n/a', 'email', 12.5, 1, 0, ?)
        """,
        (oid, int(time.time())))
    empty_db.commit()

    opportunity = dict(empty_db.execute(
        "SELECT * FROM opportunities WHERE opportunity_id = ?", (oid,)).fetchone())
    result = execute_action(opportunity, {
        "action_type": "reminder", "allowed": True, "outcome": "executed",
        "reasoning": "queued", "triggered_by": "rule",
        "candidate_id": cur.lastrowid}, empty_db)

    row = empty_db.execute(
        "SELECT d.outcome, e.state FROM recovery_decisions d "
        "JOIN recovery_executions e ON e.decision_id = d.decision_id "
        "WHERE d.decision_id = ?", (result["decision_id"],)).fetchone()

    assert row["outcome"] == "executed", row["outcome"]
    assert row["state"] == "scheduled", row["state"]
    assert row["outcome"] != row["state"], (
        "a scheduled action must show a compliance outcome of 'executed' and "
        "a lifecycle state of 'scheduled' -- if these ever coincide the "
        "collision has become genuinely ambiguous")
