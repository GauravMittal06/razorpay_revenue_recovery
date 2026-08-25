# backend/scripts/debug_live_failure.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from llm.parse_intent import parse_reply_intent

# Same shape as a second-reply call: non-empty history
history = [{"sender": "customer", "content": "i will update the payment method shortly", "timestamp": 0}]
result = parse_reply_intent("account se paise katt gaye", history, "payment_failed")
print(result)