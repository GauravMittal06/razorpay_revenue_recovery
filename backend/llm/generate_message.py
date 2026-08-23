"""
generate_message.py — Gemini-based customer-facing recovery message generation
(Stage 3, Micro-step 3). LLM generates language only (SoT section 5) --
never selects, triggers, or overrides a recovery action. Only ever called
for an action_type already selected by decide_action() (retry/reminder --
the only two customer-contact actions).

Pure function: no DB reads/writes. Persistence and pipeline wiring are
deferred to a future micro-step.

On any failure/timeout, returns a deterministic, non-LLM fallback template
-- root-cause-specific and action-oriented wherever root_cause exists;
event-type-appropriate (without inventing a root cause) for
checkout_abandoned / invoice_overdue, where root_cause is always null.
"""

import os

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TIMEOUT_SECONDS = 30

CUSTOMER_CONTACT_ACTIONS = {"retry", "reminder"}

# Human-readable root-cause phrasing for fallback templates and the LLM prompt.
ROOT_CAUSE_PHRASING = {
    "insufficient_funds": "there weren't sufficient funds available",
    "payment_declined": "your payment was declined by your bank",
    "gateway_timeout": "there was a temporary gateway/bank timeout",
    "authentication_failed": "the payment authentication step failed",
    "expired_card": "your card appears to have expired",
    "network_error": "a network issue interrupted the payment",
}

# Deterministic fallback templates -- retry, keyed by root_cause.
_RETRY_FALLBACKS = {
    "insufficient_funds": "We noticed your recent payment didn't go through because {reason}. Please ensure sufficient balance and we'll retry the payment shortly.",
    "payment_declined": "Your recent payment didn't go through because {reason}. Please check with your bank or try an alternate payment method -- we'll retry shortly.",
    "gateway_timeout": "Your recent payment didn't go through because {reason}. This is usually temporary -- we'll retry the payment shortly.",
    "authentication_failed": "Your recent payment didn't go through because {reason}. Please ensure your card/OTP details are up to date -- we'll retry shortly.",
    "expired_card": "Your recent payment didn't go through because {reason}. Please update your card details so we can retry the payment.",
    "network_error": "Your recent payment didn't go through because {reason}. We'll automatically retry shortly.",
}
_RETRY_FALLBACK_GENERIC = "We weren't able to process your recent payment. We'll retry it shortly -- no action needed unless you'd like to update your payment method."

# Deterministic fallback templates -- reminder, keyed by event_type
# (root_cause is always null for these two event types; never invented).
_REMINDER_FALLBACKS = {
    "checkout_abandoned": "You left an item in checkout without completing payment. Your order is still reserved -- complete your payment whenever you're ready.",
    "invoice_overdue": "This is a reminder that your invoice is now overdue. Please arrange payment at your earliest convenience to avoid further action.",
}
_REMINDER_FALLBACK_GENERIC = "This is a reminder regarding your pending payment. Please complete it at your earliest convenience."

_GENERIC_SAFE_FALLBACK = "We're following up regarding your recent payment. Please reach out if you have any questions."


def _build_fallback(payment: dict, classification: dict, action_type: str) -> str:
    root_cause = classification.get("root_cause") if classification else None
    event_type = payment.get("event_type")

    if action_type == "retry":
        if root_cause and root_cause in ROOT_CAUSE_PHRASING:
            return _RETRY_FALLBACKS[root_cause].format(reason=ROOT_CAUSE_PHRASING[root_cause])
        return _RETRY_FALLBACK_GENERIC

    if action_type == "reminder":
        if event_type in _REMINDER_FALLBACKS:
            return _REMINDER_FALLBACKS[event_type]
        return _REMINDER_FALLBACK_GENERIC

    # Defensive only -- this function is not intended to be called with
    # any action_type outside retry/reminder.
    return _GENERIC_SAFE_FALLBACK


def _build_prompt(payment: dict, classification: dict, action_type: str) -> str:
    root_cause = classification.get("root_cause") if classification else None
    event_type = payment.get("event_type")
    amount = payment.get("amount")
    currency = payment.get("currency", "INR")

    root_cause_line = (
        f"Root cause: {ROOT_CAUSE_PHRASING.get(root_cause, root_cause)}"
        if root_cause else "Root cause: not applicable for this event type."
    )

    return (
        "Write a short, polite, professional customer-facing message for a payment "
        "recovery system. Do not fabricate promises, discounts, deadlines, or legal "
        "commitments. Keep it to 2-3 sentences.\n\n"
        f"Event type: {event_type}\n"
        f"Action being taken: {action_type}\n"
        f"{root_cause_line}\n"
        f"Amount: {amount} {currency}\n"
    )


def generate_recovery_message(payment: dict, classification: dict, action_type: str) -> dict:
    """
    Returns:
    {
      "message": str,
      "status": "ok" | "fallback"
    }

    Only ever called for action_type in {"retry", "reminder"} -- the two
    customer-contact actions decide_action() may select. Never influences
    or re-evaluates the decision already made -- phrasing only.
    """
    fallback_text = _build_fallback(payment, classification, action_type)

    if action_type not in CUSTOMER_CONTACT_ACTIONS:
        return {"message": fallback_text, "status": "fallback"}

    try:
        import google.generativeai as genai
    except ImportError:
        return {"message": fallback_text, "status": "fallback"}

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"message": fallback_text, "status": "fallback"}

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name=GEMINI_MODEL)
        prompt = _build_prompt(payment, classification, action_type)

        response = model.generate_content(
            prompt,
            request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
        )

        text = response.text.strip() if response.text else ""
        if not text:
            return {"message": fallback_text, "status": "fallback"}

        return {"message": text, "status": "ok"}

    except Exception:
        return {"message": fallback_text, "status": "fallback"}