import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()
BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID")
phone = "+918421971145"

BASE_SYSTEM_PROMPT = """You are Riley, a warm and caring health assistant from WellRing calling to check on an elderly patient named [elder_name].

Your goal is to have a friendly 2-3 minute health check-in conversation.

How to run the call:
- Start by saying: "Hello! This is Riley calling from WellRing. Am I speaking with [elder_name]?"
- Once they respond (any response), warmly greet them and ask how they are feeling today.
- Ask gently about their sleep, appetite, energy levels, and any aches or pains.
- If they mention any symptoms, listen carefully and ask one follow-up question.
- If they mention chest pain, difficulty breathing, or a fall — say: "That sounds serious. Please call 112 right away, and I will also let your family know."
- After gathering health info, call the assess_health_risk tool to log the outcome.
- End the call warmly: "Thank you for chatting with me today. Take care and have a good day!"

Rules:
- Keep responses SHORT — one or two sentences maximum.
- Be warm, slow, and patient. They may take time to respond.
- NEVER hang up abruptly. Always say a warm goodbye before ending.
- Do NOT end the call until you have asked at least 2-3 health questions.
- If there is silence, gently say "Are you still there?" and wait."""

async def main():
    dynamic_prompt = BASE_SYSTEM_PROMPT.replace("[elder_name]", "Test User")
    history_block = (
        "\n\nIMPORTANT — This patient's recent health history:\n"
        "  • 3 days ago: breathing problem, high fever\n"
        "  • 4 days ago: breathing problem, high fever, chest pain\n\n"
        "Start the call by warmly asking a specific follow-up about the most recent symptoms listed above."
    )
    dynamic_prompt += history_block
    
    user_id_val = "demo_sharma_001"

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

    bolna_payload = {
        "agent_id": BOLNA_AGENT_ID,
        "recipient_phone_number": phone,
        "agent_prompts": {
            "task_1": {
                "system_prompt": dynamic_prompt
            }
        },
        "default_webhook": "https://wellring-backend.onrender.com/bolna-webhook",
        "agent_config": {
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
    }
    
    if user_id_val:
        bolna_payload["metadata"] = {"user_id": user_id_val}

    async with httpx.AsyncClient(timeout=30) as client:
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
