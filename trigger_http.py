import requests
import json
import os

with open("output.json", "r") as f:
    payload = json.load(f)

token = os.environ.get("BOLNA_WEBHOOK_SECRET", "")
print(f"Sending to webhook via requests with token: '{token}'...")
try:
    response = requests.post("http://localhost:8000/bolna-webhook" + (f"?token={token}" if token else ""), json=payload)
    print("Status code:", response.status_code)
    print("Response:", response.text)
except Exception as e:
    print(e)
