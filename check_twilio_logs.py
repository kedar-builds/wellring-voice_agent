import os
from twilio.rest import Client
from dotenv import load_dotenv
load_dotenv()
sid = os.environ.get("TWILIO_ACCOUNT_SID")
token = os.environ.get("TWILIO_AUTH_TOKEN")
try:
    client = Client(sid, token)
    messages = client.messages.list(limit=5)
    for msg in messages:
        print(f"Date: {msg.date_sent}, To: {msg.to}, Status: {msg.status}, Error Code: {msg.error_code}, Error Message: {msg.error_message}")
except Exception as e:
    print(f"Error: {e}")
