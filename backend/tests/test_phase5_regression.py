"""
Phase 5 -- backward-compatibility regression against the W1 golden corpus.

The Phase 5 acceptance gate requires that decisions produced with the
optimizer disabled are *identical* to the pre-optimizer behaviour, and that
this is proven rather than asserted. This module is that proof.

Tolerance, committed before the evaluation it governs: **exact equality, zero
tolerance**, on the full decision dict including which keys are present. This
is a determinism check, not a statistical one, so there is no threshold to
loosen -- a single differing field is a failure.

The corpus itself is `tests/golden/phase5_decide_action_golden.json`, captured
by `tests/phase5_scenarios.py` against decide_action.py before any Phase 5
edit. See that module for how determinism is pinned.
"""

import json

import pytest

from backend.tests import phase5_scenarios as ps


@pytest.fixture
def golden():
    if not ps.GOLDEN_PATH.exists():
        pytest.fail(
            f"golden corpus missing at {ps.GOLDEN_PATH}. It is a committed "
            "artifact captured before Phase 5 edits; it cannot be legitimately "
            "regenerated from the current tree."
        )
    return json.loads(ps.GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.gate("phase5.backward_compatibility")
def test_the_corpus_was_captured_in_the_same_ml_regime(golden):
    """
    `ml_recovery_probability` comes from a gitignored model artifact. A
    checkout without it returns None for every scenario, which would make the
    comparison pass trivially while proving nothing about the advisory field.
    Refuse to compare across regimes rather than report a hollow pass.
    """
    assert golden["ml_regime"] == ps._ml_regime(), (
        f"corpus captured under ml_regime={golden['ml_regime']} but this "
        f"environment is {ps._ml_regime()}. Restore backend/ml/models/"
        "xgb_model.joblib (gitignored, copy it from a checkout that has it) "
        "before trusting this comparison."
    )


@pytest.mark.gate("phase5.backward_compatibility")
def test_the_scenario_set_has_not_shrunk(golden):
    """A regression suite that quietly loses scenarios stops being one."""
    missing = set(golden["decisions"]) - {name for name, _ in ps.SCENARIOS}
    assert not missing, f"scenarios dropped since the corpus was captured: {sorted(missing)}"
    assert len(ps.SCENARIOS) >= golden["scenario_count"]


@pytest.mark.gate("phase5.backward_compatibility")
def test_decisions_are_unchanged_with_the_optimizer_disabled(empty_db, golden):
    """
    The whole point of Phase 5's backward-compatibility contract, in one
    assertion: every branch of decide_action(), called the way every existing
    caller calls it, still returns exactly what it returned before.
    """
    current = ps.capture_all(empty_db)
    expected = golden["decisions"]

    diffs = []
    for name in sorted(expected):
        want, got = expected[name], current.get(name)
        if got is None:
            diffs.append(f"{name}: scenario produced no decision")
            continue
        if got == want:
            continue
        for key in sorted(set(want) | set(got)):
            if key not in want:
                diffs.append(f"{name}.{key}: key ADDED (was absent) -> {got[key]!r}")
            elif key not in got:
                diffs.append(f"{name}.{key}: key REMOVED (was {want[key]!r})")
            elif want[key] != got[key]:
                diffs.append(f"{name}.{key}: {want[key]!r} -> {got[key]!r}")

    assert not diffs, (
        "decide_action() output drifted from the pre-Phase-5 baseline "
        f"({len(diffs)} field(s)):\n  " + "\n  ".join(diffs))


@pytest.mark.gate("phase5.backward_compatibility")
def test_the_corpus_still_covers_every_outcome_the_engine_can_emit(empty_db):
    """
    Guards the corpus against becoming a weaker check over time: every outcome
    value decide_action() is actually capable of emitting must appear in it.

    Compares against the *emitted* set rather than the declared
    DECISION_OUTCOMES vocabulary. The two now agree -- Phase 5 removed
    `blocked_max_retries`, which was declared but had no producer in any
    commit in the project's history (the max-retries branch emits
    stop/executed). Asserting on the emitted set is still the stronger check:
    it fails if a branch stops firing, whereas a subset check against the
    declared tuple would not.
    """
    emitted = {d["outcome"] for d in ps.capture_all(empty_db).values()}
    assert emitted == {
        "executed",
        "blocked_cooldown",
        "blocked_contact_hours",
        "blocked_already_escalated",
        "blocked_already_stopped",
        "flagged_manual_review",
    }, f"outcome coverage of the golden corpus changed: {sorted(emitted)}"


@pytest.mark.gate("permanent.single_authority")
def test_blocked_max_retries_remains_unreachable(empty_db):
    """
    Retained after the value's removal from DECISION_OUTCOMES, because the
    removal is exactly what makes accidental reintroduction plausible: nothing
    would now reject the string at write time. If a future change starts
    emitting it, that is a real change to the closed compliance vocabulary and
    must be a deliberate, reviewed decision -- not a side effect of Phase 5's
    fallthrough loop, whose natural failure mode (no compliant candidate left)
    routes to flagged_manual_review instead. See PHASE5_NOTES.md section 1.
    """
    emitted = {d["outcome"] for d in ps.capture_all(empty_db).values()}
    assert "blocked_max_retries" not in emitted, (
        "blocked_max_retries is now emitted. It was unreachable at the Phase 5 "
        "baseline (max-retries emits stop/executed). Confirm this was intended "
        "and update the sign-off record.")
