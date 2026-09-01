"""
classify(): rule-based root cause classification.
Single shared function for all three event types (SoT section 3).
Stage 1: rules only. ML-assisted layer added in stage 2 (ml/risk_model.py).

Phase 1 (Schema Foundation): signature takes event_type/error_reason
explicitly rather than a payment dict, since root_cause is now an
opportunity-level concept (diagnosed once per revenue-at-risk situation)
while error_reason is a per-payment-attempt field on the transactional log
that feeds it -- collapsing them back into one dict would blur exactly the
distinction Phase 1 exists to enforce. Logic is unchanged.
"""


def classify(event_type: str, error_reason: str = None) -> dict:
    """
    Returns:
    {"root_cause": str|None, "confidence": float, "method": "rule"}

    root_cause is only meaningful for payment_failed (SoT section 3).
    For checkout_abandoned and invoice_overdue, root_cause stays None.
    """
    if event_type != "payment_failed":
        return {"root_cause": None, "confidence": 1.0, "method": "rule"}

    # error_reason already carries the locked root-cause mapping (section 6).
    # In production this would be derived from error_code/error_description;
    # here it's a direct rule mapping.
    if error_reason is None:
        return {"root_cause": "unknown", "confidence": 0.0, "method": "rule"}

    return {"root_cause": error_reason, "confidence": 1.0, "method": "rule"}
