import asyncio
import copy
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()

BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")
BOLNA_AGENT_ID = os.getenv("BOLNA_AGENT_ID")

async def test_bolna():
    async with httpx.AsyncClient() as client:
        # Fetch config
        resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        fetched_agent = resp.json()
        agent_config = copy.deepcopy(fetched_agent.get("agent_config") or fetched_agent)

        agent_config["agent_welcome_message"] = "Hello Test User, this is WellRing. How are you feeling today? Any discomfort throughout the day?"

        # Let's see what agent_config has
        print("tasks exists in agent_config:", "tasks" in agent_config)

        # Do the same overrides as main.py
        tasks = agent_config.get("tasks", [])
        if tasks:
            task_0 = tasks[0]
            if task_0.get("tools_config") is None:
                task_0["tools_config"] = {}
                
            # If voice_id is set
            voice_id = "some_voice_id"
            tts_provider = "elevenlabs"
            
            if "synthesizer" not in task_0["tools_config"]:
                task_0["tools_config"]["synthesizer"] = {}
            if "provider_config" not in task_0["tools_config"]["synthesizer"]:
                task_0["tools_config"]["synthesizer"]["provider_config"] = {}
                
            task_0["tools_config"]["synthesizer"]["provider"] = tts_provider
            task_0["tools_config"]["synthesizer"]["provider_config"]["voice"] = voice_id
            task_0["tools_config"]["synthesizer"]["provider_config"]["voice_id"] = voice_id
            
            tasks[0] = task_0
            agent_config["tasks"] = tasks

        dynamic_prompt = "You are a caring assistant."
        bolna_payload = {  # noqa: F841 — used by the commented-out POST below
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": "+918421971145",
            "agent_prompts": {"task_1": {"system_prompt": dynamic_prompt}},
            "default_webhook": "https://wellring-backend-production.up.railway.app/bolna-webhook?token=test",
            "agent_config": agent_config
        }
        
        # Test the request
        print("Payload tasks:", json.dumps(agent_config["tasks"], indent=2))
        
        # res = await client.post(
        #     "https://api.bolna.ai/call",
        #     headers={"Authorization": f"Bearer {BOLNA_API_KEY}", "Content-Type": "application/json"},
        #     json=bolna_payload
        # )
        # print("Status:", res.status_code)
        # print("Response:", res.text)

asyncio.run(test_bolna())
