import asyncio
from src.main import _do_bolna_call

async def main():
    try:
        res = await _do_bolna_call(phone="+918421971145")
        print("Call triggered successfully:")
        print(res)
    except Exception as e:
        print(f"Error triggering call: {e}")

if __name__ == "__main__":
    asyncio.run(main())
