"""Send a live test WhatsApp through the deployed backend, then confirm delivery."""
import os
import time
import httpx
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

BASE = "https://wellring-backend-production.up.railway.app"
api_key = os.environ.get("WELLRING_API_KEY", "")

print("=== 1. POST /test-whatsapp on deployed backend ===")
r = httpx.post(
    BASE + "/test-whatsapp",
    json={"to_phone": "+918421971145", "patient_name": "Atharva"},
    headers={"X-API-Key": api_key},
    timeout=60,
)
print(f"HTTP {r.status_code}")
print(r.text[:800])

print("\n=== 2. Wait 15s, then check Twilio for the newest message ===")
time.sleep(15)
sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
token = os.environ.get("TWILIO_AUTH_TOKEN", "")
client = Client(sid, token)
msgs = client.messages.list(limit=3)
for m in msgs:
    print(f"  {m.date_sent} | {m.from_} -> {m.to} | {m.status} | err={m.error_code} {m.error_message}")
