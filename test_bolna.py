import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    async with httpx.AsyncClient() as c:
        url = "https://api.bolna.ai/agent/" + os.environ.get("BOLNA_AGENT_ID", "")
        print("URL:", url)
        r = await c.get(url, headers={"Authorization": "Bearer " + os.environ.get("BOLNA_API_KEY", "")})
        print(r.json())

asyncio.run(run())
