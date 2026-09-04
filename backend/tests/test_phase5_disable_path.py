"""
Phase 5 / W4 -- the disable path, proven rather than documented.

The acceptance gate is specific: the optimizer must be turnable off "via
configuration at runtime" with decisions "immediately reverting to
pre-optimizer behaviour", and this must be "exercised by an actual test that
flips the flag mid-run, not just documented as possible". So every test here
flips `phase5_config.OPTIMIZER_PATHWAY_ENABLED` inside a single running
process and asserts on what changes.

The strongest form of the claim, and the one this file is built around: with
the switch off, supplying a ranked list must produce output byte-identical to
the W1 golden corpus -- not merely "similar", not "the same action", but the
same dict including which keys are present. If a caller can leave the
optimizer wired in and still get exactly the pre-Phase-5 decision, the disable
path is real.
"""

import ast
from pathlib import Path

import pytest

from backend.engine import decide_action as da_module
from backend.engine import phase5_config as cfg
from backend.tests import phase5_scenarios as ps
from backend.tests.conftest import make_opportunity, make_payment, recent_in_window_ts

SOURCE = Path(da_module.__file__)


def _candidate(action, rank, **extra):
    row = {"action_type": action, "rank": rank, "timing": "immediate",
           "timing_hours": 0.0, "method": "n/a", "channel": "email",
           "predicted_eiv": 100.0 - rank, "candidate_id": 2000 + rank}
    row.update(extra)
    return row


def _decide(conn, opportunity, ranked=None, **kwargs):
    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action

    classification = classify(opportunity["event_type"],
                              opportunity.get("root_cause"))
    return decide_action(opportunity, classification, conn,
                         ranked_candidates=ranked, **kwargs)


# --------------------------------------------------------------------------
# The switch is wired so that a mid-run flip can actually work
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.disable_path")
def test_the_kill_switch_is_not_bound_at_import_time():
    """
    A `from phase5_config import OPTIMIZER_PATHWAY_ENABLED` would bind the
    value once at import, and every later flip would be silently ignored --
    the disable path would look present and be inert. This asserts the switch
    is reached through the module instead.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), str(SOURCE))
    from_imported = {alias.name for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom)
                     and node.module == "backend.engine.phase5_config"
                     for alias in node.names}
    assert "OPTIMIZER_PATHWAY_ENABLED" not in from_imported, (
        "the kill switch is from-imported; a mid-run flip would not take "
        "effect and the disable path would be inert")

    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute)
             and n.attr == "OPTIMIZER_PATHWAY_ENABLED"]
    assert reads, "decide_action.py never reads the kill switch"


@pytest.mark.gate("phase5.disable_path")
def test_the_switch_defaults_on_so_the_pathway_exists():
    """
    Distinct from the entry-point table, which is what keeps the optimizer off
    in normal operation. This is the emergency disable, not the deployment
    default.
    """
    assert cfg.OPTIMIZER_PATHWAY_ENABLED is True

    # AMENDMENT, Phase 7, 2026-09-04. This previously asserted
    # `not any(OPTIMIZER_ENABLED_BY_ENTRY_POINT.values())`, which encoded the
    # entry-point table's state at Phase 5 rather than the property the test
    # is named for. Phase 7 enables the optimizer at the creation entry point
    # by ruling, so that live opportunities carry the candidate the rule
    # engine selected and the predicted-versus-observed diagnostic has
    # something to compare.
    #
    # NOT a weakening: pinning the exact table is stronger than `not any`,
    # because it also fails if `customer_reply` or `dispatch` is ever enabled
    # silently -- which `not any` could only catch while everything was off.
    # The point of the test is unchanged: the kill switch is separate from the
    # entry-point table, and it stays armed.
    assert cfg.OPTIMIZER_ENABLED_BY_ENTRY_POINT == {
        "batch": False,
        "dispatch": False,
        "trigger_event": True,
        "customer_reply": False,
    }, ("the optimizer entry-point table has drifted from the ruled state; "
        "enabling it anywhere else is a decision, not a default")


# --------------------------------------------------------------------------
# Flipping it mid-run
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.disable_path")
def test_flipping_the_switch_mid_run_immediately_reverts_behaviour(empty_db, monkeypatch):
    """
    The gate's exact requirement, in one process: same opportunity, same
    ranked list, three calls, one flip between them.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    ranked = [_candidate("do_nothing", 1), _candidate("reminder", 2)]

    # ON -- the optimizer's list is honoured, do_nothing falls through.
    assert cfg.OPTIMIZER_PATHWAY_ENABLED is True
    enabled = _decide(empty_db, opportunity, ranked=ranked)
    assert enabled["action_type"] == "reminder"
    assert enabled["candidate_id"] == 2002

    # FLIP, with no reimport, no module reload, no process restart.
    monkeypatch.setattr(cfg, "OPTIMIZER_PATHWAY_ENABLED", False)

    disabled = _decide(empty_db, opportunity, ranked=ranked)
    hardcoded = _decide(empty_db, opportunity)

    # Reverted: the ranked list is ignored entirely.
    assert disabled == hardcoded
    assert "candidate_id" not in disabled
    assert disabled["action_type"] == "retry"   # the hardcoded default
    assert disabled != enabled

    # FLIP BACK -- the pathway returns without a restart.
    monkeypatch.setattr(cfg, "OPTIMIZER_PATHWAY_ENABLED", True)
    assert _decide(empty_db, opportunity, ranked=ranked) == enabled


@pytest.mark.gate("phase5.disable_path")
def test_the_switch_off_ignores_even_a_method_change_list(empty_db, monkeypatch):
    """
    Disabling the pathway must not create a hole in the permanent boundary.
    With the switch off the list is ignored wholesale, so a method-change
    candidate cannot be selected by that route either.
    """
    opportunity = make_opportunity(empty_db, root_cause="expired_card",
                                   created_at=recent_in_window_ts())
    make_payment(empty_db, opportunity["opportunity_id"], method="card")
    ranked = [_candidate("retry", 1, method="upi", method_changed=True)]

    monkeypatch.setattr(cfg, "OPTIMIZER_PATHWAY_ENABLED", False)
    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision == _decide(empty_db, opportunity)
    assert "method" not in decision
    assert decision["action_type"] == "retry"   # hardcoded default, current method


# --------------------------------------------------------------------------
# The strong form: full golden corpus with the optimizer wired in but off
# --------------------------------------------------------------------------

def _corpus_with_lists_supplied(conn):
    """
    Re-run every golden scenario, but pass a ranked list on every call. With
    the switch off, all of them must be ignored.

    The list deliberately leads with candidates that WOULD change the outcome
    if honoured -- a method change and a do_nothing -- so a switch that failed
    to disable would show up as a diff rather than coincidentally agreeing.
    """
    from unittest import mock

    from backend.engine.classify import classify
    from backend.engine.decide_action import decide_action

    provocative = [
        _candidate("retry", 1, method="upi", method_changed=True),
        _candidate("do_nothing", 2),
        _candidate("escalate", 3),
    ]

    captured = {}
    with mock.patch("backend.engine.decide_action.time.time",
                    return_value=float(ps.FROZEN_NOW)):
        for name, build in ps.SCENARIOS:
            oid = f"opp_disabled_{name}"
            kwargs = build(conn, oid)
            opportunity = dict(conn.execute(
                "SELECT * FROM opportunities WHERE opportunity_id = ?",
                (oid,)).fetchone())
            row = conn.execute(
                "SELECT * FROM payments WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()
            latest_payment = dict(row) if row else None
            classification = classify(
                opportunity["event_type"],
                latest_payment.get("error_reason") if latest_payment
                else opportunity.get("root_cause"))
            captured[name] = decide_action(
                opportunity, classification, conn,
                latest_payment=latest_payment,
                ranked_candidates=list(provocative), **kwargs)
    return captured


@pytest.mark.gate("phase5.disable_path")
def test_the_whole_golden_corpus_is_reproduced_with_lists_supplied_but_disabled(
        empty_db, monkeypatch):
    """
    Backward compatibility in its strongest form: the optimizer left wired in,
    the switch off, and every one of the 25 golden scenarios reproducing its
    pre-Phase-5 decision exactly -- including which keys are present.

    Tolerance is phase5_config.REGRESSION_FIELD_TOLERANCE (0), committed at W2
    before this evaluation was written.
    """
    golden = ps.GOLDEN_PATH
    assert golden.exists(), f"golden corpus missing at {golden}"
    import json
    expected = json.loads(golden.read_text(encoding="utf-8"))["decisions"]

    monkeypatch.setattr(cfg, "OPTIMIZER_PATHWAY_ENABLED", False)
    current = _corpus_with_lists_supplied(empty_db)

    diffs = []
    for name in sorted(expected):
        want, got = expected[name], current.get(name)
        if got == want:
            continue
        if got is None:
            diffs.append(f"{name}: no decision produced")
            continue
        for key in sorted(set(want) | set(got)):
            if key not in want:
                diffs.append(f"{name}.{key}: key ADDED -> {got[key]!r}")
            elif key not in got:
                diffs.append(f"{name}.{key}: key REMOVED (was {want[key]!r})")
            elif want[key] != got[key]:
                diffs.append(f"{name}.{key}: {want[key]!r} -> {got[key]!r}")

    assert len(diffs) == cfg.REGRESSION_FIELD_TOLERANCE, (
        f"disabled pathway diverged from the pre-Phase-5 baseline in "
        f"{len(diffs)} field(s):\n  " + "\n  ".join(diffs))


@pytest.mark.gate("phase5.disable_path")
def test_the_same_corpus_does_diverge_when_the_switch_is_on(empty_db):
    """
    Negative control for the test above. If the provocative lists produced the
    golden result whether or not the switch was on, the previous test would
    prove nothing about the switch -- it would just mean the lists never
    mattered. At least one scenario must differ with the pathway enabled.
    """
    import json
    expected = json.loads(ps.GOLDEN_PATH.read_text(encoding="utf-8"))["decisions"]

    assert cfg.OPTIMIZER_PATHWAY_ENABLED is True
    current = _corpus_with_lists_supplied(empty_db)

    changed = [n for n in expected if current.get(n) != expected[n]]
    assert changed, (
        "the provocative ranked lists changed nothing even with the pathway "
        "enabled, so the disable-path test above is vacuous")
