import httpx
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID")

async def get_latest_calls():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}/executions",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        if resp.status_code == 200:
            data = resp.json()
            # Sort by initiated_at descending just in case
            data.sort(key=lambda x: x.get("initiated_at") or "", reverse=True)
            for call in data[:3]:
                call_id = call.get("id")
                initiated_at = call.get("initiated_at")
                duration = call.get("telephony_data", {}).get("duration")
                hangup_by = call.get("telephony_data", {}).get("hangup_by")
                hangup_reason = call.get("telephony_data", {}).get("hangup_reason")
                status = call.get("status")
                usage = call.get("usage_breakdown", {})
                silence_timeout = usage.get("hangup_after_silence")
                print(f"Call ID: {call_id}")
                print(f"Initiated at: {initiated_at}")
                print(f"Duration: {duration}")
                print(f"Hangup By: {hangup_by}")
                print(f"Hangup Reason: {hangup_reason}")
                print(f"Status: {status}")
                print(f"hangup_after_silence config: {silence_timeout}")
                print("-" * 40)
        else:
            print(f"Failed: {resp.status_code} {resp.text}")

if __name__ == "__main__":
    asyncio.run(get_latest_calls())
