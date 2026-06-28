import asyncio
import httpx
import os
from dotenv import load_dotenv
import json

load_dotenv()
BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID")

async def main():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        print(resp.status_code)
        if resp.status_code == 200:
            print("Successfully fetched agent config!")
            data = resp.json()
            # print first 200 chars to verify
            print(json.dumps(data)[:200])
        else:
            print("Failed:", resp.text)

if __name__ == "__main__":
    asyncio.run(main())
