"""Read-only: inspect the Twilio number's inbound webhook config.

If the number's sms_url is empty/wrong, that's the root cause of 12300.
Also lists the account's phone numbers and messaging services.
"""
import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()
sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
token = os.environ.get("TWILIO_AUTH_TOKEN", "")

client = Client(sid, token)

print("=== Incoming phone numbers ===")
for num in client.incoming_phone_numbers.list(limit=20):
    print(f"  {num.phone_number}:")
    print(f"    sms_url={num.sms_url!r}  sms_method={num.sms_method}")
    print(f"    voice_url={num.voice_url!r}  voice_method={num.voice_method}")
    print(f"    capabilities=voice:{num.capabilities.get('voice')} sms:{num.capabilities.get('sms')} mms:{num.capabilities.get('mms')}")

print("\n=== Messaging services ===")
for svc in client.messaging.services.list(limit=10):
    print(f"  {svc.friendly_name} ({svc.sid}):")
    print(f"    inbound_url={svc.inbound_request_url!r}  inbound_method={svc.inbound_method}")

print("\n=== WhatsApp senders (alpha senders / whatsapp numbers) ===")
try:
    for sender in client.messaging.services.list(limit=10):
        for s in sender.phone_numbers.list():
            print(f"  {s.phone_number}")
except Exception as e:
    print(f"  (n/a: {e})")
