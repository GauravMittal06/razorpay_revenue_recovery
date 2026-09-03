"""
Phase 5 / W6 -- the two compliance-authority amendments dispatch depends on.

Both were found while planning W6 and ruled on before any code was written
(rulings A1 and A2, 2026-09-03). Each is a real defect in the sense the
project uses the word: a rule that produces the wrong answer on inputs the
system is about to start generating, not a stylistic gap.

A1 -- COOLDOWN COUNTED DECISIONS, NOT CONTACTS
    execute_action() writes recovery_decisions(action_type='reminder',
    outcome='executed') at SCHEDULE time, before anything reaches the
    customer. decide_action() builds contact_history from exactly that
    predicate, so a scheduled-but-unfired action counts as a contact already
    made. A 4h-scheduled reminder is therefore blocked by its own scheduling
    decision when the dispatcher revalidates it 4h later, and can never fire.

A2 -- THE CONTACT WINDOW COULD NOT BE REVALIDATED AT ALL
    Both window implementations read the local hour of the opportunity's
    `created_at`, which does not change between schedule time and due time.
    Revalidating via decide_action() therefore returned the identical verdict
    and closed nothing. phase5_config.DISPATCH_REVALIDATES_VIA_DECIDE_ACTION
    was declared specifically to stop a 3-day-scheduled action firing at 3am;
    without an evaluation clock it did not do that.

Both tests below are written to FAIL against the pre-amendment code, and the
failure was recorded before the fix was applied. See PHASE5_NOTES.md 1h.
"""

import time
from datetime import datetime, timedelta

import pytest

from backend.engine import phase5_config as cfg
from backend.engine.decide_action import COOLDOWN_HOURS, decide_action
from backend.engine.execute_action import execute_action
from backend.tests.conftest import make_opportunity, recent_in_window_ts

HOUR = 3600


def _candidate(conn, opportunity_id, action="reminder", timing="4h"):
    """A real recovery_candidates row -- candidate_id is a foreign key."""
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


def _schedule_a_reminder(conn, oid, timing="4h"):
    """
    Put the system in exactly the state W6's dispatcher finds: one approved
    reminder, scheduled, not yet fired.
    """
    candidate_id = _candidate(conn, oid, action="reminder", timing=timing)
    decision = {
        "action_type": "reminder",
        "allowed": True,
        "outcome": "executed",
        "reasoning": "scheduled by the ranked path",
        "triggered_by": "rule",
        "candidate_id": candidate_id,
    }
    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    return execute_action(opportunity, decision, conn)


def _backdate(conn, decision_id, seconds):
    """
    Move a decision (and its execution's scheduled_for) into the past, so the
    action is due now. Backdating the fixture rather than sleeping keeps the
    test deterministic and fast.
    """
    conn.execute(
        "UPDATE recovery_decisions SET timestamp = timestamp - ? WHERE decision_id = ?",
        (seconds, decision_id))
    conn.execute(
        "UPDATE recovery_executions SET scheduled_for = scheduled_for - ? "
        "WHERE decision_id = ?", (seconds, decision_id))
    conn.commit()


# --------------------------------------------------------------------------
# A1 -- cooldown must count delivered contacts, not scheduled intentions
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.cooldown_semantics")
def test_a_scheduled_reminder_does_not_cooldown_block_its_own_dispatch(empty_db):
    """
    THE REPRODUCTION. Ruling A1, 2026-09-03.

    Schedule a reminder 4h out, advance to its due time, and ask the
    compliance authority whether it may fire. Nothing has been sent to the
    customer -- the execution row is still 'scheduled' -- so the only contact
    in the ledger is one that has not happened.

    Before the amendment this returned blocked_cooldown with ~20h remaining,
    which is the scheduling decision blocking the very action it scheduled.
    """
    conn = empty_db
    oid = "opp_cooldown_0001"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")

    result = _schedule_a_reminder(conn, oid, timing="4h")
    execution = conn.execute(
        "SELECT * FROM recovery_executions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone()
    assert execution["state"] == "scheduled", "fixture must produce a scheduled row"
    assert execution["executed_at"] is None, "nothing has been sent yet"

    # Advance to the due moment: 4h have passed since the decision was written.
    _backdate(conn, result["decision_id"], 4 * HOUR)

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    verdict = decide_action(opportunity, {"root_cause": None}, conn)

    assert verdict["outcome"] != "blocked_cooldown", (
        "a scheduled-but-unfired reminder was counted as a contact already "
        f"made, so cooldown blocked its own dispatch: {verdict['reasoning']}")


@pytest.mark.gate("phase5.cooldown_semantics")
def test_a_delivered_contact_still_starts_the_cooldown(empty_db):
    """
    The other direction, and the one that matters for compliance: the
    amendment must not weaken cooldown for contact that actually happened.

    An immediate reminder executes on the spot (state='executed'), so it is a
    real contact and the next attempt inside 24h must still be blocked.
    """
    conn = empty_db
    oid = "opp_cooldown_0002"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")

    result = _schedule_a_reminder(conn, oid, timing="immediate")
    execution = conn.execute(
        "SELECT * FROM recovery_executions WHERE decision_id = ?",
        (result["decision_id"],)).fetchone()
    assert execution["state"] == "executed", "immediate timing fires inline"

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    verdict = decide_action(opportunity, {"root_cause": None}, conn)

    assert verdict["outcome"] == "blocked_cooldown", (
        "a delivered contact must still open the cooldown window; got "
        f"{verdict['outcome']}")


@pytest.mark.gate("phase5.cooldown_semantics")
def test_a_decision_with_no_execution_row_still_counts_as_contact(empty_db):
    """
    The conservative direction, and why the amendment is phrased as an
    exclusion rather than an inclusion.

    Every pre-Phase-5 decision in the golden corpus is inserted with no
    execution row at all. Counting only rows whose execution reached
    'executed' would silently stop counting all of them and weaken cooldown
    across the entire corpus. So a decision counts as contact UNLESS its
    execution row exists and positively says the contact has not happened.
    """
    conn = empty_db
    oid = "opp_cooldown_0003"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")
    conn.execute(
        """
        INSERT INTO recovery_decisions
        (opportunity_id, action_type, outcome, reasoning, triggered_by, timestamp)
        VALUES (?, 'reminder', 'executed', 'legacy row, no execution', 'rule', ?)
        """,
        (oid, int(time.time()) - HOUR))
    conn.commit()

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    verdict = decide_action(opportunity, {"root_cause": None}, conn)

    assert verdict["outcome"] == "blocked_cooldown", (
        "a decision with no execution row must keep counting as contact, or "
        "the amendment silently weakens cooldown for every pre-Phase-5 row")


@pytest.mark.gate("phase5.cooldown_semantics")
def test_a_cancelled_action_never_counts_as_contact(empty_db):
    """
    An action the dispatcher abandoned was never sent, so it must not consume
    the customer's cooldown budget or an attempt.
    """
    conn = empty_db
    oid = "opp_cooldown_0004"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")

    result = _schedule_a_reminder(conn, oid, timing="4h")
    conn.execute("UPDATE recovery_executions SET state = 'cancelled' "
                 "WHERE decision_id = ?", (result["decision_id"],))
    conn.commit()
    _backdate(conn, result["decision_id"], 4 * HOUR)

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    verdict = decide_action(opportunity, {"root_cause": None}, conn)

    assert verdict["outcome"] != "blocked_cooldown", (
        "a cancelled action was never sent and must not hold the cooldown")


@pytest.mark.gate("phase5.cooldown_semantics")
def test_scheduled_actions_do_not_consume_the_attempt_ceiling(empty_db):
    """
    The same defect seen through the other counter. contact_count feeds the
    MAX_RETRIES ceiling, so three scheduled-but-unfired reminders would
    exhaust the customer's entire contact budget before any of them reached
    them.
    """
    conn = empty_db
    oid = "opp_cooldown_0005"
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=recent_in_window_ts(days_ago=0, hour=12),
                     status="open")

    for _ in range(3):
        result = _schedule_a_reminder(conn, oid, timing="4h")
        _backdate(conn, result["decision_id"], 4 * HOUR)

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())
    verdict = decide_action(opportunity, {"root_cause": None}, conn)

    assert verdict["action_type"] != "stop", (
        "three unfired scheduled reminders exhausted the attempt ceiling "
        f"before any contact was made: {verdict['reasoning']}")


# --------------------------------------------------------------------------
# A2 -- the contact window must be evaluable against the dispatch clock
# --------------------------------------------------------------------------

def _hours_from_now(hours: int) -> int:
    return int((datetime.now() + timedelta(hours=hours)).timestamp())


@pytest.mark.gate("phase5.contact_window_revalidation")
def test_the_window_can_be_evaluated_against_a_supplied_clock(empty_db):
    """
    THE REPRODUCTION. Ruling A2, 2026-09-03.

    An opportunity created at noon (inside the window) whose action comes due
    at 3am (outside it). The compliance authority must be able to answer the
    question "may this fire *now*", not only "may this have fired when the
    event happened".

    Before the amendment decide_action() had no way to be asked, so the
    3am dispatch was permitted and the 9pm-8am contact ban was unenforceable
    for every scheduled action.
    """
    conn = empty_db
    oid = "opp_window_0001"
    noon = recent_in_window_ts(days_ago=0, hour=12)
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=noon, status="open")

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())

    at_3am = int(datetime.fromtimestamp(noon).replace(hour=3).timestamp())
    verdict = decide_action(opportunity, {"root_cause": None}, conn,
                            as_of=at_3am)

    assert verdict["outcome"] == "blocked_contact_hours", (
        "an action coming due at 3am must be blocked by the contact window "
        f"when the window is evaluated at its due time; got {verdict['outcome']}")


@pytest.mark.gate("phase5.contact_window_revalidation")
def test_the_supplied_clock_can_also_permit_what_created_at_forbids(empty_db):
    """
    The converse, so the parameter is proven to be a real evaluation clock
    rather than a one-way "block more" switch. An opportunity created at 3am
    whose action comes due at noon is inside the window at its due time.
    """
    conn = empty_db
    oid = "opp_window_0002"
    at_3am = recent_in_window_ts(days_ago=0, hour=3)
    make_opportunity(conn, oid, event_type="checkout_abandoned",
                     created_at=at_3am, status="open")

    opportunity = dict(
        conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                     (oid,)).fetchone())

    baseline = decide_action(opportunity, {"root_cause": None}, conn)
    assert baseline["outcome"] == "blocked_contact_hours", (
        "fixture precondition: created_at 3am is outside the window")

    noon = int(datetime.fromtimestamp(at_3am).replace(hour=12).timestamp())
    verdict = decide_action(opportunity, {"root_cause": None}, conn, as_of=noon)

    assert verdict["outcome"] != "blocked_contact_hours", (
        "an action coming due at noon is inside the window and must not be "
        f"blocked by contact hours; got {verdict['outcome']}")


@pytest.mark.gate("phase5.contact_window_revalidation")
def test_the_default_path_is_unchanged_when_no_clock_is_supplied(empty_db):
    """
    The backward-compatibility half of ruling A2: with no `as_of`, every
    verdict must be field-for-field what it was before the parameter existed.
    Pinned here per opportunity; pinned across the whole corpus by
    test_phase5_regression.py.
    """
    conn = empty_db
    for i, hour in enumerate((3, 8, 9, 12, 19, 20, 23)):
        oid = f"opp_window_default_{i}"
        make_opportunity(conn, oid, event_type="checkout_abandoned",
                         created_at=recent_in_window_ts(days_ago=0, hour=hour),
                         status="open")
        opportunity = dict(
            conn.execute("SELECT * FROM opportunities WHERE opportunity_id = ?",
                         (oid,)).fetchone())

        implicit = decide_action(opportunity, {"root_cause": None}, conn)
        explicit = decide_action(opportunity, {"root_cause": None}, conn,
                                 as_of=opportunity["created_at"])

        assert implicit == explicit, (
            f"hour {hour}: omitting as_of must be identical to passing "
            f"created_at\n  implicit={implicit}\n  explicit={explicit}")
