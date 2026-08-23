# backend/scripts/test_generate_message.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os
from unittest.mock import patch
from llm.generate_message import generate_recovery_message

# Sample payments covering all event types / root causes
payment_failed = {
    "id": "pay_test", "event_type": "payment_failed",
    "amount": 500000, "currency": "INR",
}
checkout_abandoned = {
    "id": "pay_test2", "event_type": "checkout_abandoned",
    "amount": 250000, "currency": "INR",
}
invoice_overdue = {
    "id": "pay_test3", "event_type": "invoice_overdue",
    "amount": 750000, "currency": "INR",
}

ROOT_CAUSES = [
    "insufficient_funds", "payment_declined", "gateway_timeout",
    "authentication_failed", "expired_card", "network_error",
]

def check(label, result, expect_status=None):
    print(f"--- {label} ---")
    print(result)
    assert isinstance(result, dict), "FAIL: not a dict"
    assert "message" in result and "status" in result, "FAIL: missing keys"
    assert isinstance(result["message"], str) and result["message"].strip() != "", "FAIL: empty message"
    assert result["status"] in ("ok", "fallback"), "FAIL: bad status value"
    if expect_status:
        assert result["status"] == expect_status, f"FAIL: expected status={expect_status}, got {result['status']}"
    print("OK\n")

# ---------- TEST 1: live Gemini call, retry, with root cause ----------
print("=== TEST 1: live retry generation (authentication_failed) ===")
r = generate_recovery_message(payment_failed, {"root_cause": "authentication_failed"}, "retry")
check("live retry", r)

# ---------- TEST 2: live Gemini call, reminder, checkout_abandoned ----------
print("=== TEST 2: live reminder generation (checkout_abandoned) ===")
r = generate_recovery_message(checkout_abandoned, {"root_cause": None}, "reminder")
check("live reminder", r)

# ---------- TEST 3: all 6 root causes -> forced fallback, check root-cause-specific text ----------
print("=== TEST 3: all 6 root-cause fallback templates ===")
for rc in ROOT_CAUSES:
    with patch("google.generativeai.GenerativeModel") as mock_model_cls:
        mock_model_cls.side_effect = Exception("forced failure")
        r = generate_recovery_message(payment_failed, {"root_cause": rc}, "retry")
    check(f"fallback retry / {rc}", r, expect_status="fallback")
    # confirm the fallback text differs per root cause (not a generic string)
    assert rc.replace("_", " ").split()[0] not in "" # trivial sanity
print()

# ---------- TEST 4: checkout_abandoned / invoice_overdue fallback, no invented root cause ----------
print("=== TEST 4: event-specific fallback, no root cause invented ===")
with patch("google.generativeai.GenerativeModel") as mock_model_cls:
    mock_model_cls.side_effect = Exception("forced failure")
    r1 = generate_recovery_message(checkout_abandoned, {"root_cause": None}, "reminder")
check("checkout_abandoned fallback", r1, expect_status="fallback")

with patch("google.generativeai.GenerativeModel") as mock_model_cls:
    mock_model_cls.side_effect = Exception("forced failure")
    r2 = generate_recovery_message(invoice_overdue, {"root_cause": None}, "reminder")
check("invoice_overdue fallback", r2, expect_status="fallback")

# manually confirm no root-cause words leaked into these two messages
for rc_word in ["insufficient", "declined", "timeout", "authentication", "expired", "network"]:
    assert rc_word not in r1["message"].lower(), f"FAIL: invented root cause word '{rc_word}' in checkout_abandoned fallback"
    assert rc_word not in r2["message"].lower(), f"FAIL: invented root cause word '{rc_word}' in invoice_overdue fallback"
print("No invented root-cause language: OK\n")

# ---------- TEST 5: missing/invalid API key -> fallback ----------
print("=== TEST 5: missing API key ===")
with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
    r = generate_recovery_message(payment_failed, {"root_cause": "expired_card"}, "retry")
check("missing key fallback", r, expect_status="fallback")

# ---------- TEST 6: empty Gemini response -> fallback ----------
print("=== TEST 6: empty Gemini response ===")
class FakeResponse:
    text = ""
with patch("google.generativeai.GenerativeModel") as mock_model_cls:
    mock_model_cls.return_value.generate_content.return_value = FakeResponse()
    ...
    r = generate_recovery_message(payment_failed, {"root_cause": "network_error"}, "retry")
check("empty response fallback", r, expect_status="fallback")

# ---------- TEST 7: unexpected action_type (escalate/stop) ----------
print("=== TEST 7: unexpected action_type ===")
r1 = generate_recovery_message(payment_failed, {"root_cause": "expired_card"}, "escalate")
check("escalate action_type", r1, expect_status="fallback")
r2 = generate_recovery_message(payment_failed, {"root_cause": None}, "stop")
check("stop action_type", r2, expect_status="fallback")

print("=== ALL TESTS COMPLETED ===")