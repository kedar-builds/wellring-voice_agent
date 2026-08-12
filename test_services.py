import os
from dotenv import load_dotenv

load_dotenv()

print("Testing Database...")
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("❌ DATABASE_URL is not set.")
else:
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        conn.close()
        print("✅ Neon PostgreSQL database connection successful!")
    except Exception as e:
        print(f"❌ Neon Database error: {e}")

print("\nTesting Twilio...")
use_twilio = os.environ.get("USE_TWILIO", "false").lower() == "true"
account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
from_phone = os.environ.get("TWILIO_FROM_PHONE")

if not use_twilio:
    print("⚠️ USE_TWILIO is not set to true.")
elif not all([account_sid, auth_token, from_phone]):
    print("❌ Twilio credentials missing from environment.")
else:
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        # List recent messages to verify API key/token credentials
        messages = client.messages.list(limit=1)
        print("✅ Twilio connection successful! Verified API key and permissions.")

    except Exception as e:
        print(f"❌ Twilio error: {e}")
