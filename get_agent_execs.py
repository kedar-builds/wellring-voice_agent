import asyncio
import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}/executions",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(resp.text)
            
if __name__ == "__main__":
    asyncio.run(main())
