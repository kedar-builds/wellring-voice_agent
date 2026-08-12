import os
import requests
from dotenv import load_dotenv
import sys

load_dotenv()
BOLNA_API_KEY = os.getenv("BOLNA_API_KEY")

headers = {
    'Authorization': f'Bearer {BOLNA_API_KEY}'
}

execution_id = sys.argv[1]
# Based on Bolna API docs, it might be /executions or we can fetch agent executions and filter
response = requests.get("https://api.bolna.ai/agent/220c3652-eb24-4b9b-b00a-766c8c64bdda/executions", headers=headers)
if response.status_code == 200:
    execs = response.json()
    for run in execs:
        if run.get("id") == execution_id or run.get("run_id") == execution_id:
            import json
            with open("output.json", "w") as f:
                json.dump(run, f)
            print("Successfully dumped to output.json")
else:
    print(response.status_code, response.text)
