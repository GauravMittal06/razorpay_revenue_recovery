"""
Phase 1 exit gates -- "Schema Foundation".

Phase 1's claim is structural: the schema itself, not application discipline,
must make the three-way state separation and the opportunity/attempt
distinction impossible to confuse. So most assertions here interrogate
`PRAGMA table_info` and the DDL rather than the behaviour of a function --
a passing behavioural test proves the current caller is careful, whereas a
column that does not exist cannot be misused by any caller.

Deliberate Phase 1 deferrals (recovery_candidates left empty until Phase 4,
amount_at_risk_total left mislabeled until Phase 8) are pinned, not treated
as defects: see the docstrings on the individual tests.
"""

import sqlite3

import pytest

from backend.tests.conftest import (insert_decision, make_opportunity,
                                    make_payment, recent_in_window_ts)

EXPECTED_TABLES = {
    "merchants",
    "customers",
    "opportunities",
    "payments",
    "recovery_candidates",
    "recovery_decisions",
    "recovery_executions",
    "experiment_assignment",
    "bank_health_observations",
    "messages",
    "dataset_registry",
}

RETIRED_TABLES = {"recovery_actions"}


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()}


def _columns(conn, table) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# --------------------------------------------------------------------------
# Gate: migration completed without semantic loss
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.migration")
def test_schema_contains_exactly_the_phase1_tables(empty_db):
    actual = _tables(empty_db)
    assert actual == EXPECTED_TABLES, (
        f"missing: {sorted(EXPECTED_TABLES - actual)}; "
        f"unexpected: {sorted(actual - EXPECTED_TABLES)}")


@pytest.mark.gate("phase1.migration")
def test_retired_tables_are_absent(empty_db):
    """
    `recovery_actions` conflated compliance outcome, execution state, and
    business result in one row. Leaving it in place -- even unused -- would
    leave a second, contradictory home for all three.
    """
    still_present = _tables(empty_db) & RETIRED_TABLES
    assert not still_present, f"retired table(s) still created: {sorted(still_present)}"


@pytest.mark.gate("phase1.migration")
def test_every_pre_phase1_concept_has_a_post_migration_home(empty_db):
    """
    Semantic-loss check. Each concept the flat schema carried must be
    addressable after the migration; the point is that nothing was dropped
    on the way, only relocated.
    """
    homes = {
        "attempt-level failure reason": ("payments", "error_reason"),
        "attempt-level instrument": ("payments", "method"),
        "compliance outcome": ("recovery_decisions", "outcome"),
        "compliance reasoning": ("recovery_decisions", "reasoning"),
        "who triggered the action": ("recovery_decisions", "triggered_by"),
        "advisory ml score": ("recovery_decisions", "ml_recovery_probability"),
        "manual-review flag": ("recovery_decisions", "flag_type"),
        "execution lifecycle state": ("recovery_executions", "state"),
        "money actually recovered": ("opportunities", "partial_recovery_amount"),
        "recovery boolean": ("opportunities", "recovered_bool"),
        "time to recovery": ("opportunities", "time_to_recovery"),
        "how the case ended": ("opportunities", "resolution_type"),
        "conversation thread": ("messages", "content"),
        "extracted intent": ("messages", "intent_extracted"),
    }
    missing = {concept: f"{t}.{c}" for concept, (t, c) in homes.items()
               if c not in _columns(empty_db, t)}
    assert not missing, f"concepts with no column after migration: {missing}"


# --------------------------------------------------------------------------
# Gate: three-way state separation, unconfusable by schema
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.state_separation")
def test_decision_table_cannot_hold_execution_or_money_state(empty_db):
    forbidden = {"state", "scheduled_for", "executed_at", "delivered",
                 "recovered_bool", "partial_recovery_amount", "recovered_at",
                 "resolution_type", "status"}
    leaked = _columns(empty_db, "recovery_decisions") & forbidden
    assert not leaked, (
        "recovery_decisions adjudicates compliance only; these columns would "
        f"let it also assert execution or business outcome: {sorted(leaked)}")


@pytest.mark.gate("phase1.state_separation")
def test_execution_table_cannot_hold_compliance_or_money_state(empty_db):
    forbidden = {"outcome", "allowed", "reasoning", "flag_type",
                 "ml_recovery_probability", "recovered_bool",
                 "partial_recovery_amount", "resolution_type"}
    leaked = _columns(empty_db, "recovery_executions") & forbidden
    assert not leaked, (
        "recovery_executions tracks lifecycle state only; these columns would "
        f"let it re-assert a compliance verdict or a money outcome: {sorted(leaked)}")


@pytest.mark.gate("phase1.state_separation")
def test_business_outcome_lives_only_on_the_opportunity(empty_db):
    money_columns = {"recovered_bool", "partial_recovery_amount",
                     "recovered_at", "time_to_recovery", "resolution_type"}
    assert money_columns <= _columns(empty_db, "opportunities"), (
        "opportunities is missing business-outcome columns: "
        f"{sorted(money_columns - _columns(empty_db, 'opportunities'))}")

    for table in ("recovery_decisions", "recovery_executions",
                  "recovery_candidates", "payments"):
        leaked = _columns(empty_db, table) & money_columns
        assert not leaked, f"{table} duplicates business outcome: {sorted(leaked)}"


@pytest.mark.gate("phase1.state_separation")
def test_the_three_layers_are_separable_by_a_single_join(seeded_db):
    """
    The separation must survive being *queried*, not only being declared:
    a reader must be able to get compliance verdict, execution state, and
    money outcome for one opportunity without any of the three standing in
    for another.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_sep_0001",
                           status="recovered", recovered_bool=1,
                           partial_recovery_amount=25_000,
                           resolution_type="recovered")
    decision_id = insert_decision(seeded_db, opp["opportunity_id"], "retry",
                                  outcome="executed")
    seeded_db.execute(
        "INSERT INTO recovery_executions (decision_id, state, executed_at, "
        "channel) VALUES (?, 'executed', ?, 'email')",
        (decision_id, recent_in_window_ts()))
    seeded_db.commit()

    row = seeded_db.execute(
        "SELECT d.outcome AS decision_outcome, e.state AS execution_state, "
        "o.recovered_bool, o.partial_recovery_amount "
        "FROM recovery_decisions d "
        "JOIN recovery_executions e ON e.decision_id = d.decision_id "
        "JOIN opportunities o ON o.opportunity_id = d.opportunity_id "
        "WHERE d.decision_id = ?", (decision_id,)).fetchone()

    assert row["decision_outcome"] == "executed"
    assert row["execution_state"] == "executed"
    assert row["recovered_bool"] == 1
    assert row["partial_recovery_amount"] == 25_000


# --------------------------------------------------------------------------
# Gate: one opportunity owns many payment attempts
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.opportunity_aggregation")
def test_one_opportunity_aggregates_multiple_attempts(seeded_db):
    opp = make_opportunity(seeded_db, opportunity_id="opp_multi_0001")
    for i in range(3):
        make_payment(seeded_db, opp["opportunity_id"],
                     payment_id=f"pay_multi_{i}",
                     created_at=recent_in_window_ts(days_ago=3 - i))

    attempts = seeded_db.execute(
        "SELECT COUNT(*) FROM payments WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchone()[0]
    assert attempts == 3

    owners = seeded_db.execute(
        "SELECT COUNT(DISTINCT opportunity_id) FROM payments WHERE id LIKE 'pay_multi_%'"
    ).fetchone()[0]
    assert owners == 1, "the three attempts did not resolve to a single owner"


@pytest.mark.gate("phase1.opportunity_aggregation")
def test_case_detail_returns_all_attempts_for_one_case(seeded_db):
    """
    The API must present the aggregate, not one attempt. `payments` is
    plural in the response for exactly this reason.
    """
    from backend.api.queries import get_case_detail

    opp = make_opportunity(seeded_db, opportunity_id="opp_detail_0001")
    for i in range(2):
        make_payment(seeded_db, opp["opportunity_id"], payment_id=f"pay_detail_{i}")

    detail = get_case_detail(seeded_db, opp["opportunity_id"])
    assert len(detail["payments"]) == 2, \
        f"case detail exposed {len(detail['payments'])} of 2 attempts"


@pytest.mark.gate("phase1.opportunity_aggregation")
def test_seeded_data_contains_a_real_multi_attempt_case(seeded_db):
    """Guards against the aggregation gate passing only on synthetic rows."""
    top = seeded_db.execute(
        "SELECT opportunity_id, COUNT(*) AS n FROM payments "
        "GROUP BY opportunity_id ORDER BY n DESC LIMIT 1").fetchone()
    assert top is not None and top["n"] > 1, \
        "no seeded opportunity has more than one payment attempt"


# --------------------------------------------------------------------------
# Gate: recovery_candidates stores every candidate, including do_nothing
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.candidates")
def test_recovery_candidates_round_trips_every_candidate_including_do_nothing(seeded_db):
    """
    The table has to be able to hold the *rejected* options and the explicit
    do-nothing baseline, because incremental value is meaningless without
    them. Phase 4 populates it; Phase 1 owes only the structure, so this
    writes the rows directly.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_cand_0001")
    now = recent_in_window_ts()
    candidates = [
        ("retry", "immediate", "card", "email", 0.42, 0.31, 1, None, 1),
        ("reminder", "t_plus_24h", None, "sms", 0.36, 0.31, 2, None, 0),
        ("do_nothing", None, None, None, 0.31, 0.31, 3, None, 0),
        ("escalate", "immediate", None, "human", 0.30, 0.31, 4, "cost_filter", 0),
    ]
    for action, timing, method, channel, p_t, p_b, rank, pruned, selected in candidates:
        seeded_db.execute(
            "INSERT INTO recovery_candidates (opportunity_id, action_type, timing, "
            "method, channel, predicted_p_treated, predicted_p_baseline, "
            "predicted_eiv, rank, pruned_stage, selected, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (opp["opportunity_id"], action, timing, method, channel, p_t, p_b,
             (p_t - p_b) * opp["amount_at_risk"], rank, pruned, selected, now))
    seeded_db.commit()

    stored = {r["action_type"]: dict(r) for r in seeded_db.execute(
        "SELECT * FROM recovery_candidates WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchall()}

    assert set(stored) == {"retry", "reminder", "do_nothing", "escalate"}
    assert stored["do_nothing"]["predicted_p_treated"] == stored["do_nothing"]["predicted_p_baseline"], \
        "the do_nothing baseline must not carry a treatment lift"
    assert sum(c["selected"] for c in stored.values()) == 1, \
        "exactly one candidate may be marked selected"
    assert stored["escalate"]["pruned_stage"] == "cost_filter", \
        "a pruned candidate must record why it was dropped"


@pytest.mark.gate("phase1.candidates")
def test_recovery_candidates_is_empty_after_a_phase1_bootstrap(seeded_db):
    """
    A pin on a deliberate deferral, not a defect. Fabricating predicted_eiv
    before an optimizer exists would be a synthetic number sitting in the
    column a later phase reads as fact. Phase 4 flips this expectation.
    """
    from backend.engine.core_loop import run_cycle

    run_cycle()
    count = seeded_db.execute("SELECT COUNT(*) FROM recovery_candidates").fetchone()[0]
    assert count == 0, (
        f"{count} candidate rows exist after a Phase 1 run. If Phase 4 has "
        "landed, this pin is the test to update.")


# --------------------------------------------------------------------------
# Gate: merchant scope
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.merchant_scope")
def test_merchant_scope_is_addressable_end_to_end(seeded_db):
    """
    Merchant must be a first-class dimension: every opportunity attributable
    to one, and aggregation by merchant possible without inferring it
    through the customer.
    """
    assert "merchant_id" in _columns(seeded_db, "opportunities")
    assert "merchant_id" in _columns(seeded_db, "customers")

    unattributed = seeded_db.execute(
        "SELECT COUNT(*) FROM opportunities WHERE merchant_id IS NULL").fetchone()[0]
    assert unattributed == 0, f"{unattributed} seeded opportunities have no merchant"

    by_merchant = seeded_db.execute(
        "SELECT m.merchant_id, m.cohort, COUNT(o.opportunity_id) AS n, "
        "COALESCE(SUM(o.amount_at_risk),0) AS exposure "
        "FROM merchants m LEFT JOIN opportunities o "
        "ON o.merchant_id = m.merchant_id GROUP BY m.merchant_id").fetchall()
    assert len(by_merchant) > 1, "single-merchant dataset cannot exercise scope"
    assert sum(r["n"] for r in by_merchant) == seeded_db.execute(
        "SELECT COUNT(*) FROM opportunities").fetchone()[0]


@pytest.mark.gate("phase1.merchant_scope")
def test_customer_and_opportunity_merchant_attribution_agree(seeded_db):
    """
    Two paths to the same merchant is fine; two paths to *different*
    merchants would make every per-merchant number depend on which join the
    reader happened to pick.
    """
    conflicts = seeded_db.execute(
        "SELECT o.opportunity_id, o.merchant_id AS via_opportunity, "
        "c.merchant_id AS via_customer FROM opportunities o "
        "JOIN customers c ON c.customer_id = o.customer_id "
        "WHERE o.merchant_id IS NOT NULL AND c.merchant_id IS NOT NULL "
        "AND o.merchant_id != c.merchant_id").fetchall()
    assert not conflicts, \
        f"merchant attribution disagrees for: {[tuple(r) for r in conflicts[:5]]}"


# --------------------------------------------------------------------------
# Gate: bank / method / PSP health with explicit time windows
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.network_health")
def test_bank_health_observations_are_window_scoped(empty_db):
    required = {"bank", "method", "psp", "window_start", "window_end",
                "success_rate", "timeout_rate", "health_score"}
    missing = required - _columns(empty_db, "bank_health_observations")
    assert not missing, f"bank_health_observations missing columns: {sorted(missing)}"

    base = recent_in_window_ts(days_ago=2)
    for offset, success in ((0, 0.98), (3600, 0.55)):
        empty_db.execute(
            "INSERT INTO bank_health_observations (bank, method, psp, "
            "window_start, window_end, success_rate, timeout_rate, health_score) "
            "VALUES ('HDFC','card','razorpay',?,?,?,?,?)",
            (base + offset, base + offset + 3600, success, 1 - success, success))
    empty_db.commit()

    degraded = empty_db.execute(
        "SELECT success_rate FROM bank_health_observations "
        "WHERE bank='HDFC' AND method='card' AND window_start >= ? "
        "ORDER BY window_start DESC LIMIT 1", (base,)).fetchone()
    assert degraded["success_rate"] == 0.55, (
        "the same bank/method/psp must be able to hold different health in "
        "different windows, otherwise the signal is not time-scoped")


# --------------------------------------------------------------------------
# Gate: experiment_assignment holds exactly one group per opportunity
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.experiment_assignment")
def test_experiment_assignment_is_unique_per_opportunity_by_schema(seeded_db):
    """
    Enforced by `opportunity_id TEXT PRIMARY KEY`, not by application code.
    An opportunity in both arms silently destroys the causal claim the
    holdout exists to support, so the second write must be rejected by the
    database rather than avoided by a careful caller.
    """
    opp = make_opportunity(seeded_db, opportunity_id="opp_exp_0001")
    now = recent_in_window_ts()
    seeded_db.execute(
        "INSERT INTO experiment_assignment (opportunity_id, \"group\", "
        "assigned_at, assignment_method) VALUES (?, 'treatment', ?, 'hash')",
        (opp["opportunity_id"], now))
    seeded_db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute(
            "INSERT INTO experiment_assignment (opportunity_id, \"group\", "
            "assigned_at, assignment_method) VALUES (?, 'control', ?, 'hash')",
            (opp["opportunity_id"], now))
        seeded_db.commit()
    seeded_db.rollback()

    rows = seeded_db.execute(
        "SELECT \"group\" FROM experiment_assignment WHERE opportunity_id = ?",
        (opp["opportunity_id"],)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "treatment", \
        f"assignment is not single-valued: {[tuple(r) for r in rows]}"


@pytest.mark.gate("phase1.experiment_assignment")
def test_assignment_requires_an_existing_opportunity(seeded_db):
    """FK enforcement, so no arm can contain a case that does not exist."""
    with pytest.raises(sqlite3.IntegrityError):
        seeded_db.execute(
            "INSERT INTO experiment_assignment (opportunity_id, \"group\", "
            "assigned_at, assignment_method) "
            "VALUES ('opp_ghost_9999', 'control', 0, 'hash')")
        seeded_db.commit()
    seeded_db.rollback()


# --------------------------------------------------------------------------
# Gate: pipeline regression after the migration
# --------------------------------------------------------------------------

@pytest.mark.gate("phase1.pipeline_regression")
def test_second_pass_over_the_same_opportunity_is_blocked_by_cooldown(seeded_db):
    """
    The behavioural regression the migration had to preserve: contact once,
    then the 24h cooldown must block the next attempt on the same
    opportunity. Written against a synthetic opportunity with an explicitly
    recent, in-window created_at so the branch reached does not depend on
    the age of the seed fixture.
    """
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action
    from backend.engine.execute_action import execute_action

    opp = make_opportunity(seeded_db, opportunity_id="opp_cooldown_0001",
                           created_at=recent_in_window_ts(days_ago=1))
    make_payment(seeded_db, opp["opportunity_id"], payment_id="pay_cooldown_0001")
    classification = classify(opp["event_type"], opp["root_cause"])

    first = decide_action(opp, classification, seeded_db)
    assert first["outcome"] == "executed" and first["action_type"] == "retry", \
        f"first pass did not execute a retry: {first}"
    execute_action(opp, first, seeded_db)

    second = decide_action(opp, classification, seeded_db)
    assert second["outcome"] == "blocked_cooldown", \
        f"second pass returned {second['outcome']}, expected blocked_cooldown"
    assert second["allowed"] is False


@pytest.mark.gate("phase1.migration")
def test_dataset_registry_can_hold_a_reproducibility_manifest(empty_db):
    """
    Structural only -- Phase 2 populates it. Asserted here because this table
    is the intended home for the dataset-provenance record that the permanent
    gate currently has no verifiable artifact for.
    """
    required = {"dataset_name", "version", "seed", "generator_version",
                "row_count", "case_count", "validator_results", "created_at"}
    missing = required - _columns(empty_db, "dataset_registry")
    assert not missing, f"dataset_registry missing columns: {sorted(missing)}"

    empty_db.execute(
        "INSERT INTO dataset_registry (dataset_name, version, seed, "
        "generator_version, row_count, case_count, validator_results, created_at) "
        "VALUES ('training_corpus', 'v1', 42, 'sim-v1', 22016, 8000, '{}', ?)",
        (recent_in_window_ts(),))
    empty_db.commit()
    row = empty_db.execute("SELECT * FROM dataset_registry").fetchone()
    assert row["seed"] == 42 and row["row_count"] == 22016
