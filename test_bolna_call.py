import asyncio
from dotenv import load_dotenv

load_dotenv()

# We need to ensure we don't start the FastAPI app just by importing
# But src.main imports a lot. Let's see if it works.
from src.main import _do_bolna_call

recipient = "+918421971145"

async def main():
    print(f"Triggering Bolna outbound call to {recipient}...")
    try:
        result = await _do_bolna_call(phone=recipient, user_name="Test User")
        print("Call triggered successfully:")
        print(result)
    except Exception as e:
        print(f"Failed to trigger call: {e}")

if __name__ == "__main__":
    asyncio.run(main())
