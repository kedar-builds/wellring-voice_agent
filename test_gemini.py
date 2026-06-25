import os
import asyncio
from google import genai
from google.genai import types

async def test():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=gemini_key)
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="hello",
        )
        print("2.5-flash success")
    except Exception as e:
        print(f"2.5-flash error: {e}")

asyncio.run(test())
