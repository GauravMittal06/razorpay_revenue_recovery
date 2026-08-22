"""
Layer 2: stochastic recovery-outcome simulator.
Generates a training-only corpus (X + y). Distinct from the locked
150-record demo/evaluation dataset. Hidden variables are never written
to output.
"""

import numpy as np
import pandas as pd
import os

RNG_SEED = 42
N_CASES = 8000
RECOVERY_HORIZON_HOURS = 72

EVENT_TYPES = ["checkout_abandoned", "payment_failed", "invoice_overdue"]
ROOT_CAUSES = [
    "insufficient_funds", "payment_declined", "gateway_timeout",
    "authentication_failed", "expired_card", "network_error"
]
METHODS = ["card", "netbanking", "upi", "wallet"]
CHANNELS = ["email", "sms", "whatsapp"]


def clip01(x):
    return np.clip(x, 0.0, 1.0)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def sample_hidden_variables(payment_history_score, past_recovery_rate, method, rng):
    liquidity_state = clip01(rng.normal(0.3 + 0.5 * payment_history_score, 0.22))
    issuer_availability = clip01(rng.beta(8, 2))

    method_base = {"card": 0.72, "netbanking": 0.68, "upi": 0.80, "wallet": 0.75}
    payment_method_health = clip01(rng.normal(method_base[method], 0.15))

    # Evidence-inspired design choice, NOT a dataset-fitted coefficient.
    # UCI data showed tree-based (non-linear) models dramatically
    # outperform linear models for repayment-risk prediction, implying
    # real threshold/interaction structure rather than pure linearity.
    # We introduce a mild sigmoid-shaped relationship (soft tipping
    # point around mid-range past_recovery_rate) instead of a straight
    # line, so responsiveness diverges non-proportionally near that
    # midpoint. Steepness/midpoint/range chosen for a bounded, mild
    # effect and validated via sensitivity check -- not fitted to any
    # dataset, and not tuned to favor XGBoost over Logistic Regression.
    responsiveness_base = 0.15 + 0.7 * sigmoid(6 * (past_recovery_rate - 0.5))
    customer_responsiveness = clip01(rng.normal(responsiveness_base, 0.20))
    bank_condition_temp = clip01(rng.beta(9, 1))

    recovery_willingness = clip01(
        0.5 * liquidity_state + 0.4 * customer_responsiveness
        + rng.normal(0.0, 0.18)
    )

    return {
        "liquidity_state": liquidity_state,
        "issuer_availability": issuer_availability,
        "payment_method_health": payment_method_health,
        "customer_responsiveness": customer_responsiveness,
        "bank_condition_temp": bank_condition_temp,
        "recovery_willingness": recovery_willingness,
    }


def action_effectiveness(candidate_action, root_cause, event_type):
    if event_type == "payment_failed":
        transient = {"gateway_timeout", "network_error"}
        needs_action = {"authentication_failed", "expired_card"}
        if candidate_action == "retry":
            if root_cause in transient:
                return 1.1
            if root_cause == "insufficient_funds":
                return -0.6
            if root_cause in needs_action:
                return -0.9
            return 0.0
        if candidate_action == "reminder":
            if root_cause in needs_action:
                return 0.7
            if root_cause == "insufficient_funds":
                return 0.5
            return 0.2
        if candidate_action == "escalate":
            return 0.35

    if event_type == "checkout_abandoned":
        return {"reminder": 0.8, "retry": -0.3, "escalate": 0.25}[candidate_action]

    if event_type == "invoice_overdue":
        return {"reminder": 0.6, "retry": -0.4, "escalate": 0.4}[candidate_action]

    return 0.0


def retry_count_penalty(retry_count):
    # Evidence-inspired design choice, NOT a dataset-fitted coefficient.
    # UCI Credit Card Default data shows default rate rises sharply once
    # a client is even slightly delinquent -- recent status matters more
    # than a flat averaged history. We mirror that qualitative shape by
    # adding an extra penalty specifically at the max-attempts boundary
    # (retry_count==3), rather than a purely flat linear penalty.
    # Magnitude chosen for a mild, bounded effect; validated via
    # sensitivity check (monotonicity, probability range), not fitted
    # to any external dataset.
    base_penalty = -0.10 * max(0, retry_count - 1)
    boundary_penalty = -0.15 if retry_count >= 3 else 0.0
    return base_penalty + boundary_penalty


def decay_term(event_type, days_since_event, days_overdue):
    if event_type == "invoice_overdue":
        return -0.09 * (days_overdue or 0)
    return -0.05 * (days_since_event or 0)


def amount_friction(event_type, amount):
    return -0.000004 * amount if event_type == "checkout_abandoned" else 0.0


def eligible_candidate_actions(retry_count):
    # stop excluded by design decision -- not a recovery candidate
    actions = ["reminder", "escalate"]
    if retry_count < 3:
        actions.insert(0, "retry")
    return actions


def generate_case(case_id, rng):
    event_type = rng.choice(EVENT_TYPES)
    root_cause = rng.choice(ROOT_CAUSES) if event_type == "payment_failed" else None
    method = rng.choice(METHODS)
    channel = rng.choice(CHANNELS)

    payment_history_score = clip01(rng.beta(5, 3))
    past_recovery_rate = clip01(rng.beta(4, 3))
    amount = int(rng.integers(50000, 5000000))
    retry_count = int(rng.integers(0, 4))
    days_since_event = float(rng.uniform(0, 10))
    days_overdue = float(rng.uniform(0, 20)) if event_type == "invoice_overdue" else None
    last_action_type = rng.choice(["none", "retry", "reminder", "escalate"])
    hours_since_last_action = float(rng.uniform(0, 200)) if last_action_type != "none" else None

    hidden = sample_hidden_variables(payment_history_score, past_recovery_rate, method, rng)

    rows = []
    for candidate_action in eligible_candidate_actions(retry_count):
        hidden_term = (
            0.9 * hidden["liquidity_state"]
            + 0.6 * hidden["issuer_availability"]
            + 0.5 * hidden["payment_method_health"]
            + 0.9 * hidden["customer_responsiveness"]
            + 0.4 * hidden["bank_condition_temp"]
            + 1.0 * hidden["recovery_willingness"]
        )
        z = (
            -0.4
            + hidden_term
            + action_effectiveness(candidate_action, root_cause, event_type)
            + retry_count_penalty(retry_count)
            + decay_term(event_type, days_since_event, days_overdue)
            + amount_friction(event_type, amount)
            + rng.normal(0, 0.15)
        )
        p = sigmoid(z)
        y = int(rng.random() < p)

        rows.append({
            "case_id": case_id,  # for grouped train/test split -- drop before training
            "event_type": event_type,
            "root_cause": root_cause,
            "amount": amount,
            "method": method,
            "retry_count": retry_count,
            "days_since_event": days_since_event,
            "days_overdue": days_overdue,
            "last_action_type": last_action_type,
            "hours_since_last_action": hours_since_last_action,
            "candidate_action": candidate_action,
            "payment_history_score": payment_history_score,
            "past_recovery_rate": past_recovery_rate,
            "preferred_channel": channel,
            "recovery_horizon_hours": RECOVERY_HORIZON_HOURS,
            "y": y,
        })
    return rows


def generate_corpus(n_cases=N_CASES, seed=RNG_SEED):
    rng = np.random.default_rng(seed)
    all_rows = []
    for i in range(n_cases):
        all_rows.extend(generate_case(f"sim_{i:06d}", rng))
    return pd.DataFrame(all_rows)


if __name__ == "__main__":
    df = generate_corpus()
    out_path = "backend/ml/data/training_corpus.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"{len(df)} rows, {df['case_id'].nunique()} cases -> {out_path}")
    print("overall positive rate:", df["y"].mean())
    print(df.groupby("candidate_action")["y"].mean())