"""
parse_intent.py — Gemini-based customer reply/intent parsing (Stage 3, Micro-step 1).
LLM is language/intent-extraction only (SoT section 5). Never selects, triggers,
or overrides a recovery action. root_cause is deliberately NOT sent to Gemini --
mentioned_reason must reflect only what the customer stated, to avoid anchoring
bias (approved contract, Stage 3 Micro-step 1).

Output schema (strict JSON only):
{
  "intent": "promise_to_pay"|"dispute"|"payment_method_updated"|"general_query"|"unclear",
  "confidence": float 0.0-1.0,
  "mentioned_reason": one of the 6 locked error_reason values (SoT section 6) | null,
  "extracted_detail": str | null
}

On any failure/timeout/malformed JSON, returns the fixed fail-safe fallback
(confidence=0.0, intent="unclear") -- never raises, never fabricates intent.
"""

import json
import os
import time

# Locked error_reason values (SoT section 6). Must match classify.py's
# expected values exactly.
LOCKED_ROOT_CAUSES = {
    "insufficient_funds",
    "payment_declined",
    "gateway_timeout",
    "authentication_failed",
    "expired_card",
    "network_error",
}

ALLOWED_INTENTS = {
    "promise_to_pay",
    "dispute",
    "payment_method_updated",
    "general_query",
    "unclear",
}

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TIMEOUT_SECONDS = 30
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_BACKOFF_SECONDS = 2  # doubles each retry: 2s, 4s, 8s

_FALLBACK = {
    "intent": "unclear",
    "confidence": 0.0,
    "mentioned_reason": None,
    "extracted_detail": None,
}

_SYSTEM_PROMPT = """You are a structured intent extractor for a payment recovery system.
You do NOT decide any action. You only read a customer's reply and extract structured
JSON. Never include any text outside the JSON object.

Given the customer's message and recent conversation history, return ONLY this JSON object:
{
  "intent": one of ["promise_to_pay", "dispute", "payment_method_updated", "general_query", "unclear"],
  "confidence": a float between 0.0 and 1.0 reflecting how confident you are in the intent classification,
  "mentioned_reason": one of ["insufficient_funds", "payment_declined", "gateway_timeout",
                               "authentication_failed", "expired_card", "network_error"] if and only if
                       the customer explicitly states or clearly implies a specific payment failure
                       reason themselves, otherwise null,
  "extracted_detail": a short free-text string capturing any relevant detail (e.g. a promised date),
                       or null if nothing relevant was stated
}

Rules:
- Base mentioned_reason strictly on what the customer said. Do not guess or infer beyond their words.
- The message may be in English, Hinglish, or a mix of both.
- Output raw JSON only. No markdown, no commentary, no code fences.
"""


def _build_user_prompt(customer_message, conversation_history, event_type):
    history_lines = []
    for m in (conversation_history or [])[-10:]:
        sender = m.get("sender", "unknown")
        content = m.get("content", "")
        history_lines.append(f"{sender}: {content}")
    history_text = "\n".join(history_lines) if history_lines else "(no prior messages)"

    return (
        f"Event type: {event_type}\n\n"
        f"Recent conversation history:\n{history_text}\n\n"
        f"Latest customer message:\n{customer_message}"
    )


def _validate_and_normalize(parsed: dict) -> dict:
    """Strict validation. Any deviation from the expected schema -> fallback."""
    if not isinstance(parsed, dict):
        print(f"[parse_intent] validation failed: not a dict: {parsed!r}")
        return dict(_FALLBACK)

    intent = parsed.get("intent")
    if intent not in ALLOWED_INTENTS:
        print(f"[parse_intent] validation failed: bad intent: {intent!r}")
        return dict(_FALLBACK)

    confidence = parsed.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        print(f"[parse_intent] validation failed: bad confidence type: {confidence!r}")
        return dict(_FALLBACK)
    if not (0.0 <= confidence <= 1.0):
        print(f"[parse_intent] validation failed: confidence out of range: {confidence!r}")
        return dict(_FALLBACK)

    mentioned_reason = parsed.get("mentioned_reason")
    if mentioned_reason is not None and mentioned_reason not in LOCKED_ROOT_CAUSES:
        # model returned a value outside the locked list -- do not trust it
        mentioned_reason = None

    extracted_detail = parsed.get("extracted_detail")
    if extracted_detail is not None and not isinstance(extracted_detail, str):
        extracted_detail = None

    return {
        "intent": intent,
        "confidence": round(confidence, 4),
        "mentioned_reason": mentioned_reason,
        "extracted_detail": extracted_detail,
    }


def parse_reply_intent(customer_message: str, conversation_history: list, event_type: str) -> dict:
    """
    Calls Gemini to extract structured intent from a customer reply.
    root_cause is intentionally NOT accepted as a parameter here and must
    never be passed into the prompt (bias-prevention, approved contract).

    Never raises. Returns the fixed fallback dict on any failure, timeout,
    or malformed/invalid model output.
    """
    try:
        import google.generativeai as genai
    except ImportError:
        return dict(_FALLBACK)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return dict(_FALLBACK)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
    )
    user_prompt = _build_user_prompt(customer_message, conversation_history, event_type)

    last_error = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = model.generate_content(
                user_prompt,
                generation_config={"response_mime_type": "application/json"},
                request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
            )
            raw_text = response.text
            parsed = json.loads(raw_text)
            return _validate_and_normalize(parsed)

        except Exception as e:
            last_error = e
            is_rate_limit = "429" in str(e) or "TooManyRequests" in str(e) or "quota" in str(e).lower()
            is_last_attempt = attempt == GEMINI_MAX_RETRIES - 1
            if is_rate_limit and not is_last_attempt:
                wait = GEMINI_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                print(f"[parse_intent] rate limited (attempt {attempt+1}/{GEMINI_MAX_RETRIES}), retrying in {wait}s")
                time.sleep(wait)
                continue
            break

    print(f"[parse_intent] Gemini call failed after retries: {type(last_error).__name__}: {last_error}")
    return dict(_FALLBACK)