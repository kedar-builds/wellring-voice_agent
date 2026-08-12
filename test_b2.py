import asyncio
from src.storage import upload_recording_to_b2
from dotenv import load_dotenv

load_dotenv()

async def main():
    url = "https://api.bolna.ai/recordings/call/6cffc889-0ce1-49f7-917b-a9d22fb8a780"
    res = await upload_recording_to_b2(url, "+1234567890")
    print("Result URL:", res)

asyncio.run(main())
