# backend/scripts/debug_parse_intent.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os
import json
import google.generativeai as genai

from llm.parse_intent import _SYSTEM_PROMPT, _build_user_prompt, GEMINI_MODEL, GEMINI_TIMEOUT_SECONDS

api_key = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=api_key)

model = genai.GenerativeModel(
    model_name=GEMINI_MODEL,
    system_instruction=_SYSTEM_PROMPT,
)

user_prompt = _build_user_prompt(
    "I already updated my card, please retry the payment", [], "payment_failed"
)

response = model.generate_content(
    user_prompt,
    generation_config={"response_mime_type": "application/json"},
    request_options={"timeout": GEMINI_TIMEOUT_SECONDS},
)

print("RAW TEXT:", response.text)
parsed = json.loads(response.text)
print("PARSED:", parsed)