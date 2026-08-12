from fastapi.testclient import TestClient
from src.main import app
import json
import os

print("Initializing TestClient...")
try:
    with TestClient(app) as client:
        print("TestClient initialized.")
        with open("output.json", "r") as f:
            payload = json.load(f)
        
        token = os.environ.get("BOLNA_WEBHOOK_SECRET", "")
        print(f"Sending to webhook via TestClient with token: '{token}'...")
        response = client.post("/bolna-webhook", json=payload, params={"token": token} if token else None)
        print("Status code:", response.status_code)
        print("Response:", response.text)
except Exception:
    import traceback
    traceback.print_exc()
