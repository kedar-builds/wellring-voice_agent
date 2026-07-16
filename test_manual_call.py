import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

payload = {
    "agent_id": BOLNA_AGENT_ID,
    "recipient_phone_number": "+919082487585"
}

headers = {
    "Authorization": f"Bearer {BOLNA_API_KEY}",
    "Content-Type": "application/json"
}

resp = httpx.post("https://api.bolna.ai/call", json=payload, headers=headers)
print("Status Code:", resp.status_code)
print("Response:", resp.text)
