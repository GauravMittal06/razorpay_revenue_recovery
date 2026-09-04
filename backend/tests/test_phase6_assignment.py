"""
Phase 6 / X2 -- randomized assignment at opportunity-creation time.

What these tests are actually protecting:

* **Assignment happens exactly once per opportunity, ever.** Re-randomizing a
  live opportunity is the failure mode with no downstream symptom -- the row
  looks perfectly consistent afterwards while having silently changed arms
  after it was already treated. So idempotency is tested directly, under
  concurrency, and through the replayed-event path.
* **Assignment precedes the first decision.** An opportunity treated before it
  was assigned would sit in the control arm with a treatment in its history.
* **Only the creation entry point assigns.** The structural half of that is in
  test_permanent_gates.py; the behavioural half is here.

The randomization *quality* question -- are the two arms actually comparable
-- is not asked here. That is X5's hard gate, against real assigned
opportunities, and it reports raw numbers rather than a verdict.
"""

import sqlite3

import pytest

from backend.engine import phase6_config as cfg
from backend.engine.assign_experiment_group import (assign_experiment_group,
                                                    get_assignment)
from backend.tests.conftest import make_opportunity


# --------------------------------------------------------------------------
# The core contract
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.assignment")
def test_assignment_records_group_time_and_method(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_asg_0001")
    result = assign_experiment_group(opp["opportunity_id"], seeded_db, now=1700)

    assert result["status"] == "assigned"
    assert result["group"] in cfg.GROUPS
    assert result["assigned_at"] == 1700
    assert result["assignment_method"] == cfg.assignment_method_record()

    row = seeded_db.execute(
        'SELECT opportunity_id, "group", assigned_at, assignment_method '
        "FROM experiment_assignment WHERE opportunity_id = ?",
        (opp["opportunity_id"],),
    ).fetchone()
    assert dict(row) == {k: result[k] for k in
                         ("opportunity_id", "group", "assigned_at",
                          "assignment_method")}


@pytest.mark.gate("phase6.assignment")
def test_the_recorded_group_is_the_one_the_locked_formula_derives(seeded_db):
    """
    The stored group must be recomputable from the id and the committed salt
    alone. This is what makes an assignment auditable years later rather than
    merely asserted -- and it fails if the module ever starts drawing its own
    randomness instead of using the locked derivation.
    """
    for i in range(40):
        opp = make_opportunity(seeded_db, opportunity_id=f"opp_asg_d{i:04d}")
        result = assign_experiment_group(opp["opportunity_id"], seeded_db)
        assert result["group"] == cfg.assigned_group(opp["opportunity_id"])


@pytest.mark.gate("phase6.assignment")
def test_the_method_string_is_stored_per_row_not_just_derived(seeded_db):
    """A stored assignment must stay interpretable if the config is later
    amended for a different population, so the row carries the method, salt
    and fraction that actually produced it."""
    opp = make_opportunity(seeded_db, opportunity_id="opp_asg_0002")
    assign_experiment_group(opp["opportunity_id"], seeded_db)
    stored = get_assignment(opp["opportunity_id"], seeded_db)["assignment_method"]
    assert cfg.ASSIGNMENT_METHOD in stored
    assert cfg.ASSIGNMENT_SALT in stored
    assert str(cfg.HOLDOUT_FRACTION) in stored


# --------------------------------------------------------------------------
# Assign once, and only once
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.assignment")
def test_a_second_call_is_a_no_op_and_never_re_randomizes(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_asg_0003")
    first = assign_experiment_group(opp["opportunity_id"], seeded_db, now=1000)
    second = assign_experiment_group(opp["opportunity_id"], seeded_db, now=9999)

    assert first["status"] == "assigned"
    assert second["status"] == "already_assigned"
    assert second["group"] == first["group"]
    assert second["assigned_at"] == 1000, \
        "a repeat call moved assigned_at; the original assignment instant is " \
        "the one the experiment is anchored to"

    n = seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment WHERE opportunity_id = ?",
        (opp["opportunity_id"],),
    ).fetchone()[0]
    assert n == 1


@pytest.mark.gate("phase6.assignment")
def test_the_primary_key_is_the_guarantee_not_the_select(seeded_db):
    """
    The SELECT fast path has a check-then-insert race window. The real
    guarantee is the PRIMARY KEY, so a direct second INSERT must be rejected
    by the schema rather than merely avoided by the code.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_asg_0004")
    assign_experiment_group(opp["opportunity_id"], seeded_db)
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute(
            'INSERT INTO experiment_assignment '
            '(opportunity_id, "group", assigned_at, assignment_method) '
            "VALUES (?, ?, ?, ?)",
            (opp["opportunity_id"], "treatment", 1, "manual"),
        )


@pytest.mark.gate("phase6.assignment")
def test_an_unknown_opportunity_is_not_assigned(seeded_db):
    """
    The foreign key lands in the same IntegrityError handler as the primary
    key, and the two need opposite answers. Inventing an assignment for a row
    that does not exist would put an orphan in the experiment population.
    """
    result = assign_experiment_group("opp_does_not_exist", seeded_db)
    assert result["status"] == "opportunity_not_found"
    assert result["group"] is None
    assert seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment "
        "WHERE opportunity_id = 'opp_does_not_exist'").fetchone()[0] == 0


@pytest.mark.gate("phase6.assignment")
def test_get_assignment_never_derives_a_group_for_an_unassigned_row(seeded_db):
    """
    An opportunity with no row is NOT in the experiment (ruling 2026-09-04).
    A reader that silently recomputed a group from the id would quietly enrol
    the entire pre-Phase-6 population into an experiment it was never
    randomized for -- and every one of those rows would then count toward an
    incremental number it has no business informing.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_asg_0005")
    assert get_assignment(opp["opportunity_id"], seeded_db) is None


# --------------------------------------------------------------------------
# The creation-time hook
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.assignment")
def test_trigger_event_assigns_every_opportunity_it_creates(seeded_db):
    from backend.engine.trigger_event import trigger_event

    result = trigger_event("payment_failed", 5000, seeded_db,
                           root_cause="insufficient_funds")
    assert result["status"] == "ok"
    opportunity_id = result["opportunity"]["opportunity_id"]

    assert result["assignment"]["status"] == "assigned"
    assert result["assignment"]["group"] in cfg.GROUPS

    stored = get_assignment(opportunity_id, seeded_db)
    assert stored is not None, "trigger_event created an unassigned opportunity"
    assert stored["group"] == cfg.assigned_group(opportunity_id)


@pytest.mark.gate("phase6.assignment")
def test_assignment_precedes_the_first_decision(seeded_db):
    """
    An opportunity treated before it was assigned would land in the control
    arm carrying a treatment in its history -- an inconsistency the
    counterfactual gate would report and nothing could repair after the fact.
    """
    from backend.engine.trigger_event import trigger_event

    result = trigger_event("payment_failed", 7000, seeded_db,
                           root_cause="gateway_timeout")
    opportunity_id = result["opportunity"]["opportunity_id"]

    assigned_at = get_assignment(opportunity_id, seeded_db)["assigned_at"]
    first_decision = seeded_db.execute(
        "SELECT MIN(timestamp) FROM recovery_decisions WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()[0]
    assert first_decision is not None, "no decision was recorded to compare against"
    assert assigned_at <= first_decision


@pytest.mark.gate("phase6.assignment")
def test_a_replayed_event_does_not_reassign(seeded_db):
    """
    The duplicate-event short-circuit returns before the assignment call. A
    replayed upstream delivery must not re-randomize an opportunity that
    already exists.
    """
    from backend.engine.trigger_event import trigger_event

    first = trigger_event("payment_failed", 4200, seeded_db,
                          root_cause="expired_card", event_id="evt_replay_1")
    opportunity_id = first["opportunity"]["opportunity_id"]
    before = get_assignment(opportunity_id, seeded_db)

    second = trigger_event("payment_failed", 4200, seeded_db,
                           root_cause="expired_card", event_id="evt_replay_1")
    assert second["status"] == "duplicate_event_ignored"

    after = get_assignment(opportunity_id, seeded_db)
    assert after == before
    assert seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment").fetchone()[0] == 1


@pytest.mark.gate("phase6.assignment")
def test_an_invalid_event_creates_no_assignment(seeded_db):
    """Validation returns before any row is written, so a rejected event must
    leave neither an opportunity nor an assignment behind."""
    from backend.engine.trigger_event import trigger_event

    before = seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment").fetchone()[0]
    for bad in (
        dict(event_type="nonsense", amount=100),
        dict(event_type="payment_failed", amount=0,
             root_cause="insufficient_funds"),
        dict(event_type="payment_failed", amount=100, root_cause="not_a_cause"),
    ):
        result = trigger_event(conn=seeded_db, **bad)
        assert result["status"] != "ok"
    after = seeded_db.execute(
        "SELECT COUNT(*) FROM experiment_assignment").fetchone()[0]
    assert after == before


# --------------------------------------------------------------------------
# Both arms are actually reachable through the real entry point
# --------------------------------------------------------------------------

@pytest.mark.gate("phase6.assignment")
def test_both_arms_are_produced_by_real_trigger_event_ids(seeded_db):
    """
    Not a balance test -- that is X5. This is the far weaker precondition
    that ids minted the way trigger_event mints them actually reach both
    arms. A bucketing bug that sent every real opportunity to one arm would
    otherwise only surface at X5, after the volume run.
    """
    from backend.engine.trigger_event import trigger_event

    groups = set()
    for i in range(30):
        result = trigger_event("checkout_abandoned", 1000 + i, seeded_db)
        groups.add(result["assignment"]["group"])
        if groups == set(cfg.GROUPS):
            break
    assert groups == set(cfg.GROUPS), (
        f"only reached {sorted(groups)} in 30 real trigger_event calls")
