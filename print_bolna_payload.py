import asyncio
import json
from src.main import _build_bolna_payload, _get_bolna_agent_config
import httpx
from dotenv import load_dotenv

load_dotenv()

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        agent_config = await _get_bolna_agent_config(client)
        bolna_payload, resolved_name, ctx, dynamic_prompt = await asyncio.to_thread(
            _build_bolna_payload, "+918421971145", None, agent_config
        )
        print(json.dumps(bolna_payload, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
