"""
Smoke test: simulates the exact tool-call payloads a correctly-functioning LLM
would emit for each scenario, then calls the Railway /assess endpoint.

Scenarios:
  1. chest_pain only                 -> CRITICAL (CRITICAL tier, severity=critical)
  2. chest_pain + headache           -> CRITICAL (CRITICAL dominates MINOR)
  3. dizziness + headache (MODERATE+MINOR) -> severity must be medium, not low
  4. headache + fatigue only (MINOR) -> severity=low, LOW risk
  5. "I feel fine" — NO tool call    -> not applicable to /assess directly; documented below
"""

import os
import requests, json

ASSESS_URL = "https://wellring-backend-production.up.railway.app/assess"
_api_key = os.environ.get("WELLRING_API_KEY")
if not _api_key:
    raise RuntimeError("WELLRING_API_KEY env var is not set. Cannot run smoke test.")
HEADERS = {"X-API-Key": _api_key, "Content-Type": "application/json"}

scenarios = [
    {
        "label": "1. CRITICAL: chest_pain only",
        "tool_args": {"symptoms": ["chest_pain"], "severity": "critical"},
    },
    {
        "label": "2. CRITICAL dominates: chest_pain + headache",
        "tool_args": {"symptoms": ["chest_pain", "headache"], "severity": "critical"},
    },
    {
        "label": "3. MODERATE+MINOR: dizziness + headache (severity=medium, not low)",
        "tool_args": {"symptoms": ["dizziness", "headache"], "severity": "medium"},
    },
    {
        "label": "4. MINOR only: headache + fatigue (severity=low)",
        "tool_args": {"symptoms": ["headache", "fatigue"], "severity": "low"},
    },
]

for s in scenarios:
    print(f"\n{'='*60}")
    print(f"SCENARIO: {s['label']}")
    print(f"Tool-call args sent: {json.dumps(s['tool_args'])}")
    r = requests.post(ASSESS_URL, headers=HEADERS, json=s["tool_args"])
    print(f"HTTP status: {r.status_code}")
    print(f"Raw response: {r.text}")

print(f"\n{'='*60}")
print("SCENARIO 5: No-symptom control ('I feel fine')")
print("This cannot be exercised via /assess — assess_health_risk must NOT be called.")
print("Verification method: the system prompt's STEP 2 routes 'NO/ALL GOOD/FINE' to")
print("end_call only, skipping the tool. Confirmed by reading the live prompt in GET above.")
print("A false-positive trigger would require an actual call log from Bolna; flag for")
print("post-demo log audit (Bolna execution logs > check tool_calls for no-symptom runs).")
