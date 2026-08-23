# backend/scripts/test_stage3_e2e.py
import sys
import time
import datetime as dt
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from unittest.mock import patch
from db.db import get_connection
from engine.classify import classify
from engine.handle_customer_reply import handle_customer_reply

conn = get_connection()

results = []

def record(name, condition, expected, actual):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    print(f"   expected: {expected}")
    print(f"   actual:   {actual}\n")
    results.append((name, status))

def created_at_with_hour(hour):
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return int(target.timestamp())

def get_customer_id():
    row = conn.execute("SELECT customer_id FROM customers LIMIT 1").fetchone()
    return row["customer_id"]

CUSTOMER_ID = get_customer_id()

def make_payment(suffix, hour, error_reason="authentication_failed"):
    pid = f"pay_e2e_{suffix}_{int(time.time())}"
    conn.execute(
        """
        INSERT INTO payments
        (id, entity, amount, currency, status, order_id, invoice_id, method, email, contact,
         error_code, error_description, error_source, error_step, error_reason,
         created_at, event_type, recovery_status, customer_id, days_overdue)
        VALUES
        (?, 'payment', 500000, 'INR', 'failed', NULL, NULL, 'card', 'test@example.com', '0000000000',
         'BAD_REQUEST', 'test error', 'business', 'payment_capture', ?,
         ?, 'payment_failed', 'open', ?, NULL)
        """,
        (pid, error_reason, created_at_with_hour(hour), CUSTOMER_ID),
    )
    conn.commit()
    return pid

def agent_msg_count(pid):
    return conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE payment_id=? AND sender='agent'", (pid,)
    ).fetchone()["c"]

def get_status(pid):
    return conn.execute(
        "SELECT recovery_status FROM payments WHERE id=?", (pid,)
    ).fetchone()["recovery_status"]

def action_count(pid):
    return conn.execute(
        "SELECT COUNT(*) c FROM recovery_actions WHERE payment_id=?", (pid,)
    ).fetchone()["c"]


# ===================================================================
# TEST A: Full real end-to-end happy path (real Gemini both directions)
# ===================================================================
print("=== TEST A: full real E2E (retry, in-window, real Gemini) ===")
try:
    pid_a = make_payment("happy", hour=12, error_reason="authentication_failed")
    result = handle_customer_reply(
        pid_a, "Sorry for the delay, I will pay by Friday", conn
    )
    print(result)

    record(
        "A1: overall status ok",
        result.get("status") == "ok",
        "ok", result.get("status")
    )
    record(
        "A2: decision outcome executed",
        result.get("decision", {}).get("outcome") == "executed",
        "executed", result.get("decision", {}).get("outcome")
    )
    record(
        "A3: action_type is retry",
        result.get("decision", {}).get("action_type") == "retry",
        "retry", result.get("decision", {}).get("action_type")
    )
    record(
        "A4: agent message persisted",
        agent_msg_count(pid_a) == 1,
        1, agent_msg_count(pid_a)
    )
    cust_row = conn.execute(
        "SELECT intent_extracted, intent_confidence FROM messages WHERE payment_id=? AND sender='customer'",
        (pid_a,)
    ).fetchone()
    record(
        "A5: customer message has parsed intent persisted",
        cust_row is not None and cust_row["intent_extracted"] is not None,
        "non-null intent_extracted", dict(cust_row) if cust_row else None
    )
except Exception as e:
    record("TEST A (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# TEST B: low confidence -> flagged_manual_review, no agent message
# ===================================================================
print("=== TEST B: low confidence ===")
try:
    pid_b = make_payment("lowconf", hour=12)
    fake = {"intent": "promise_to_pay", "confidence": 0.3, "mentioned_reason": None, "extracted_detail": None}
    with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake):
        result = handle_customer_reply(pid_b, "test low confidence", conn)
    print(result)

    record(
        "B1: outcome flagged_manual_review",
        result.get("decision", {}).get("outcome") == "flagged_manual_review",
        "flagged_manual_review", result.get("decision", {}).get("outcome")
    )
    record(
        "B2: action_type is None",
        result.get("decision", {}).get("action_type") is None,
        None, result.get("decision", {}).get("action_type")
    )
    record(
        "B3: no agent message generated",
        agent_msg_count(pid_b) == 0,
        0, agent_msg_count(pid_b)
    )
    record(
        "B4: recovery_status unchanged (open)",
        get_status(pid_b) == "open",
        "open", get_status(pid_b)
    )
except Exception as e:
    record("TEST B (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# TEST C: mismatch -> flagged_manual_review, flag_type=mismatch
# ===================================================================
print("=== TEST C: mismatch ===")
try:
    pid_c = make_payment("mismatch", hour=12, error_reason="authentication_failed")
    fake = {"intent": "general_query", "confidence": 0.9, "mentioned_reason": "insufficient_funds", "extracted_detail": None}
    with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake):
        result = handle_customer_reply(pid_c, "test mismatch", conn)
    print(result)

    record(
        "C1: outcome flagged_manual_review",
        result.get("decision", {}).get("outcome") == "flagged_manual_review",
        "flagged_manual_review", result.get("decision", {}).get("outcome")
    )
    record(
        "C2: flag_type is mismatch",
        result.get("decision", {}).get("flag_type") == "mismatch",
        "mismatch", result.get("decision", {}).get("flag_type")
    )
    record(
        "C3: no agent message generated",
        agent_msg_count(pid_c) == 0,
        0, agent_msg_count(pid_c)
    )
except Exception as e:
    record("TEST C (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# TEST D: dispute -> flagged_manual_review, flag_type=dispute_flag
# ===================================================================
print("=== TEST D: dispute ===")
try:
    pid_d = make_payment("dispute", hour=12)
    fake = {"intent": "dispute", "confidence": 0.95, "mentioned_reason": None, "extracted_detail": None}
    with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake):
        result = handle_customer_reply(pid_d, "this is not my payment", conn)
    print(result)

    record(
        "D1: outcome flagged_manual_review",
        result.get("decision", {}).get("outcome") == "flagged_manual_review",
        "flagged_manual_review", result.get("decision", {}).get("outcome")
    )
    record(
        "D2: flag_type is dispute_flag",
        result.get("decision", {}).get("flag_type") == "dispute_flag",
        "dispute_flag", result.get("decision", {}).get("flag_type")
    )
    record(
        "D3: no agent message generated",
        agent_msg_count(pid_d) == 0,
        0, agent_msg_count(pid_d)
    )
except Exception as e:
    record("TEST D (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# TEST E: outbound Gemini failure -> deterministic fallback still persisted
# ===================================================================
print("=== TEST E: outbound Gemini failure -> fallback persisted ===")
try:
    pid_e = make_payment("geminifail", hour=12, error_reason="expired_card")
    fake = {"intent": "promise_to_pay", "confidence": 0.9, "mentioned_reason": None, "extracted_detail": None}
    with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake), \
         patch("google.generativeai.GenerativeModel") as mock_model_cls:
        mock_model_cls.side_effect = Exception("forced outbound failure")
        result = handle_customer_reply(pid_e, "will pay soon", conn)
    print(result)

    record(
        "E1: decision still executed (control unaffected by message failure)",
        result.get("decision", {}).get("outcome") == "executed",
        "executed", result.get("decision", {}).get("outcome")
    )
    agent_row = conn.execute(
        "SELECT content FROM messages WHERE payment_id=? AND sender='agent'", (pid_e,)
    ).fetchone()
    record(
        "E2: fallback agent message persisted",
        agent_row is not None and "expired" in agent_row["content"].lower(),
        "a persisted expired_card fallback message", dict(agent_row) if agent_row else None
    )
except Exception as e:
    record("TEST E (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# TEST F: blocked outcome (outside contact hours) -> no agent message
# ===================================================================
print("=== TEST F: blocked_contact_hours ===")
try:
    pid_f = make_payment("blocked", hour=22)  # outside 9-20 window
    fake = {"intent": "promise_to_pay", "confidence": 0.9, "mentioned_reason": None, "extracted_detail": None}
    with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake):
        result = handle_customer_reply(pid_f, "will pay soon", conn)
    print(result)

    record(
        "F1: outcome blocked_contact_hours",
        result.get("decision", {}).get("outcome") == "blocked_contact_hours",
        "blocked_contact_hours", result.get("decision", {}).get("outcome")
    )
    record(
        "F2: no agent message generated",
        agent_msg_count(pid_f) == 0,
        0, agent_msg_count(pid_f)
    )
except Exception as e:
    record("TEST F (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# TEST G: message persistence failure (agent insert only) -> isolated
# ===================================================================
print("=== TEST G: agent message persistence failure isolation ===")

class ConnProxyFailSecondMessagesInsert:
    """Lets the FIRST 'INSERT INTO messages' (customer msg) succeed,
    fails the SECOND one (agent msg from deliver_recovery_message)."""
    def __init__(self, real_conn):
        self._conn = real_conn
        self._messages_insert_count = 0

    def execute(self, query, *args, **kwargs):
        if "INSERT INTO messages" in query:
            self._messages_insert_count += 1
            if self._messages_insert_count >= 2:
                raise RuntimeError("simulated agent-message persistence failure")
        return self._conn.execute(query, *args, **kwargs)

    def commit(self):
        return self._conn.commit()

try:
    pid_g = make_payment("persistfail", hour=12)
    fake = {"intent": "promise_to_pay", "confidence": 0.9, "mentioned_reason": None, "extracted_detail": None}
    proxy = ConnProxyFailSecondMessagesInsert(conn)

    status_before = get_status(pid_g)
    actions_before = action_count(pid_g)

    with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake):
        result = handle_customer_reply(pid_g, "will pay soon", proxy)
    print(result)

    status_after = get_status(pid_g)
    actions_after = action_count(pid_g)

    record(
        "G1: recovery decision still executed despite later message failure",
        result.get("decision", {}).get("outcome") == "executed",
        "executed", result.get("decision", {}).get("outcome")
    )
    record(
        "G2: recovery_status unaffected by message persistence failure",
        status_before == status_after,
        f"unchanged ({status_before})", f"{status_before} -> {status_after}"
    )
    record(
        "G3: recovery_actions row still written once (unaffected)",
        actions_after == actions_before + 1,
        actions_before + 1, actions_after
    )
    record(
        "G4: no agent message persisted (insert failed as forced)",
        agent_msg_count(pid_g) == 0,
        0, agent_msg_count(pid_g)
    )
except Exception as e:
    record("TEST G (unexpected exception)", False, "no exception", str(e))


# ===================================================================
# SUMMARY
# ===================================================================
conn.close()
print("=" * 60)
passed = sum(1 for _, s in results if s == "PASS")
failed = sum(1 for _, s in results if s == "FAIL")
print(f"TOTAL: {passed} PASS, {failed} FAIL out of {len(results)} checks")
if failed:
    print("FAILED CHECKS:")
    for name, status in results:
        if status == "FAIL":
            print(f"  - {name}")
print("=" * 60)