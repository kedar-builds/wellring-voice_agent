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
        # Bolna has an endpoint to get executions/calls, e.g. GET /calls or GET /agent/{id}/executions
        # Let's try GET /batches or GET /executions
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}/executions",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        if resp.status_code == 200:
            print("Executions:", json.dumps(resp.json(), indent=2))
        else:
            # Maybe it's GET /calls?
            resp2 = await client.get(
                "https://api.bolna.ai/calls",
                headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
            )
            print("Calls status:", resp2.status_code)
            if resp2.status_code == 200:
                print("Calls:", json.dumps(resp2.json(), indent=2)[:2000])
            else:
                print("Failed both.")
                print(resp.text)
                print(resp2.text)

if __name__ == "__main__":
    asyncio.run(main())
