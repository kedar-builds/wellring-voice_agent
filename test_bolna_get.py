import asyncio
import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

async def test():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        print(f"Status: {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2))
        except:
            print(resp.text)

asyncio.run(test())
