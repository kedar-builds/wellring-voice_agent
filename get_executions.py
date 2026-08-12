import asyncio
import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://api.bolna.ai/executions",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        print(resp.status_code)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2)[:1000])
        else:
            print(resp.text)
            
if __name__ == "__main__":
    asyncio.run(main())
