import asyncio
from src.main import _do_bolna_call
from dotenv import load_dotenv

load_dotenv()

async def main():
    res = await _do_bolna_call("+919082487585")
    print(res)

asyncio.run(main())
