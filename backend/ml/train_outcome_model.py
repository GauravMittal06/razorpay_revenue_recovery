"""
Phase 3 -- train the one joint outcome model (Execution Plan Section 7 /
Phase 3; Decisions C/I). Two XGBoost heads over one shared feature contract
(ml/outcome_features.py):

  p_pipeline     -- XGBClassifier, P(recovered=1 | features), trained on
                    every row of the training split (recovered is 0/1 for
                    every candidate, including do_nothing).
  amount_pipeline -- XGBRegressor, E[recovered_amount | recovered=1, features],
                    trained ONLY on rows where recovered=1.

expected_recovered_amount = p_recovery * E[amount | recovered] (Decision C) is
computed at SCORING time (ml/inference.py), never as a third trained target --
this keeps the derivation visible/testable independent of the model weights.

Data: ONLY backend/data_factory/phase3_eval/phase3_baseline_seed42_training_pool.csv
(hash-verified before use). The model-selection-val split below is carved
from THIS training pool -- it is still "seen" data (Decision A); it is never
the calibration_holdout or temporal_holdout, which stay untouched until
evaluate_outcome_model.py's gates run.

Run from the directory containing backend/:
    python -m backend.ml.train_outcome_model
"""

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from backend.data_factory import eval_set_lock as evl
from backend.db import db as db_module
from backend.ml import bank_health_setup
from backend.ml import outcome_features as feats

_THIS_DIR = Path(__file__).resolve().parent
MODEL_DIR = _THIS_DIR / "models"
MODEL_PATH = MODEL_DIR / "outcome_model.joblib"
MANIFEST_PATH = MODEL_DIR / "outcome_model_manifest.json"

RANDOM_STATE = 42
VAL_FRACTION = 0.15  # model-selection-val, carved from the training pool
GENERATOR_VERSION_EXPECTED = "data-factory-v1.0.0-phase2"


def _verify_training_pool_artifact():
    r = evl.verify_phase3_artifact("phase3_baseline_seed42_training_pool")
    if not r["passed"]:
        raise RuntimeError(f"training_pool hash verification FAILED -- refusing to train "
                            f"against a possibly-modified file: {r}")
    return r


def _ensure_bank_health_populated():
    conn = db_module.get_connection()
    try:
        n = conn.execute("SELECT COUNT(*) FROM bank_health_observations").fetchone()[0]
        if n == 0:
            print("bank_health_observations is empty -- populating (Decision B2)...")
            conn.close()
            bank_health_setup.main()
            conn = db_module.get_connection()
            n = conn.execute("SELECT COUNT(*) FROM bank_health_observations").fetchone()[0]
        return n
    finally:
        conn.close()


def deterministic_train_val_split(training_pool: pd.DataFrame):
    """Case-grouped, deterministic (fixed random_state), case-disjoint split
    of the SEEN training pool into train / model-selection-val. Mirrors the
    exact GroupShuffleSplit + zero-overlap-assertion pattern already proven
    in ml/train_risk_model.py:grouped_split() and
    data_factory/validators.py:leakage_case_level. Not hash-locked (Decision
    A: only calibration_holdout and temporal_holdout are frozen/unseen)."""
    splitter = GroupShuffleSplit(n_splits=1, test_size=VAL_FRACTION, random_state=RANDOM_STATE)
    train_idx, val_idx = next(splitter.split(training_pool, groups=training_pool["case_id"]))
    train_df = training_pool.iloc[train_idx].reset_index(drop=True)
    val_df = training_pool.iloc[val_idx].reset_index(drop=True)

    overlap = set(train_df["case_id"]) & set(val_df["case_id"])
    assert len(overlap) == 0, f"train/model-selection-val case_id leakage: {len(overlap)} cases"
    cust_overlap = set(train_df["customer_id"]) & set(val_df["customer_id"])
    # Reported, not asserted: within the SEEN pool this is not required to be
    # zero by Decision A (only training_pool<->calibration_holdout must be
    # customer-disjoint); a customer can legitimately have some cases in
    # train and others in model-selection-val.
    return train_df, val_df, {"train_cases": len(set(train_df["case_id"])),
                               "val_cases": len(set(val_df["case_id"])),
                               "case_overlap": len(overlap),
                               "customer_overlap_within_seen_pool": len(cust_overlap)}


def build_preprocessor():
    return ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), feats.CATEGORICAL_FEATURES),
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                           ("scale", StandardScaler())]), feats.NUMERIC_FEATURES),
    ])


def fit_outcome_model(train_df: pd.DataFrame, health_lookup, verbose=True) -> dict:
    """Fits both heads on `train_df` (any joint-dataset-shaped frame -- the
    seed-42 primary `train` split, or a full per-seed training_pool for the
    multi-seed robustness gate) against the shared feature contract. Returns
    the same artifact shape train_outcome_model.main() persists. Factored out
    so evaluate_outcome_model.py's multi-seed gate reuses this exact fitting
    code instead of re-implementing it. `health_lookup` is a
    feats.NetworkHealthLookup."""
    X_train = feats.build_feature_frame_from_joint_df(train_df, health_lookup)
    y_p_train = train_df["recovered"].astype(int)

    if verbose:
        print(f"  Fitting p_recovery head on {len(X_train)} rows "
              f"(positive rate={y_p_train.mean():.4f})...")
    # Conservative capacity, matching backend/ml/train_risk_model.py's shape.
    # A capacity bump (600 trees / depth 5 / lr 0.03) was tried and REVERTED:
    # it overfit the ~2100-case training pool -> WORSE held-out calibration
    # (in-profile ECE 0.019 -> 0.021, bin-gap 0.036 -> 0.058, failing
    # phase3_calibration) and did not improve cross-profile or temporal
    # ranking. AUC ~0.63 is near this synthetic world's observable-feature
    # ceiling (the outcome logit is dominated by hidden state the model
    # cannot see -- see backend/data_factory/outcome_model.py) and more
    # capacity just fits noise.
    p_pipeline = Pipeline([
        ("prep", build_preprocessor()),
        ("clf", XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05,
                               subsample=0.8, colsample_bytree=0.8,
                               eval_metric="logloss", random_state=RANDOM_STATE)),
    ])
    p_pipeline.fit(X_train, y_p_train)

    recovered_mask = train_df["recovered"] == 1
    X_train_amount = X_train[recovered_mask.values]
    y_amount_train = train_df.loc[recovered_mask, "recovered_amount"].astype(float)
    if verbose:
        print(f"  Fitting E[amount|recovered] head on {len(X_train_amount)} recovered rows...")
    amount_pipeline = Pipeline([
        ("prep", build_preprocessor()),
        ("reg", XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8,
                              random_state=RANDOM_STATE)),
    ])
    amount_pipeline.fit(X_train_amount, y_amount_train)

    return {
        "p_pipeline": p_pipeline,
        "amount_pipeline": amount_pipeline,
        "feature_columns": feats.ALL_FEATURES,
        "categorical_features": feats.CATEGORICAL_FEATURES,
        "numeric_features": feats.NUMERIC_FEATURES,
    }


def main():
    t0 = time.time()
    print("Verifying training_pool artifact hash before training...")
    v = _verify_training_pool_artifact()
    print(f"  OK -- {v['row_count']} rows / {v['case_count']} cases, "
          f"locked_at={v['locked_at']}, sha256={v['locked_hash'][:16]}...")

    n_health = _ensure_bank_health_populated()
    print(f"bank_health_observations: {n_health} rows")

    training_pool = feats.read_joint_csv(_THIS_DIR.parent / "data_factory" / "phase3_eval"
                                          / "phase3_baseline_seed42_training_pool.csv")
    gv = training_pool["generator_version"].unique().tolist()
    assert gv == [GENERATOR_VERSION_EXPECTED], f"unexpected generator_version(s): {gv}"

    train_df, val_df, split_meta = deterministic_train_val_split(training_pool)
    print(f"Split: {split_meta}")

    conn = db_module.get_connection()
    health_lookup = feats.NetworkHealthLookup(feats.load_health_observations(conn))
    conn.close()

    print("Fitting (train split)...")
    artifact = fit_outcome_model(train_df, health_lookup)

    # Quick sanity metric on model-selection-val -- NOT a Phase 3 gate (those
    # run on calibration_holdout / temporal_holdout / stress in
    # evaluate_outcome_model.py). This only confirms fitting produced a
    # non-degenerate model before spending time on the full gate suite.
    from sklearn.metrics import roc_auc_score
    X_val = feats.build_feature_frame_from_joint_df(val_df, health_lookup)
    y_p_val = val_df["recovered"].astype(int)
    p_val_pred = artifact["p_pipeline"].predict_proba(X_val)[:, 1]
    val_auc = roc_auc_score(y_p_val, p_val_pred)
    print(f"model-selection-val ROC-AUC (sanity only, not a gate): {val_auc:.4f}")
    assert 0.5 < val_auc <= 1.0, f"p_recovery head is not better than chance on val (AUC={val_auc:.4f})"

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_PATH)

    manifest = {
        "trained_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "random_state": RANDOM_STATE,
        "val_fraction": VAL_FRACTION,
        "training_artifact": "phase3_baseline_seed42_training_pool",
        "training_artifact_sha256": v["locked_hash"],
        "generator_version": GENERATOR_VERSION_EXPECTED,
        "split_meta": split_meta,
        "n_train_rows": int(len(train_df)),
        "n_train_recovered_rows": int((train_df["recovered"] == 1).sum()),
        "n_val_rows": int(len(X_val)),
        "val_roc_auc_sanity_only": float(val_auc),
        "model_type": "two XGBoost heads (classifier + regressor) over one shared "
                       "ColumnTransformer feature contract (Decision I)",
        "wall_clock_seconds": round(time.time() - t0, 1),
    }
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f"\nSaved: {MODEL_PATH}")
    print(f"Saved: {MANIFEST_PATH}")
    print(f"Done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
