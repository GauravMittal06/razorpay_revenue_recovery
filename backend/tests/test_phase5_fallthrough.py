"""
Phase 5 / W3 -- the optimizer-driven path of decide_action().

Two halves:

* **Static authority checks.** The rule engine must consume the optimizer's
  ranking in the order given and must not read the rupee-space score. Both are
  properties of the source, not of any one execution, so they are checked by
  parsing the module rather than by sampling behaviour.

* **Behavioural fallthrough.** That a blocked top candidate falls through to
  the next executable one, that a payment-method change never wins, and that
  exhausting the list routes to manual review rather than to `stop`.
"""

import ast
import time
from datetime import datetime
from pathlib import Path

import pytest

from backend.engine import decide_action as da_module
from backend.engine import phase5_config as cfg
from backend.engine.decide_action import (CONTACT_WINDOW_END,
                                          CONTACT_WINDOW_START, decide_action)
from backend.tests.conftest import (insert_decision, make_opportunity,
                                    make_payment, outside_window_ts,
                                    recent_in_window_ts)

SOURCE = Path(da_module.__file__)
HOUR = 3600
DAY = 86400

# Functions that make up the Phase 5 path. The static checks apply to these;
# the hardcoded body is out of scope because it never sees ranked_candidates.
PHASE5_FUNCTIONS = {
    "decide_action",
    "_decide_action_from_ranked",
    "_candidate_block_reason",
    "_is_method_change",
    "_within_contact_window",
}


def _phase5_nodes():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), str(SOURCE))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in PHASE5_FUNCTIONS]


def _candidate(action, rank, **extra):
    row = {"action_type": action, "rank": rank, "timing": "immediate",
           "timing_hours": 0.0, "method": "n/a", "channel": "email",
           "predicted_eiv": 100.0 - rank, "candidate_id": 1000 + rank}
    row.update(extra)
    return row


def _classify_for(opportunity):
    from backend.engine.classify import classify
    return classify(opportunity["event_type"], opportunity.get("root_cause"))


def _decide(conn, opportunity, ranked=None, **kwargs):
    return decide_action(opportunity, _classify_for(opportunity), conn,
                         ranked_candidates=ranked, **kwargs)


# --------------------------------------------------------------------------
# Static: ranking authority stays with the optimizer
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.single_authority")
def test_the_rule_engine_never_reorders_the_ranked_list():
    """
    Ruling 3: `decide_action()` consumes ranked_candidates in the order given.
    Re-sorting here -- even by the better-evidenced probability signal -- would
    relocate "ranking by expected incremental value" out of the optimizer,
    which owns it exclusively (EXECUTION_PLAN.md:83).
    """
    offenders = []
    for func in _phase5_nodes():
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in ("sorted", "sort", "reverse", "reversed"):
                offenders.append(f"{func.name}:{node.lineno} calls {name}()")
    assert not offenders, (
        "the rule engine reorders the optimizer's ranking:\n  "
        + "\n  ".join(offenders))


@pytest.mark.gate("permanent.single_authority")
def test_the_rule_engine_never_reads_the_rupee_score():
    """
    predicted_eiv carries a measured ~16% pair-order sensitivity to noise in
    the model's amount head (PHASE4_NOTES.md section 8.6). Reading it here --
    to threshold on it, or to break a tie -- would import that noise into an
    execution gate. Docstrings are excluded: the boundary may be documented,
    only not implemented.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), str(SOURCE))
    docstrings = {id(ast.get_docstring(n, clean=False))
                  for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                    ast.AsyncFunctionDef))}
    offenders = [f"line {n.lineno}" for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and "predicted_eiv" in n.value and id(n.value) not in docstrings]
    assert not offenders, ("decide_action.py reads predicted_eiv: "
                           + ", ".join(offenders))


@pytest.mark.gate("permanent.single_authority")
def test_the_boundary_constants_are_imported_not_redefined():
    """
    METHOD_CHANGE_IS_EXECUTABLE and EXHAUSTION_OUTCOME are recorded rulings.
    A second inline definition of either -- a literal "flagged_manual_review"
    in the exhaustion return, say -- would mean the ruling and the code could
    drift apart silently.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), str(SOURCE))
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "backend.engine.phase5_config"
                for alias in node.names}
    assert {"METHOD_CHANGE_IS_EXECUTABLE", "EXHAUSTION_OUTCOME",
            "EXECUTABLE_ACTIONS", "MAX_FALLTHROUGH_CANDIDATES"} <= imported

    assigned = {t.id for node in ast.walk(tree)
                if isinstance(node, ast.Assign) for t in node.targets
                if isinstance(t, ast.Name)}
    assert "METHOD_CHANGE_IS_EXECUTABLE" not in assigned
    assert "EXHAUSTION_OUTCOME" not in assigned


@pytest.mark.gate("permanent.single_authority")
def test_no_branch_returns_a_payment_method(empty_db):
    """
    The executor must have no field through which a method change could ride.
    Checked on the returned contract across every path, not by reading source.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    make_payment(empty_db, opportunity["opportunity_id"])
    ranked = [_candidate("retry", 1, method="upi", method_changed=True),
              _candidate("reminder", 2)]

    for decision in (_decide(empty_db, opportunity),
                     _decide(empty_db, opportunity, ranked=ranked)):
        assert "method" not in decision
        assert "current_method" not in decision


# --------------------------------------------------------------------------
# The duplicated contact-window check is pinned to the hardcoded one
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.fallthrough")
@pytest.mark.parametrize("hour", list(range(24)))
def test_contact_window_helper_agrees_with_the_hardcoded_branch(empty_db, hour):
    """
    _within_contact_window() knowingly duplicates the condition inside the
    hardcoded body, which must stay literally unmodified. This asserts the two
    agree at every hour, so a change to one that is not mirrored is a failure
    rather than a silent divergence.
    """
    created_at = recent_in_window_ts(days_ago=1, hour=hour)
    opportunity = make_opportunity(empty_db, opportunity_id=f"opp_h{hour}",
                                   created_at=created_at)
    hardcoded_allows = _decide(empty_db, opportunity)["outcome"] != "blocked_contact_hours"
    assert da_module._within_contact_window(created_at) is hardcoded_allows, (
        f"hour {hour}: helper says "
        f"{da_module._within_contact_window(created_at)}, hardcoded path says "
        f"{hardcoded_allows}")


# --------------------------------------------------------------------------
# Behavioural: selection and fallthrough
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.fallthrough")
def test_the_top_ranked_executable_candidate_is_selected(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    ranked = [_candidate("reminder", 1), _candidate("retry", 2)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "reminder"
    assert decision["allowed"] is True
    assert decision["outcome"] == "executed"
    assert decision["triggered_by"] == "rule"
    assert decision["candidate_id"] == 1001


@pytest.mark.gate("phase5.fallthrough")
def test_a_do_nothing_top_pick_falls_through_to_the_next_executable(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    ranked = [_candidate("do_nothing", 1, method=None, channel=None),
              _candidate("reminder", 2)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "reminder"
    assert decision["candidate_id"] == 1002
    assert "do_nothing" in decision["reasoning"]


@pytest.mark.gate("permanent.single_authority")
def test_a_method_change_top_pick_is_never_selected(empty_db):
    """
    The central Phase 5 boundary. A method change is a retry carrying a method
    other than the opportunity's current one -- it outranks everything here and
    must still lose.
    """
    opportunity = make_opportunity(empty_db, root_cause="expired_card",
                                   created_at=recent_in_window_ts())
    make_payment(empty_db, opportunity["opportunity_id"], method="card")
    ranked = [_candidate("retry", 1, method="upi", method_changed=True),
              _candidate("reminder", 2)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "reminder"
    assert decision["candidate_id"] == 1002
    assert "payment-method change" in decision["reasoning"]


@pytest.mark.gate("permanent.single_authority")
def test_a_same_method_retry_is_not_mistaken_for_a_method_change(empty_db):
    """The guard must not block ordinary retries on the current instrument."""
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    make_payment(empty_db, opportunity["opportunity_id"], method="card")
    ranked = [_candidate("retry", 1, method="card")]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "retry"
    assert decision["allowed"] is True


@pytest.mark.gate("permanent.single_authority")
def test_a_method_changed_flag_alone_is_enough_to_block(empty_db):
    """
    Belt and braces: if the generator's flag and the derived comparison ever
    disagree, the safe direction wins.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    make_payment(empty_db, opportunity["opportunity_id"], method="card")
    ranked = [_candidate("retry", 1, method="card", method_changed=True),
              _candidate("reminder", 2)]

    assert _decide(empty_db, opportunity, ranked=ranked)["action_type"] == "reminder"


@pytest.mark.gate("phase5.fallthrough")
def test_payment_link_is_selectable(empty_db):
    """It is in the declared executable vocabulary as of Phase 5 (ruling 1)."""
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    ranked = [_candidate("payment_link", 1)]

    assert _decide(empty_db, opportunity, ranked=ranked)["action_type"] == "payment_link"


@pytest.mark.gate("phase5.fallthrough")
def test_exhausting_every_candidate_routes_to_manual_review_not_stop(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    make_payment(empty_db, opportunity["opportunity_id"], method="card")
    ranked = [_candidate("do_nothing", 1),
              _candidate("retry", 2, method="upi", method_changed=True)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["outcome"] == cfg.EXHAUSTION_OUTCOME == "flagged_manual_review"
    assert decision["action_type"] is None
    assert decision["allowed"] is False
    assert decision["action_type"] != "stop"


@pytest.mark.gate("phase5.fallthrough")
def test_contact_hours_block_contact_actions_but_not_escalation(empty_db):
    """
    The one genuinely candidate-dependent compliance rule, and therefore the
    case the fallthrough exists for.
    """
    opportunity = make_opportunity(empty_db, created_at=outside_window_ts())
    ranked = [_candidate("reminder", 1), _candidate("escalate", 2)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "escalate"
    assert "contact window" in decision["reasoning"]


@pytest.mark.gate("phase5.fallthrough")
def test_an_all_contact_list_outside_the_window_keeps_the_specific_block(empty_db):
    """
    When the fallthrough is attempted and finds nothing, the hardcoded path's
    specific reason is more informative than the generic exhaustion outcome,
    so it is what gets recorded. Otherwise the audit trail would lose "this was
    outside contact hours" and report only "nothing was executable".
    """
    opportunity = make_opportunity(empty_db, created_at=outside_window_ts())
    ranked = [_candidate("reminder", 1), _candidate("payment_link", 2)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["outcome"] == "blocked_contact_hours"
    assert decision["outcome"] != cfg.EXHAUSTION_OUTCOME
    assert decision == _decide(empty_db, opportunity)


@pytest.mark.gate("phase5.fallthrough")
def test_payment_link_respects_the_contact_window(empty_db):
    """
    payment_link never reached the window check pre-Phase-5 because it could
    not be a hardcoded default_action. It is customer contact by SoT section
    7's definition, so it is gated like retry and reminder.
    """
    opportunity = make_opportunity(empty_db, created_at=outside_window_ts())
    ranked = [_candidate("payment_link", 1), _candidate("escalate", 2)]

    assert _decide(empty_db, opportunity, ranked=ranked)["action_type"] == "escalate"


# --------------------------------------------------------------------------
# Behavioural: the ranked path never overturns the hardcoded path
# --------------------------------------------------------------------------

@pytest.mark.gate("permanent.single_authority")
@pytest.mark.parametrize("label,build,expected_outcome", [
    ("cooldown", lambda c, o: insert_decision(
        c, o["opportunity_id"], "retry", "executed",
        timestamp=int(time.time()) - 6 * HOUR), "blocked_cooldown"),
    ("already_stopped", lambda c, o: insert_decision(
        c, o["opportunity_id"], "stop", "executed"), "blocked_already_stopped"),
    ("already_escalated", lambda c, o: insert_decision(
        c, o["opportunity_id"], "escalate", "executed"),
     "blocked_already_escalated"),
])
def test_a_blocked_opportunity_is_never_unblocked_by_a_ranked_list(
        empty_db, label, build, expected_outcome):
    """
    Every blocking rule is opportunity-scoped: it blocks all candidates
    equally. So a block must never be a reason to try a different candidate --
    falling through one would be the ranked path overturning a compliance
    decision, which is the exact authority inversion Phase 5 must not create.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    build(empty_db, opportunity)
    ranked = [_candidate("reminder", 1), _candidate("escalate", 2)]

    with_list = _decide(empty_db, opportunity, ranked=ranked)
    without = _decide(empty_db, opportunity)

    assert with_list["outcome"] == expected_outcome
    assert with_list["allowed"] is False
    assert with_list == without, f"{label}: ranked path diverged from hardcoded"


@pytest.mark.gate("permanent.single_authority")
def test_the_attempt_ceiling_stop_is_not_overridden(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    for days in (5, 4, 3):
        insert_decision(empty_db, opportunity["opportunity_id"], "retry",
                        "executed", timestamp=int(time.time()) - days * DAY)
    ranked = [_candidate("reminder", 1)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "stop"
    assert decision == _decide(empty_db, opportunity)


@pytest.mark.gate("permanent.single_authority")
def test_auto_escalation_after_silence_is_not_overridden(empty_db):
    opportunity = make_opportunity(
        empty_db, created_at=recent_in_window_ts(days_ago=9))
    ranked = [_candidate("reminder", 1)]

    decision = _decide(empty_db, opportunity, ranked=ranked)

    assert decision["action_type"] == "escalate"
    assert decision == _decide(empty_db, opportunity)


@pytest.mark.gate("permanent.single_authority")
def test_a_dispute_still_hard_stops_with_a_ranked_list(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    ranked = [_candidate("reminder", 1)]

    decision = _decide(empty_db, opportunity, ranked=ranked, dispute_flag=True)

    assert decision["outcome"] == "flagged_manual_review"
    assert decision["flag_type"] == "dispute_flag"


# --------------------------------------------------------------------------
# Declared bounds are enforced
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.declared_bounds")
def test_a_ranked_list_over_the_declared_ceiling_is_rejected(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    oversized = [_candidate("reminder", i + 1)
                 for i in range(cfg.MAX_FALLTHROUGH_CANDIDATES + 1)]

    with pytest.raises(ValueError) as exc:
        _decide(empty_db, opportunity, ranked=oversized)
    assert "exceeds the declared ceiling" in str(exc.value)


@pytest.mark.gate("phase5.fallthrough")
def test_an_empty_ranked_list_routes_to_manual_review(empty_db):
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())

    decision = _decide(empty_db, opportunity, ranked=[])

    assert decision["outcome"] == cfg.EXHAUSTION_OUTCOME
    assert decision["allowed"] is False


@pytest.mark.gate("phase5.fallthrough")
def test_the_selection_reasoning_discloses_rank_and_confidence(empty_db):
    """
    Where a fallthrough landed is only interpretable against how confident the
    ranking was at that point -- a near-tie skip costs nothing, a
    high-confidence skip is a real concession. Disclosure only; never a gate.
    """
    opportunity = make_opportunity(empty_db, created_at=recent_in_window_ts())
    ranked = [_candidate("do_nothing", 1),
              _candidate("reminder", 2, eiv_confidence="low",
                         eiv_gap_to_next=0.0031)]

    reasoning = _decide(empty_db, opportunity, ranked=ranked)["reasoning"]

    assert "rank 2" in reasoning
    assert "low" in reasoning
    assert "0.0031" in reasoning
