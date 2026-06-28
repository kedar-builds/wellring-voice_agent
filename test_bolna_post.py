import asyncio
import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID")

async def main():
    async with httpx.AsyncClient() as client:
        # We don't want to actually call anyone real, or maybe we do?
        # Let's just pass a fake number.
        payload = {
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": "+919999999999",
            "default_webhook": "https://wellring-backend.onrender.com/bolna-webhook",
            "webhook": "https://wellring-backend.onrender.com/bolna-webhook",
            "agent_prompts": {
                "task_1": {
                    "system_prompt": "Test system prompt."
                }
            }
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
