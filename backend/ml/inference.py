"""
Phase 3 -- the single model-loading and scoring module. Used identically
whether called from the offline evaluation harness (evaluate_outcome_model.py)
or, later, Phase 4's live optimizer -- neither path is allowed to compute a
feature or load a model any other way (see ml/outcome_features.py's module
docstring for the shared-feature-construction half of this guarantee).

Authority boundary (permanent, do not weaken):
  - This module PREDICTS. It has no DB write access, no import of
    execute_action / decide_action / mark_opportunity_recovered, and no code
    path that selects or triggers a recovery action. It answers "what outcome
    do we expect for this candidate" -- nothing more.
  - It does NOT touch backend/engine/decide_action.py's existing legacy
    `_load_ml_model()` advisory path (a different, single-candidate risk
    model) -- that stays exactly as shipped.

Failure behavior (Phase 3 acceptance gate, scope per Decision E): a malformed
or out-of-contract (context, candidate) pair returns a CLEARLY-FLAGGED null
result -- {"error": "...", "p_recovery": None, ...} -- never a silently
computed, plausible-looking score. This module only proves that contract;
proving a malformed case never reaches recovery_candidates is Phase 4's job
(recovery_candidates does not exist as a write target yet).
"""

from pathlib import Path

import joblib
import pandas as pd

from backend.db import db as db_module
from backend.ml import outcome_features as feats

_THIS_DIR = Path(__file__).resolve().parent
MODEL_PATH = _THIS_DIR / "models" / "outcome_model.joblib"

_MODEL = None
_MODEL_LOAD_ATTEMPTED = False
_HEALTH_LOOKUP = None


def _load_model():
    """Lazy-load once per process, cached. Returns None (never raises) if the
    artifact is missing or fails to load -- callers treat that as 'model
    unavailable', a flagged-null case, exactly like the legacy advisory
    loader's discipline in decide_action.py (that pattern is fine; only the
    duplication of feature/scoring logic it also implied is what Phase 3
    removes, by having exactly one such module for the new joint model)."""
    global _MODEL, _MODEL_LOAD_ATTEMPTED
    if _MODEL_LOAD_ATTEMPTED:
        return _MODEL
    _MODEL_LOAD_ATTEMPTED = True
    try:
        _MODEL = joblib.load(MODEL_PATH)
    except Exception:
        _MODEL = None
    return _MODEL


def _get_health_lookup(conn=None):
    """Builds the NetworkHealthLookup ONCE per process and caches it -- from
    the exact same table (via the exact same
    outcome_features.load_health_observations) the training script reads, and
    the exact same NetworkHealthLookup class the training script constructs.
    Accepts an optional open connection; opens and closes its own otherwise."""
    global _HEALTH_LOOKUP
    if _HEALTH_LOOKUP is not None:
        return _HEALTH_LOOKUP
    owns_conn = conn is None
    if owns_conn:
        conn = db_module.get_connection()
    try:
        health_df = feats.load_health_observations(conn)
    finally:
        if owns_conn:
            conn.close()
    _HEALTH_LOOKUP = feats.NetworkHealthLookup(health_df)
    return _HEALTH_LOOKUP


def reset_cache():
    """Test/reload hook -- forces the next call to re-load the model and
    rebuild the health lookup. Not used by any live scoring path."""
    global _MODEL, _MODEL_LOAD_ATTEMPTED, _HEALTH_LOOKUP
    _MODEL, _MODEL_LOAD_ATTEMPTED, _HEALTH_LOOKUP = None, False, None


def score_candidate(context: dict, candidate: dict, conn=None) -> dict:
    """
    The one scoring entry point. Returns, on success:
        {"error": None, "p_recovery": float in [0,1],
         "expected_amount_given_recovered": float >= 0,
         "expected_recovered_amount": float >= 0}
    expected_recovered_amount = p_recovery * expected_amount_given_recovered
    (Decision C) -- computed here, not inside either sklearn pipeline, so the
    derivation is visible and testable independent of the model artifacts.

    On any failure (malformed input, model unavailable, scoring exception):
        {"error": "<reason>", "p_recovery": None,
         "expected_amount_given_recovered": None, "expected_recovered_amount": None}
    Never partially-filled, never a plausible-looking number on failure.
    """
    model = _load_model()
    if model is None:
        return _null_result("model artifact unavailable")

    try:
        health_lookup = _get_health_lookup(conn)
        row = feats.build_feature_row(context, candidate, health_lookup)
    except feats.FeatureValidationError as e:
        return _null_result(f"feature validation failed: {e}")
    except Exception as e:  # fail closed on anything unexpected too
        return _null_result(f"unexpected feature-construction error: {e!r}")

    try:
        X = pd.DataFrame([row])[feats.ALL_FEATURES]
        p_recovery = float(model["p_pipeline"].predict_proba(X)[:, 1][0])
        expected_amount = float(model["amount_pipeline"].predict(X)[0])
        expected_amount = max(0.0, expected_amount)
        p_recovery = min(1.0, max(0.0, p_recovery))
    except Exception as e:
        return _null_result(f"unexpected scoring error: {e!r}")

    return {
        "error": None,
        "p_recovery": p_recovery,
        "expected_amount_given_recovered": expected_amount,
        "expected_recovered_amount": p_recovery * expected_amount,
    }


def score_do_nothing(context: dict, conn=None) -> dict:
    """Convenience wrapper -- do_nothing is always a scoreable candidate
    (Section 9 permanent invariant), never a special-cased zero."""
    return score_candidate(context, feats.do_nothing_candidate(), conn=conn)


def _null_result(reason: str) -> dict:
    return {"error": reason, "p_recovery": None,
            "expected_amount_given_recovered": None, "expected_recovered_amount": None}
