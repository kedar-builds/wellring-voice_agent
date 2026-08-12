import os
import requests
from dotenv import load_dotenv

load_dotenv()

account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

if not account_sid or not auth_token:
    print("Missing Twilio credentials in .env")
    exit(1)

url = f"https://content.twilio.com/v1/Content"
response = requests.get(url, auth=(account_sid, auth_token))

if response.status_code == 200:
    data = response.json()
    contents = data.get("contents", [])
    if not contents:
        print("No content templates found.")
    else:
        for c in contents:
            print(f"SID: {c.get('sid')}")
            print(f"Name: {c.get('friendly_name')}")
            print(f"Language: {c.get('language')}")
            print(f"Types: {list(c.get('types', {}).keys())}")
            print("-" * 40)
else:
    print(f"Error fetching templates: {response.status_code} - {response.text}")
