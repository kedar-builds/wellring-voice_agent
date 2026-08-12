import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("BOLNA_WEBHOOK_SECRET", "")

async def main():
    async with httpx.AsyncClient() as client:
        # NOTE: default_webhook is REQUIRED for the post-call summary/recording
        # WhatsApp to be sent. Without it, Bolna falls back to the agent's
        # stored webhook (often None) and the webhook never reaches the backend.
        payload = {
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": "+918421971145",
            "default_webhook": f"{BASE_WEBHOOK_URL}/bolna-webhook?token={WEBHOOK_SECRET}",
            "metadata": {"user_id": "b286e754-6603-4676-8438-2543f576a4a9"},
            "agent_config": {
                "agent_welcome_message": "Hello, this is a test. Are you there?"
            }
        }
        resp = await client.post(
            "https://api.bolna.ai/call",
            headers={
                "Authorization": f"Bearer {BOLNA_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload
        )
        print("Status:", resp.status_code)
        print("Response:", resp.text)

if __name__ == "__main__":
    asyncio.run(main())
