import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID")

async def main():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        agent_config = resp.json()
        
        payload = {
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": "+919999999999",
            "agent_config": agent_config,
            "default_webhook": "https://wellring-backend.onrender.com/bolna-webhook",
            "webhook": "https://wellring-backend.onrender.com/bolna-webhook"
        }
        
        post_resp = await client.post(
            "https://api.bolna.ai/call",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}", "Content-Type": "application/json"},
            json=payload
        )
        
        print("POST /call Status:", post_resp.status_code)
        print("POST /call Body:", post_resp.text)

if __name__ == "__main__":
    asyncio.run(main())
