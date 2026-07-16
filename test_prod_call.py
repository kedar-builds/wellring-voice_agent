import httpx

url = "http://localhost:8000/call"
headers = {
    "X-API-Key": "***REMOVED***",
    "Content-Type": "application/json"
}
payload = {
    "phone": "+919082487585",
    "user_name": "Test User"
}

resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
print("Status Code:", resp.status_code)
print("Response:", resp.text)
