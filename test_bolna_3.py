import asyncio
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()

BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

async def test_bolna():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        fetched_agent = resp.json()
        print(json.dumps(fetched_agent, indent=2))

asyncio.run(test_bolna())
