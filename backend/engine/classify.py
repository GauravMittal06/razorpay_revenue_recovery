"""
classify(): rule-based root cause classification.
Single shared function for all three event types (SoT section 3).
Stage 1: rules only. ML-assisted layer added in stage 2 (ml/risk_model.py).
"""


def classify(payment: dict) -> dict:
    """
    Returns:
    {"root_cause": str|None, "confidence": float, "method": "rule"}

    root_cause is only meaningful for payment_failed (SoT section 3).
    For checkout_abandoned and invoice_overdue, root_cause stays None.
    """
    event_type = payment["event_type"]

    if event_type != "payment_failed":
        return {"root_cause": None, "confidence": 1.0, "method": "rule"}

    # error_reason already carries the locked root-cause mapping (section 6).
    # In production this would be derived from error_code/error_description;
    # here it's a direct rule mapping.
    root_cause = payment.get("error_reason")

    if root_cause is None:
        return {"root_cause": "unknown", "confidence": 0.0, "method": "rule"}

    return {"root_cause": root_cause, "confidence": 1.0, "method": "rule"}