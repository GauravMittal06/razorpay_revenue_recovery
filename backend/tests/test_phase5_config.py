"""
Phase 5 / W2 -- the declared bounds are enforced, not just written down.

The Phase 4 precedent (its acceptance gate, and STATE_AND_DECISIONS.md:409)
is that a declared bound must be mechanically enforced rather than merely
measured, and that `assert` is not enforcement because it is stripped under
`python -O`. These tests are the mechanical half of that for Phase 5.

Two kinds of check here:

1. **Derivation checks.** Several bounds claim to be derived from a frozen
   Phase 3/4 module. If that module changes and the derived bound does not,
   the claim in the comment becomes false while the code keeps running. These
   tests make that drift a failure.

2. **Ruling checks.** Values that encode a recorded ruling rather than a
   measurement. These pin the ruling so reversing it has to be deliberate.
"""

import importlib

import pytest

from backend.engine import optimizer_config as phase4
from backend.engine import phase5_config as cfg


# --------------------------------------------------------------------------
# Derivation checks -- bounds tied to frozen modules
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.declared_bounds")
def test_schedule_horizon_matches_the_shared_generators_longest_timing():
    """
    MAX_SCHEDULE_HORIZON_HOURS claims to be the largest value in the shared
    candidate generator's TIMING_HOURS. If a longer timing window is ever
    added there, the executor would silently refuse to schedule it.
    """
    from backend.data_factory.candidate_generation import TIMING_HOURS

    assert cfg.MAX_SCHEDULE_HORIZON_HOURS == max(TIMING_HOURS.values()), (
        f"declared horizon {cfg.MAX_SCHEDULE_HORIZON_HOURS}h no longer matches "
        f"the generator's longest timing {max(TIMING_HOURS.values())}h "
        f"({TIMING_HOURS})")


@pytest.mark.gate("phase5.declared_bounds")
def test_immediate_timing_matches_the_generators_immediate():
    from backend.data_factory.candidate_generation import TIMING_HOURS

    assert cfg.IMMEDIATE_TIMING_HOURS == TIMING_HOURS["immediate"]


@pytest.mark.gate("phase5.declared_bounds")
def test_fallthrough_ceiling_is_the_phase4_candidate_ceiling():
    """
    The rule engine cannot be asked to walk further than the optimizer is
    allowed to propose. Tying the two means a Phase 4 config change surfaces
    here rather than silently truncating a longer ranked list.
    """
    assert cfg.MAX_FALLTHROUGH_CANDIDATES == phase4.MAX_CANDIDATES


@pytest.mark.gate("phase5.declared_bounds")
def test_latency_budget_is_imported_not_redeclared():
    """
    Phase 5 must not declare its own, more generous latency budget -- that
    would be loosening a bound to obtain a pass. Identity, not equality: a
    equal-but-separate literal would drift the moment Phase 4's changed.
    """
    assert cfg.LATENCY_BUDGET_MS is phase4.LATENCY_BUDGET_MS


# --------------------------------------------------------------------------
# Ruling checks
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.declared_bounds")
def test_executable_vocabulary_matches_the_execution_plan():
    """
    EXECUTION_PLAN.md:206 names the executable set verbatim: "retry, reminder
    (with a channel attribute), payment link, escalate, stop".
    """
    assert set(cfg.EXECUTABLE_ACTIONS) == {
        "retry", "reminder", "payment_link", "escalate", "stop"}


@pytest.mark.gate("phase5.declared_bounds")
def test_every_candidate_action_is_classified_exactly_once():
    """
    Every action the optimizer can rank must be either executable or
    explicitly recorded as evaluable-only. An action in neither list is one
    whose executability nobody decided -- which is how a boundary erodes.
    """
    from backend.data_factory.candidate_generation import ACTION_TYPES

    executable = set(cfg.EXECUTABLE_ACTIONS)
    evaluable_only = set(cfg.EVALUABLE_BUT_NOT_EXECUTABLE_ACTIONS)

    unclassified = set(ACTION_TYPES) - executable - evaluable_only
    assert not unclassified, (
        f"candidate action(s) with no declared executability: "
        f"{sorted(unclassified)}")
    assert not (executable & evaluable_only)


@pytest.mark.gate("phase5.declared_bounds")
def test_stop_is_executable_but_never_a_candidate():
    """
    The asymmetry runs both ways and is deliberate: `do_nothing` is ranked but
    not executable, and `stop` is executable but never proposed by the
    optimizer (it is reached only by the rule engine's own max-retries
    branch). Pinned so neither direction changes silently.
    """
    from backend.data_factory.candidate_generation import ACTION_TYPES

    assert "stop" in cfg.EXECUTABLE_ACTIONS
    assert "stop" not in ACTION_TYPES
    assert "do_nothing" in ACTION_TYPES
    assert "do_nothing" not in cfg.EXECUTABLE_ACTIONS


@pytest.mark.gate("permanent.single_authority")
def test_method_change_is_declared_non_executable():
    assert cfg.METHOD_CHANGE_IS_EXECUTABLE is False


@pytest.mark.gate("permanent.single_authority")
def test_exhaustion_routes_to_manual_review_and_never_to_stop():
    """
    See PHASE5_NOTES.md section 1.3: `stop` currently has exactly one producer
    (the max-retries branch), which is what makes
    `action_type='stop' AND outcome='executed'` an unambiguous query for
    "terminated by the retry ceiling". Routing exhaustion there would give the
    action a second meaning and silently corrupt that query.
    """
    from backend.db.db import DECISION_OUTCOMES

    assert cfg.EXHAUSTION_OUTCOME == "flagged_manual_review"
    assert cfg.EXHAUSTION_OUTCOME != "stop"
    assert cfg.EXHAUSTION_OUTCOME in DECISION_OUTCOMES


@pytest.mark.gate("phase5.declared_bounds")
def test_tolerances_are_zero_because_these_are_determinism_properties():
    assert cfg.REGRESSION_FIELD_TOLERANCE == 0
    assert cfg.DISPATCH_IDEMPOTENCY_EXPECTED_ROWS == 1
    assert cfg.DISPATCH_DUE_GRACE_SECONDS == 0


@pytest.mark.gate("phase5.declared_bounds")
def test_optimizer_defaults_off_at_every_entry_point():
    """
    The backward-compatible path must be what holds when nothing has been
    deliberately switched on.
    """
    assert cfg.OPTIMIZER_ENABLED_DEFAULT is False
    assert not any(cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT.values())
    assert set(cfg.ENTRY_POINTS) == {
        "batch", "dispatch", "trigger_event", "customer_reply"}


@pytest.mark.gate("phase5.declared_bounds")
def test_synchronous_entry_points_stay_off_while_the_latency_budget_is_unmet():
    """
    trigger_event and customer_reply are request-synchronous behind
    api/server.py. They must not be switched on while optimize_opportunity()
    is knowingly over its declared budget -- that would put a ~0.75s model
    call on a user-facing request path.
    """
    for entry_point in ("trigger_event", "customer_reply"):
        assert cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT[entry_point] is False, (
            f"{entry_point} is request-synchronous; enabling the optimizer "
            f"there requires the {cfg.LATENCY_BUDGET_MS}ms budget to be met "
            "first (PHASE4_HANDOFF section 3)")


# --------------------------------------------------------------------------
# The import-time guard actually guards
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.declared_bounds")
@pytest.mark.parametrize("attr,bad_value,expected", [
    ("METHOD_CHANGE_IS_EXECUTABLE", True, "permanent structural boundary"),
    ("EXHAUSTION_OUTCOME", "stop", "second meaning"),
    ("EXHAUSTION_OUTCOME", "blocked_max_retries", "closed"),
    ("DISPATCH_DUE_GRACE_SECONDS", 300, "before its scheduled time"),
])
def test_the_self_check_rejects_contradictory_values(monkeypatch, attr,
                                                     bad_value, expected):
    """
    Negative control. A guard that has never rejected anything is not known to
    work -- these prove _check() fails on each contradiction it claims to
    catch, rather than trusting that it would.
    """
    monkeypatch.setattr(cfg, attr, bad_value)
    with pytest.raises(ValueError) as exc:
        cfg._check()
    assert expected in str(exc.value)


@pytest.mark.gate("phase5.declared_bounds")
def test_the_module_still_imports_clean_after_the_negative_controls():
    """monkeypatch unwinds; a fresh import must pass its own guard."""
    importlib.reload(cfg)


# --------------------------------------------------------------------------
# Forcing function for W5
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.declared_bounds")
def test_the_executor_can_dispatch_every_declared_executable_action():
    """
    TIGHTENED 2026-09-02 (W5), exactly as this test's earlier form instructed.

    It was planted at W2 as a forcing function, asserting
    `missing == {"payment_link"}` -- pinning the one known gap between the
    declared executable vocabulary and what execute_action could dispatch, so
    that closing it would be a deliberate, visible step rather than a silent
    widening. Adding payment_link to STATUS_MAP tripped it as designed
    (observed: "executor gap changed: []"), and the instruction it carried was
    to tighten to plain equality rather than widen the expected gap. Done.
    """
    from backend.engine.execute_action import STATUS_MAP

    missing = set(cfg.EXECUTABLE_ACTIONS) - set(STATUS_MAP)
    assert not missing, (
        f"declared executable but not dispatchable: {sorted(missing)}")

    extra = set(STATUS_MAP) - set(cfg.EXECUTABLE_ACTIONS)
    assert not extra, (
        f"dispatchable but not declared executable: {sorted(extra)}")
