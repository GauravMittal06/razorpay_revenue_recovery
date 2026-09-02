"""
Phase 5 -- network health at serving time.

Before Phase 5 every live scoring ran at `network_health_known = 0.0`: payments
carried no channel, no health series was seeded, and optimize.py hardcoded
bank=None/psp=None/decision_time_hours=0.0. All three are closed now.

The tests that matter most here are the two tripwires:

* `test_the_rolling_health_value_actually_varies_across_opportunities` --
  the lookup is NOT honest past the end of its series. For an `as_of` beyond
  the last window it clamps (outcome_features.py:262) and returns the single
  final observation with `known=True`, forever. Every opportunity would then
  read an identical constant while the feature asserts the data is real, which
  is strictly worse than `known=0`. The chosen modulo mapping structurally
  cannot reach that state; this test is what would notice if a future change
  did.

* `test_the_frozen_exception_stayed_narrow` -- the exception granted for
  optimize.py was justified on the grounds that bank/psp/decision_time_hours
  are lookup keys, not model features. If any of them ever becomes a feature,
  the justification evaporates and the change stops being plumbing.
"""

import time

import pytest

from backend.engine import optimize
from backend.engine import phase5_config as cfg
from backend.ml import inference
from backend.ml import outcome_features as feats


def _live_contexts(conn, limit=None):
    ids = [r[0] for r in conn.execute(
        "SELECT opportunity_id FROM opportunities WHERE status IN ('open','recovering')"
    ).fetchall()]
    if limit:
        ids = ids[:limit]
    out = []
    for oid in ids:
        ctx, _ = optimize.load_context(conn, oid)
        if ctx is None:
            continue
        out.append((oid, ctx))
    return out


def _rows(conn, contexts):
    lookup = inference._get_health_lookup(conn)
    rows = []
    for _oid, ctx in contexts:
        cand = {"action_type": "retry", "timing": "immediate", "timing_hours": 0.0,
                "method": ctx.get("current_method"), "channel": "n/a",
                "method_changed": False}
        rows.append(feats.build_feature_row(ctx, cand, lookup))
    return rows


# --------------------------------------------------------------------------
# The mapping
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.network_health")
def test_the_mapping_always_lands_inside_the_truthful_range():
    """
    [WINDOW, HORIZON) is exactly the range where the lookup tells the truth:
    below WINDOW no observation has closed (known=False), and at or past
    HORIZON the clamp returns a stale constant claiming known=True.
    """
    lo, hi = cfg.HEALTH_WINDOW_HOURS, cfg.HEALTH_HORIZON_HOURS
    origin = cfg.NETWORK_HEALTH_ORIGIN_UNIX
    probes = [origin, origin - 10 ** 7, origin + 10 ** 7, origin + 10 ** 9, 0]
    probes += [origin + h * 3600 for h in (0, 1, 4, 2875, 2876, 2877, 5752)]
    for ts in probes:
        got = cfg.simulated_hour_for(ts)
        assert lo <= got < hi, f"ts={ts} mapped to {got}, outside [{lo}, {hi})"


@pytest.mark.gate("phase5.network_health")
def test_the_mapping_is_deterministic():
    ts = cfg.NETWORK_HEALTH_ORIGIN_UNIX + 123456
    assert cfg.simulated_hour_for(ts) == cfg.simulated_hour_for(ts)


@pytest.mark.gate("phase5.network_health")
def test_the_horizon_exceeds_the_trailing_average_span():
    """
    At horizon == trailing span every query averages from window 0 and the
    rolling value degenerates into a prefix average. Measured rolling-score
    spread: 168h -> 0.0864, 720h -> 0.1572, 2880h -> 0.2119.
    """
    assert cfg.HEALTH_HORIZON_HOURS > feats.NETWORK_HEALTH_WINDOW_HOURS


@pytest.mark.gate("phase5.network_health")
def test_the_seeded_horizon_and_the_mapping_modulus_are_one_constant():
    """
    Two independent literals would drift and the lookup would silently start
    clamping past the end of the series.

    Asserts equality AND that the seed generator imports the constant rather
    than defining its own. An earlier version of this test used `is`, which is
    not a valid way to compare ints -- it happened to hold at 2880 and broke at
    720, since neither is in CPython's small-integer cache. Equality is the
    real property; the import check is what actually rules out a second
    literal.
    """
    import ast
    from pathlib import Path

    from backend.data import generate_seed_data as gsd
    assert gsd.HEALTH_HORIZON_HOURS == cfg.HEALTH_HORIZON_HOURS

    source = Path(gsd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {t.id for node in ast.walk(tree)
                if isinstance(node, ast.Assign) for t in node.targets
                if isinstance(t, ast.Name)}
    assert "HEALTH_HORIZON_HOURS" not in assigned, (
        "generate_seed_data defines its own HEALTH_HORIZON_HOURS; it must "
        "import the one the live mapping takes its modulus from")
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "backend.engine.phase5_config"
                for alias in node.names}
    assert "HEALTH_HORIZON_HOURS" in imported


@pytest.mark.gate("phase5.network_health")
def test_the_window_constant_matches_the_generator():
    from backend.data_factory.bank_health_timeseries import WINDOW_HOURS
    assert cfg.HEALTH_WINDOW_HOURS == WINDOW_HOURS


# --------------------------------------------------------------------------
# The wiring
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.network_health")
def test_every_seeded_payment_names_a_channel(seeded_db):
    total = seeded_db.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    named = seeded_db.execute(
        "SELECT COUNT(*) FROM payments WHERE bank IS NOT NULL AND psp IS NOT NULL"
    ).fetchone()[0]
    assert total > 0
    assert named == total, f"{total - named} payments carry no channel"


@pytest.mark.gate("phase5.network_health")
def test_the_health_series_is_seeded(seeded_db):
    n = seeded_db.execute(
        "SELECT COUNT(*) FROM bank_health_observations").fetchone()[0]
    assert n > 0, "no health observations; every lookup would return known=False"


@pytest.mark.gate("phase5.network_health")
def test_the_optimizer_context_carries_the_real_channel(seeded_db):
    """The three lines the frozen-list exception covers."""
    contexts = _live_contexts(seeded_db, limit=10)
    assert contexts
    for oid, ctx in contexts:
        payment = seeded_db.execute(
            "SELECT bank, psp FROM payments WHERE opportunity_id = ?"
            " ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()
        if payment is None:
            continue
        assert ctx["bank"] == payment["bank"]
        assert ctx["psp"] == payment["psp"]
        assert cfg.HEALTH_WINDOW_HOURS <= ctx["decision_time_hours"] < cfg.HEALTH_HORIZON_HOURS


@pytest.mark.gate("phase5.network_health")
def test_live_scoring_now_runs_with_network_health_present(seeded_db):
    rows = _rows(seeded_db, _live_contexts(seeded_db))
    assert rows
    known = {r["network_health_known"] for r in rows}
    assert known == {1.0}, (
        f"network_health_known values across live contexts: {sorted(known)}; "
        "expected every live scoring to resolve to real observations")


# --------------------------------------------------------------------------
# The tripwires
# --------------------------------------------------------------------------

@pytest.mark.gate("phase5.network_health")
def test_the_rolling_health_value_actually_varies_across_opportunities(seeded_db):
    """
    THE tripwire. A constant rolling value with known=1.0 is what the
    past-the-end clamp produces, and it is indistinguishable from real data by
    inspection of the flag alone -- the model would learn from a number that
    carries no information. Requires genuine spread, not merely "not all equal".
    """
    rows = _rows(seeded_db, _live_contexts(seeded_db))
    scores = [r["network_health_score_rolling"] for r in rows
              if r["network_health_score_rolling"] is not None]
    assert len(scores) >= 10, f"only {len(scores)} scored contexts"

    distinct = len(set(scores))
    spread = max(scores) - min(scores)
    assert distinct > 1, (
        f"all {len(scores)} opportunities read an identical rolling health "
        f"score ({scores[0]}). This is the past-the-end clamp signature: a "
        "stale constant reported as known=1.0.")
    assert spread > 0.01, (
        f"rolling health spread is only {spread:.5f} across {len(scores)} "
        f"opportunities ({distinct} distinct). Near-constant health carries no "
        "signal even when the flag says known.")


@pytest.mark.gate("permanent.single_authority")
def test_the_frozen_exception_stayed_narrow():
    """
    optimize.py was unfrozen for three lines on the grounds that bank, psp and
    decision_time_hours are lookup keys rather than model features, so the
    change reaches the model only through the four network_health_* features.
    If any of them becomes a feature, that justification no longer holds and
    the exception must be re-examined rather than silently inherited.
    """
    for key in ("bank", "psp", "decision_time_hours"):
        assert key not in feats.ALL_FEATURES, (
            f"{key!r} is now a model feature; the Phase 5 frozen-list "
            "exception for optimize.py was justified on it NOT being one")

    network = [f for f in feats.ALL_FEATURES if f.startswith("network_health")]
    assert sorted(network) == [
        "network_health_known",
        "network_health_score_rolling",
        "network_health_success_rate_rolling",
        "network_health_timeout_rate_rolling",
    ], f"the network-health feature set changed: {sorted(network)}"


@pytest.mark.gate("phase5.network_health")
def test_do_nothing_still_has_exactly_zero_incremental_value(seeded_db):
    """
    Network health is now a live input to every score. do_nothing's EIV is a
    subtraction of a candidate's expected amount from its own baseline, so it
    must remain exactly 0.0 regardless of what the health features say.
    """
    oid = seeded_db.execute(
        "SELECT opportunity_id FROM opportunities WHERE status IN ('open','recovering')"
        " LIMIT 1").fetchone()[0]
    result = optimize.optimize_opportunity(seeded_db, oid, persist=False)
    if result["error"] is not None:
        pytest.skip(f"optimizer unavailable: {result['error']}")
    dn = [r for r in result["ranked"] if r["action_type"] == "do_nothing"]
    assert len(dn) == 1
    assert dn[0]["predicted_eiv"] == 0.0
