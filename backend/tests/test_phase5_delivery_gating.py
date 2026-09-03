"""
Phase 5 / W6 -- ruling A7: message delivery is gated on the execution having
actually completed, not on the decision having been approved.

THE DEFECT
    deliver_recovery_message() gated only on `outcome == 'executed'` and the
    action type. But `outcome` is a COMPLIANCE verdict -- it means "this action
    was permitted" -- while whether the action has actually fired lives in
    recovery_executions.state. For a scheduled action those two disagree for
    the whole scheduling window: the decision says 'executed' the moment it is
    approved, while the execution sits in 'scheduled' for up to 3 days.

    So the customer received the message at SCHEDULE time, and would receive it
    again when W6's dispatcher fired the action at due time. Two contacts for
    one approved action, against a system whose entire compliance surface is
    built to prevent exactly that.

WHY IT IS FIXED HERE AND NOT LEFT TO W7
    It is unreachable today only because no entry point supplies
    ranked_candidates, so nothing is ever scheduled. W6 is the change that
    makes scheduling reachable, so shipping W6 without this ships the
    double-contact. It is the same category as the two concurrency defects
    already fixed this phase -- a latent correctness bug that a new component
    activates -- not new-feature scope.

    Conflating the two tables is also precisely what the Phase 5 acceptance
    gate "Execution separation" and the permanent invariant "a payment, an
    opportunity, a decision, an execution and an outcome are five distinct
    concepts" forbid. The bug is a live instance of that conflation.
"""

import time

import pytest

from backend.engine.deliver_message import deliver_recovery_message
from backend.engine.execute_action import execute_action
from backend.tests.conftest import make_opportunity, recent_in_window_ts


def _candidate(conn, opportunity_id, action="reminder", timing="4h"):
    cur = conn.execute(
        """
        INSERT INTO recovery_candidates
        (opportunity_id, action_type, timing, method, channel,
         predicted_eiv, rank, selected, created_at)
        VALUES (?, ?, ?, 'n/a', 'email', 12.5, 1, 0, ?)
        """,
        (opportunity_id, action, timing, int(time.time())),
    )
    conn.commit()
    return cur.lastrowid


def _approve(conn, oid, timing):
    candidate_id = _candidate(conn, oid, action="reminder", timing=timing)
    decision = {
        "action_type": "reminder",
        "allowed": True,
        "outcome": "executed",
        "reasoning": "approved by the ranked path",
        "triggered_by": "rule",
        "candidate_id": candidate_id,
    }
    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    result = execute_action(opportunity, decision, conn)
    return opportunity, decision, result


def _messages(conn, oid):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM messages WHERE opportunity_id = ? AND sender = 'agent'",
        (oid,)).fetchall()]


@pytest.mark.gate("phase5.delivery_gating")
def test_a_scheduled_action_sends_no_message_at_schedule_time(empty_db):
    """
    THE REPRODUCTION. Ruling A7, 2026-09-03.

    The decision is compliant and approved; the execution is 'scheduled' and
    has not fired. Nothing may reach the customer yet.
    """
    conn = empty_db
    oid = "opp_deliver_0001"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")

    opportunity, decision, result = _approve(conn, oid, timing="4h")
    execution = conn.execute(
        "SELECT * FROM recovery_executions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone()
    assert execution["state"] == "scheduled", "fixture must produce a scheduled row"

    delivery = deliver_recovery_message(
        opportunity, {"root_cause": None}, decision, conn,
        decision_id=result["decision_id"])

    assert _messages(conn, oid) == [], (
        "a message was sent to the customer for an action that has not fired "
        "yet; it will be sent again when the dispatcher fires it")
    assert delivery["delivered"] is False


@pytest.mark.gate("phase5.delivery_gating")
def test_an_immediate_action_still_sends_its_message(empty_db):
    """
    The other direction. Gating on execution state must not stop the ordinary
    immediate path from contacting anyone -- that would silence the system.
    """
    conn = empty_db
    oid = "opp_deliver_0002"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")

    opportunity, decision, result = _approve(conn, oid, timing="immediate")
    execution = conn.execute(
        "SELECT * FROM recovery_executions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone()
    assert execution["state"] == "executed", "immediate timing fires inline"

    delivery = deliver_recovery_message(
        opportunity, {"root_cause": None}, decision, conn,
        decision_id=result["decision_id"])

    assert delivery["delivered"] is True, delivery
    assert len(_messages(conn, oid)) == 1


@pytest.mark.gate("phase5.delivery_gating")
def test_a_blocked_decision_still_sends_nothing(empty_db):
    """The pre-existing compliance gate must survive the new lifecycle gate."""
    conn = empty_db
    oid = "opp_deliver_0003"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    blocked = {"action_type": "reminder", "allowed": False,
               "outcome": "blocked_cooldown", "reasoning": "cooldown",
               "triggered_by": "rule"}

    delivery = deliver_recovery_message(opportunity, {"root_cause": None},
                                        blocked, conn)

    assert delivery["status"] == "skipped_ineligible"
    assert _messages(conn, oid) == []


@pytest.mark.gate("phase5.delivery_gating")
def test_delivery_without_a_verifiable_execution_fails_closed(empty_db):
    """
    If the caller cannot say which execution this delivery belongs to, the
    function cannot know whether the action fired, and must not guess.

    Failing closed costs at most a missing message, which is visible in the
    returned status. Failing open costs a duplicate contact, which is the
    defect this ruling exists to remove.
    """
    conn = empty_db
    oid = "opp_deliver_0004"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    approved = {"action_type": "reminder", "allowed": True,
                "outcome": "executed", "reasoning": "approved",
                "triggered_by": "rule"}

    delivery = deliver_recovery_message(opportunity, {"root_cause": None},
                                        approved, conn)

    assert delivery["delivered"] is False
    assert delivery["status"] == "skipped_unverified_execution"
    assert _messages(conn, oid) == []


@pytest.mark.gate("phase5.delivery_gating")
def test_the_recovery_path_has_exactly_one_delivery_call_site(source_files):
    """
    Structural, not behavioural. The fail-closed default above means a caller
    that forgets `decision_id` goes silent rather than double-contacting --
    but silence is still a defect, so the call site is pinned.

    AMENDED 2026-09-04 by W7, and disclosed as an amendment rather than a
    silent edit. It previously asserted a delivery call in EACH of
    core_loop.py, trigger_event.py and handle_customer_reply.py. W7 unified
    those three onto engine/pipeline.py, so that premise became false --
    the three no longer deliver, the shared pipeline does.

    The replacement is STRICTLY STRONGER, not a relaxation to obtain a pass.
    Before: three call sites, each of which had to pass decision_id --
    a fourth caller could appear anywhere and be missed. After: the recovery
    path has EXACTLY ONE delivery call site, it is in the shared pipeline,
    and it passes decision_id. There is no longer anywhere for a call to
    drift to.
    """
    import re

    pattern = r"deliver_recovery_message\((?:[^()]|\([^()]*\))+\)"
    # dispatch_scheduled.py legitimately delivers too, for an action it is
    # firing from the queue; deliver_message.py is the definition itself.
    exempt = {"deliver_message.py", "dispatch_scheduled.py"}

    call_sites = {}
    for path in source_files:
        if path.name in exempt or "engine" not in path.parts:
            continue
        calls = re.findall(pattern, path.read_text(encoding="utf-8"))
        if calls:
            call_sites[path.name] = calls

    assert list(call_sites) == ["pipeline.py"], (
        "the recovery path must deliver from exactly one place, the shared "
        f"pipeline; found call sites in {sorted(call_sites)}")
    assert all("decision_id=" in c for c in call_sites["pipeline.py"]), (
        "the shared pipeline delivers without naming the execution, so "
        "delivery fails closed and no customer is ever contacted: "
        f"{call_sites['pipeline.py']}")


@pytest.mark.gate("phase5.shared_pipeline")
def test_no_entry_point_bypasses_the_shared_pipeline(source_files):
    """
    The other half: the three entry points must not reach past
    run_recovery_pipeline() to the engine calls it owns. A caller that still
    called decide_action() or execute_action() itself would be a second
    pipeline wearing the first one's name.
    """
    import re

    entry_points = {"core_loop.py", "trigger_event.py", "handle_customer_reply.py"}
    forbidden = ("decide_action", "execute_action", "deliver_recovery_message",
                 "opportunity_lock", "classify")

    offenders = {}
    for path in source_files:
        if path.name not in entry_points:
            continue
        body = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#"))
        body = body.split('"""')[-1]          # drop the module docstring
        hits = [name for name in forbidden
                if re.search(rf"\b{name}\s*\(", body)]
        if hits:
            offenders[path.name] = hits

    assert not offenders, (
        "these entry points call pipeline-owned functions directly instead "
        f"of going through run_recovery_pipeline(): {offenders}")
