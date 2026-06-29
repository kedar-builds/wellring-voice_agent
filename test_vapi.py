import asyncio
from src.main import initiate_call, CallRequest

async def main():
    payload = CallRequest(phone="+918421971145", user_name="Test User")
    try:
        await initiate_call(payload, api_key="***REMOVED***")
    except Exception as e:
        print(f"Exception: {e.detail if hasattr(e, 'detail') else e}")

asyncio.run(main())
