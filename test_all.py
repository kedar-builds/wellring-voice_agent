import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()
from src.main import _build_bolna_payload, _get_bolna_agent_config
from src.notifications import send_test_whatsapp

async def test_bolna():
    phone = "+918421971145"
    async with httpx.AsyncClient() as client:
        print("Fetching config...")
        config = await _get_bolna_agent_config(client)
        payload, name, ctx, prompt = _build_bolna_payload(phone, "Subaru", config)
        
        print("Initiating call to", phone)
        resp = await client.post(
            "https://api.bolna.ai/call",
            json=payload,
            headers={"Authorization": f"Bearer {os.environ.get('BOLNA_API_KEY')}"},
            timeout=30.0
        )
        print("Call Status:", resp.status_code)
        print("Call Response:", resp.text)

def test_whatsapp():
    phone = "+918421971145"
    print("Sending WhatsApp to", phone)
    result = send_test_whatsapp(phone, "Hello! This is a test WhatsApp message from WellRing.")
    print("WhatsApp Result:", result)

if __name__ == "__main__":
    asyncio.run(test_bolna())
    test_whatsapp()
