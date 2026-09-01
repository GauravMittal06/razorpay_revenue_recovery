"""
Layer 3: train + evaluate recovery-probability models.
Trains on the simulator corpus only (backend/ml/data/training_corpus.csv).
Never touches the 150-record demo/evaluation dataset.
Rule engine remains final compliance authority -- this script only
produces a scoring model, no control logic here.
"""

import os
from pathlib import Path
import numpy as np
import pandas as pd
import joblib

from sklearn.model_selection import GroupShuffleSplit
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    classification_report, confusion_matrix,
    roc_auc_score, average_precision_score
)
from sklearn.calibration import calibration_curve

from xgboost import XGBClassifier

# Anchored on this file's location, not on the caller's cwd (see
# simulate_training_data.py for the same fix and rationale).
_ML_DIR = Path(__file__).resolve().parent
DATA_PATH = _ML_DIR / "data" / "training_corpus.csv"
MODEL_DIR = _ML_DIR / "models"
RANDOM_STATE = 42

CATEGORICAL_FEATURES = [
    "event_type", "root_cause", "method",
    "last_action_type", "candidate_action", "preferred_channel"
]
NUMERIC_FEATURES = [
    "amount", "retry_count", "days_since_event", "days_overdue",
    "hours_since_last_action", "payment_history_score", "past_recovery_rate"
]


def load_data():
    df = pd.read_csv(DATA_PATH)
    df["root_cause"] = df["root_cause"].fillna("none")
    df["days_overdue"] = df["days_overdue"].fillna(0)
    df["hours_since_last_action"] = df["hours_since_last_action"].fillna(0)
    return df


def grouped_split(df):
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
    train_idx, test_idx = next(splitter.split(df, groups=df["case_id"]))
    train_df = df.iloc[train_idx].reset_index(drop=True)
    test_df = df.iloc[test_idx].reset_index(drop=True)

    # leakage check -- must be zero overlap
    overlap = set(train_df["case_id"]) & set(test_df["case_id"])
    assert len(overlap) == 0, f"case_id leakage detected: {len(overlap)} cases"

    return train_df, test_df


def build_preprocessor():
    return ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ("num", StandardScaler(), NUMERIC_FEATURES),
    ])


def evaluate(name, y_true, y_pred, y_proba):
    print(f"\n=== {name} ===")
    print(classification_report(y_true, y_pred, digits=3))

    cm = confusion_matrix(y_true, y_pred)
    print("Confusion matrix [[TN FP] [FN TP]]:")
    print(cm)

    roc_auc = roc_auc_score(y_true, y_proba)
    pr_auc = average_precision_score(y_true, y_proba)
    print(f"ROC-AUC: {roc_auc:.4f}")
    print(f"PR-AUC:  {pr_auc:.4f}")

    frac_pos, mean_pred = calibration_curve(y_true, y_proba, n_bins=10, strategy="quantile")
    print("Calibration (predicted vs actual, 10 quantile bins):")
    for p, a in zip(mean_pred, frac_pos):
        print(f"  predicted={p:.3f}  actual={a:.3f}")

    return {"roc_auc": roc_auc, "pr_auc": pr_auc}


def main():
    df = load_data()
    train_df, test_df = grouped_split(df)
    print(f"Train rows: {len(train_df)} ({train_df['case_id'].nunique()} cases)")
    print(f"Test rows:  {len(test_df)} ({test_df['case_id'].nunique()} cases)")

    X_train = train_df[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    y_train = train_df["y"]
    X_test = test_df[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    y_test = test_df["y"]

    preprocessor = build_preprocessor()

    # --- Baseline: Logistic Regression ---
    lr_pipeline = Pipeline([
        ("prep", preprocessor),
        ("clf", LogisticRegression(max_iter=1000, random_state=RANDOM_STATE))
    ])
    lr_pipeline.fit(X_train, y_train)
    lr_proba = lr_pipeline.predict_proba(X_test)[:, 1]
    lr_pred = (lr_proba >= 0.5).astype(int)
    lr_metrics = evaluate("Logistic Regression (baseline)", y_test, lr_pred, lr_proba)

    # --- Stronger: XGBoost ---
    xgb_pipeline = Pipeline([
        ("prep", preprocessor),
        ("clf", XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=RANDOM_STATE
        ))
    ])
    xgb_pipeline.fit(X_train, y_train)
    xgb_proba = xgb_pipeline.predict_proba(X_test)[:, 1]
    xgb_pred = (xgb_proba >= 0.5).astype(int)
    xgb_metrics = evaluate("XGBoost", y_test, xgb_pred, xgb_proba)

    print("\n=== Comparison ===")
    print(f"LR   ROC-AUC={lr_metrics['roc_auc']:.4f}  PR-AUC={lr_metrics['pr_auc']:.4f}")
    print(f"XGB  ROC-AUC={xgb_metrics['roc_auc']:.4f}  PR-AUC={xgb_metrics['pr_auc']:.4f}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(lr_pipeline, MODEL_DIR / "lr_model.joblib")
    joblib.dump(xgb_pipeline, MODEL_DIR / "xgb_model.joblib")
    print(f"\nModels saved to {MODEL_DIR}/")


if __name__ == "__main__":
    main()