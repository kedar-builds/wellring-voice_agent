import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

async def test():
    bolna_payload = {
        "agent_id": BOLNA_AGENT_ID,
        "recipient_phone_number": "+919004261186",
        "agent_prompts": {"task_1": {"system_prompt": "You are a test agent. Say hello and hang up."}},
        "default_webhook": "https://wellring-backend.onrender.com/bolna-webhook"
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.bolna.ai/call",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}", "Content-Type": "application/json"},
            json=bolna_payload,
        )
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")

asyncio.run(test())
