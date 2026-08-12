import asyncio
from src.database import get_call_timeline
from dotenv import load_dotenv

load_dotenv()

async def main():
    timeline = get_call_timeline(phone="+919082487585", limit=5)
    print(timeline)

if __name__ == "__main__":
    asyncio.run(main())
