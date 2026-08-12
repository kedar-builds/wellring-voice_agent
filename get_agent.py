import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

async def main():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        print("Keys:", resp.json().keys())

if __name__ == "__main__":
    asyncio.run(main())
