import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))
import sys; sys.path.append(".")
from db.db import get_connection
from engine.classify import classify
from engine.decide_action import decide_action
from engine.execute_action import execute_action

conn = get_connection()
payment = dict(conn.execute("SELECT * FROM payments WHERE event_type='payment_failed' LIMIT 1").fetchone())
classification = classify(payment)

# Case A: low confidence -> should hard-stop
d1 = decide_action(payment, classification, conn, extracted_intent="promise_to_pay", intent_confidence=0.4)
print(d1)

# Case B: dispute -> should hard-stop
d2 = decide_action(payment, classification, conn, extracted_intent="dispute", intent_confidence=0.9, dispute_flag=True)
print(d2)

# Case C: mismatch -> should hard-stop
d3 = decide_action(payment, classification, conn, extracted_intent="general_query", intent_confidence=0.9, mentioned_reason="network_error")
print(d3)  # only meaningful if payment's root_cause != network_error

# Case D: root_cause_update_candidate -> should NOT block
d4 = decide_action(payment, classification, conn, extracted_intent="payment_method_updated", intent_confidence=0.9, mentioned_reason="expired_card")
print(d4)  # only meaningful if payment's root_cause != expired_card

# check payment's recovery_status BEFORE
before = dict(conn.execute("SELECT recovery_status FROM payments WHERE id=?", (payment["id"],)).fetchone())
print("BEFORE:", before)

r1 = execute_action(payment, d1, conn)
print("execute_action result:", r1)

rows = conn.execute(
    "SELECT outcome, action_type, flag_type FROM recovery_actions WHERE payment_id=?",
    (payment["id"],)
).fetchall()
for r in rows:
    print(dict(r))

after = dict(conn.execute("SELECT recovery_status FROM payments WHERE id=?", (payment["id"],)).fetchone())
print("AFTER:", after)