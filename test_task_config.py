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
        if resp.status_code == 200:
            data = resp.json()
            try:
                task_config = data["tasks"][0]["task_config"]
                print(json.dumps(task_config, indent=2))
            except Exception as e:
                print("Error extracting task_config:", e)
        else:
            print("Failed:", resp.text)

if __name__ == "__main__":
    asyncio.run(main())
