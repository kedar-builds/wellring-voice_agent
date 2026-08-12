import time
import requests
import json

print("Waiting for server...")
for _ in range(15):
    try:
        r = requests.get("http://127.0.0.1:8000/docs")
        if r.status_code == 200:
            print("Server is up!")
            break
    except requests.exceptions.ConnectionError:
        time.sleep(1)
else:
    print("Server did not start in time.")
    exit(1)

with open("output.json", "r") as f:
    payload = json.load(f)

print("Sending webhook payload...")
r = requests.post("http://127.0.0.1:8000/bolna-webhook?token=", json=payload)
print(r.status_code)
print(r.text)
