# backend/scripts/verify_gemini.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os

# --- Step A: confirm key is loaded ---
key = os.environ.get("GEMINI_API_KEY")
print("=== Step A: env check ===")
print("Key found:", bool(key))
print("Key prefix:", key[:8] if key else None)
print()

# --- Step B: raw Gemini call (bypasses parse_intent.py's try/except) ---
# print("=== Step B: raw Gemini call ===")
# import google.generativeai as genai
# genai.configure(api_key=key)
# model = genai.GenerativeModel(model_name="gemini-3.6-flash")

# response = model.generate_content(
#     'Say hello in JSON: {"greeting": "..."}',
#     generation_config={"response_mime_type": "application/json"},
# )
# print(response.text)
# print()

# --- Step C: actual parse_reply_intent() test ---
print("=== Step C: parse_reply_intent() live test ===")
from llm.parse_intent import parse_reply_intent

result = parse_reply_intent(
    "I already updated my card, please retry the payment", [], "payment_failed"
)
print(result)