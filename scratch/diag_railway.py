"""Read-only probe of the deployed Railway backend."""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://wellring-backend-production.up.railway.app"
api_key = os.environ.get("WELLRING_API_KEY", "")
print(f"WELLRING_API_KEY locally: {'set (len %d)' % len(api_key) if api_key else 'UNSET'}")

headers = {"X-API-Key": api_key} if api_key else {}

for path in ("/health", "/storage-status", "/config-check"):
    try:
        r = httpx.get(BASE + path, headers=headers, timeout=20)
        print(f"\nGET {path} -> HTTP {r.status_code}")
        print(r.text[:1200])
    except Exception as e:
        print(f"\nGET {path} -> ERROR: {e}")
