import asyncio
import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID")
phone = "+918421971145"
user_id_val = "demo_sharma_001"

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        bolna_payload = {
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": phone,
            "agent_prompts": {
                "task_1": {
                    "system_prompt": "Test prompt"
                }
            },
            "default_webhook": "https://wellring-backend.onrender.com/bolna-webhook"
        }
        
        task_config = {
            "tools_config": {
                "api_tools": {
                    "tools_params": {
                        "assess_health_risk": {
                            "param": {
                                "intent": "%(intent)s",
                                "symptoms": "%(symptoms)s",
                                "severity": "%(severity)s",
                                "confidence": "%(confidence)s",
                                "user_id": user_id_val
                            }
                        }
                    }
                }
            }
        }
        bolna_payload["agent_config"] = {
            "tasks": [task_config],
            "engine": {
                "transcription": {
                    "interruption_threshold": 1,
                    "generate_precise_transcript": True
                },
                "response_latency": {
                    "endpointing_ms": 200,
                    "linear_delay_ms": 50
                }
            }
        }

        resp = await client.post(
            "https://api.bolna.ai/call",
            headers={
                "Authorization": f"Bearer {BOLNA_API_KEY}",
                "Content-Type": "application/json"
            },
            json=bolna_payload
        )
        print(resp.status_code)
        print(resp.text)

if __name__ == "__main__":
    asyncio.run(main())
