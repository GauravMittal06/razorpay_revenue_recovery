# backend/scripts/test_deliver_message.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from unittest.mock import patch
from db.db import get_connection
from engine.classify import classify
from engine.deliver_message import deliver_recovery_message
from engine.core_loop import run_cycle
from engine.handle_customer_reply import handle_customer_reply

conn = get_connection()

def msg_count(payment_id, sender=None):
    if sender:
        return conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE payment_id=? AND sender=?", (payment_id, sender)
        ).fetchone()["c"]
    return conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE payment_id=?", (payment_id,)
    ).fetchone()["c"]

def action_count(payment_id):
    return conn.execute(
        "SELECT COUNT(*) c FROM recovery_actions WHERE payment_id=?", (payment_id,)
    ).fetchone()["c"]

def get_status(payment_id):
    return conn.execute(
        "SELECT recovery_status FROM payments WHERE id=?", (payment_id,)
    ).fetchone()["recovery_status"]

row = conn.execute("SELECT * FROM payments WHERE event_type='payment_failed' LIMIT 1").fetchone()
payment = dict(row)
pid = payment["id"]
classification = classify(payment)
print(f"Using payment_id={pid}\n")

# ---------- TEST 1: executed retry -> message generated + persisted ----------
print("=== TEST 1: executed retry ===")
before = msg_count(pid, "agent")
d = {"action_type": "retry", "outcome": "executed", "allowed": True, "reasoning": "x", "triggered_by": "rule"}
r = deliver_recovery_message(payment, classification, d, conn)
print(r)
after = msg_count(pid, "agent")
print("agent messages before/after:", before, "->", after)
assert r["delivered"] is True and r["status"] in ("ok", "fallback") and r["message"]
assert after == before + 1
print("PASS\n")

# ---------- TEST 2: executed reminder -> message generated + persisted ----------
print("=== TEST 2: executed reminder ===")
before = msg_count(pid, "agent")
d = {"action_type": "reminder", "outcome": "executed", "allowed": True, "reasoning": "x", "triggered_by": "rule"}
r = deliver_recovery_message(payment, classification, d, conn)
print(r)
after = msg_count(pid, "agent")
print("agent messages before/after:", before, "->", after)
assert r["delivered"] is True and after == before + 1
print("PASS\n")

# ---------- TEST 3: escalate -> no message ----------
print("=== TEST 3: escalate ===")
before = msg_count(pid, "agent")
d = {"action_type": "escalate", "outcome": "executed", "allowed": True, "reasoning": "x", "triggered_by": "rule"}
r = deliver_recovery_message(payment, classification, d, conn)
print(r)
after = msg_count(pid, "agent")
assert r == {"delivered": False, "status": "skipped_ineligible", "message": None}
assert after == before
print("PASS\n")

# ---------- TEST 4: stop -> no message ----------
print("=== TEST 4: stop ===")
before = msg_count(pid, "agent")
d = {"action_type": "stop", "outcome": "executed", "allowed": True, "reasoning": "x", "triggered_by": "rule"}
r = deliver_recovery_message(payment, classification, d, conn)
print(r)
after = msg_count(pid, "agent")
assert r["status"] == "skipped_ineligible" and after == before
print("PASS\n")

# ---------- TEST 5: blocked_* -> no message ----------
print("=== TEST 5: blocked outcomes ===")
for outcome in ["blocked_cooldown", "blocked_contact_hours", "blocked_already_stopped", "blocked_already_escalated"]:
    before = msg_count(pid, "agent")
    d = {"action_type": "retry", "outcome": outcome, "allowed": False, "reasoning": "x", "triggered_by": "rule"}
    r = deliver_recovery_message(payment, classification, d, conn)
    after = msg_count(pid, "agent")
    print(outcome, "->", r, "| count", before, "->", after)
    assert r["status"] == "skipped_ineligible" and after == before
print("PASS\n")

# ---------- TEST 6: flagged_manual_review -> no message ----------
print("=== TEST 6: flagged_manual_review ===")
before = msg_count(pid, "agent")
d = {"action_type": None, "outcome": "flagged_manual_review", "allowed": False, "reasoning": "x", "triggered_by": "rule", "flag_type": "mismatch"}
r = deliver_recovery_message(payment, classification, d, conn)
print(r)
after = msg_count(pid, "agent")
assert r["status"] == "skipped_ineligible" and after == before
print("PASS\n")

# ---------- TEST 7: Gemini failure -> deterministic fallback persisted ----------
print("=== TEST 7: Gemini failure -> fallback persisted ===")
before = msg_count(pid, "agent")
d = {"action_type": "retry", "outcome": "executed", "allowed": True, "reasoning": "x", "triggered_by": "rule"}
with patch("google.generativeai.GenerativeModel") as mock_model_cls:
    mock_model_cls.side_effect = Exception("forced failure")
    r = deliver_recovery_message(payment, classification, d, conn)
print(r)
after = msg_count(pid, "agent")
assert r["delivered"] is True and r["status"] == "fallback" and r["message"]
assert after == before + 1
last_msg = conn.execute(
    "SELECT content FROM messages WHERE payment_id=? AND sender='agent' ORDER BY message_id DESC LIMIT 1", (pid,)
).fetchone()
print("Persisted content:", last_msg["content"])
print("PASS\n")

# ---------- TEST 8: persistence failure -> already-executed action unaffected ----------
print("=== TEST 8: messages persistence failure isolation ===")
status_before = get_status(pid)
actions_before = action_count(pid)
d = {"action_type": "retry", "outcome": "executed", "allowed": True, "reasoning": "x", "triggered_by": "rule"}

class FailingMessagesConn:
    def __init__(self, real_conn):
        self._conn = real_conn
    def execute(self, query, *args, **kwargs):
        if "INSERT INTO messages" in query:
            raise RuntimeError("simulated DB failure")
        return self._conn.execute(query, *args, **kwargs)
    def commit(self):
        return self._conn.commit()

faulty_conn = FailingMessagesConn(conn)
r = deliver_recovery_message(payment, classification, d, faulty_conn)
print(r)
status_after = get_status(pid)
actions_after = action_count(pid)
assert r["status"] == "persist_failed" and r["delivered"] is False
assert status_before == status_after
assert actions_before == actions_after
print("recovery_status unaffected:", status_before, "->", status_after)
print("PASS\n")

# ---------- TEST 9a: core_loop.py wiring ----------
print("=== TEST 9a: core_loop.py integration ===")
conn.close()
results = run_cycle()
conn2 = get_connection()
executed_retry_reminder = conn2.execute(
    """SELECT payment_id FROM recovery_actions
       WHERE outcome='executed' AND action_type IN ('retry','reminder')"""
).fetchall()
print(f"executed retry/reminder rows this run: {len(executed_retry_reminder)}")
missing = []
for row in executed_retry_reminder:
    pid_check = row["payment_id"]
    c = conn2.execute(
        "SELECT COUNT(*) c FROM messages WHERE payment_id=? AND sender='agent'", (pid_check,)
    ).fetchone()["c"]
    if c == 0:
        missing.append(pid_check)
print("payments with executed retry/reminder but NO agent message:", missing)
assert missing == [], "FAIL: some eligible actions did not get a message delivered"
print("PASS\n")

# ---------- TEST 9b: handle_customer_reply.py wiring ----------
print("=== TEST 9b: handle_customer_reply.py integration ===")
fake_decision = {"action_type": "reminder", "outcome": "executed", "allowed": True,
                  "reasoning": "forced for wiring test", "triggered_by": "rule", "flag_type": None}
fake_intent = {"intent": "promise_to_pay", "confidence": 0.9, "mentioned_reason": None, "extracted_detail": None}

before = conn2.execute(
    "SELECT COUNT(*) c FROM messages WHERE payment_id=? AND sender='agent'", (pid,)
).fetchone()["c"]

with patch("engine.handle_customer_reply.parse_reply_intent", return_value=fake_intent), \
     patch("engine.handle_customer_reply.decide_action", return_value=fake_decision):
    result = handle_customer_reply(pid, "wiring test message", conn2)

print(result)
after = conn2.execute(
    "SELECT COUNT(*) c FROM messages WHERE payment_id=? AND sender='agent'", (pid,)
).fetchone()["c"]
print("agent messages before/after:", before, "->", after)
assert result["status"] == "ok"
assert after == before + 1
print("PASS\n")

# ---------- TEST 10: no duplicate/malformed rows ----------
print("=== TEST 10: row integrity check ===")
rows = conn2.execute(
    "SELECT message_id, sender, content, intent_extracted, intent_confidence, mentioned_reason FROM messages WHERE payment_id=? ORDER BY message_id", (pid,)
).fetchall()
seen_ids = set()
for r in rows:
    rd = dict(r)
    print(rd)
    assert rd["message_id"] not in seen_ids, "FAIL: duplicate message_id"
    seen_ids.add(rd["message_id"])
    if rd["sender"] == "agent":
        assert rd["intent_extracted"] is None and rd["intent_confidence"] is None and rd["mentioned_reason"] is None, \
            "FAIL: agent row has non-null customer-reply-specific fields"
        assert rd["content"] and rd["content"].strip() != "", "FAIL: empty agent message content"
print("PASS\n")

conn2.close()
print("=== ALL TESTS COMPLETED ===")