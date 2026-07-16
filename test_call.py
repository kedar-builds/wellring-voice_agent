import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
    BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID", "59528716-267c-4a93-af51-97e7282f0123")
    
    print(f"API KEY: {BOLNA_API_KEY[:5]}... AGENT ID: {BOLNA_AGENT_ID}")
    
    async with httpx.AsyncClient() as client:
        # 1. Fetch agent config
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        print("AGENT FETCH:", resp.status_code)
        
        if resp.status_code == 200:
            agent_config = resp.json()
            if "agent_config" in agent_config:
                agent_config = agent_config["agent_config"]
                
            bolna_payload = {
                "agent_id": BOLNA_AGENT_ID,
                "recipient_phone_number": "+919004261186",  # test phone
                "agent_config": agent_config
            }
            
            call_resp = await client.post(
                "https://api.bolna.ai/call",
                headers={"Authorization": f"Bearer {BOLNA_API_KEY}", "Content-Type": "application/json"},
                json=bolna_payload
            )
            print("CALL RESPONSE:", call_resp.status_code)
            print(call_resp.text)
        else:
            print("Failed to fetch agent:", resp.text)

asyncio.run(main())
