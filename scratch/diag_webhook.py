"""Diagnose inbound Twilio webhook (error 12300) against the deployed backend.

Error 12300 = Twilio received the inbound message but the webhook URL returned
a non-2xx or invalid response. Causes: (a) webhook URL not configured in the
Twilio Console, (b) URL mismatch so signature validation fails -> 403,
(c) TWILIO_AUTH_TOKEN unset on Railway -> fail-closed 403.

This script POSTs exactly what Twilio would send, with a signature computed
from the LOCAL auth token. If Railway shares the token, we get 200 + TwiML.
A 403 means the deployed token is missing or different.
"""
import os
from urllib.parse import urlencode
from twilio.request_validator import RequestValidator
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://wellring-backend-production.up.railway.app"
WEBHOOK_URL = BASE + "/twilio-webhook"

form = {
    "MessageSid": "SM_deployed_probe_1234",
    "From": "whatsapp:+918421971145",
    "To": "whatsapp:+17372508034",
    "Body": "probe",
    "NumMedia": "0",
}

token = os.environ.get("TWILIO_AUTH_TOKEN", "")
print(f"Local TWILIO_AUTH_TOKEN: {'set (len %d)' % len(token) if token else 'UNSET'}")

if token:
    validator = RequestValidator(token)
    sig = validator.compute_signature(WEBHOOK_URL, form)
    print(f"Computed signature: {sig[:40]}...")
    r = httpx.post(WEBHOOK_URL, data=form, headers={"X-Twilio-Signature": sig}, timeout=30)
    print(f"\nPOST {WEBHOOK_URL} (valid signature) -> HTTP {r.status_code}")
    print("Response body:", r.text[:500])

# Also test unsigned request to observe fail-closed behavior
r2 = httpx.post(WEBHOOK_URL, data=form, timeout=30)
print(f"\nPOST {WEBHOOK_URL} (no signature)   -> HTTP {r2.status_code}")
print("Response body:", r2.text[:300])
