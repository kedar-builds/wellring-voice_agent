"""Read-only: identify the account + sender number behind the WhatsApp traffic."""
import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()
sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
token = os.environ.get("TWILIO_AUTH_TOKEN", "")

client = Client(sid, token)

acct = client.api.accounts(sid).fetch()
print(f"Account SID (local creds): {acct.sid}")
print(f"Friendly name: {acct.friendly_name!r}")
print(f"Type: {acct.type!r} | Status: {acct.status!r}")

print("\n=== Recent messages (from_ / to / status / error) ===")
for m in client.messages.list(limit=10):
    print(f"  {m.date_sent} | {m.from_} -> {m.to} | {m.status} | err={m.error_code} {m.error_message}")

print("\n=== Message services (v1) ===")
for svc in client.messaging.v1.services.list(limit=10):
    print(f"  {svc.friendly_name} ({svc.sid}) inbound_url={svc.inbound_request_url!r}")
    for num in svc.phone_numbers.list():
        print(f"    number: {num.phone_number}")
