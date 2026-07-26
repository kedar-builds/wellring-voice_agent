"""
WellRing System Integrity Check
Run: python3 check_system.py
"""
import os, json, time, sys
from pathlib import Path

# Load .env manually so it works standalone
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import requests
import psycopg2
import psycopg2.extras
import boto3

BASE = "http://localhost:8000"
KEY = os.environ.get("WELLRING_API_KEY")
if not KEY:
    raise RuntimeError("WELLRING_API_KEY env var is not set. Cannot run health check.")
H = {"X-API-Key": KEY, "Content-Type": "application/json"}

results = {}

def check(label, fn):
    try:
        ok, detail = fn()
        status = "✅" if ok else "❌"
        print(f"  {status} {label}: {detail}")
        results[label] = ok
    except Exception as e:
        print(f"  ❌ {label}: EXCEPTION — {e}")
        results[label] = False

print("\n" + "=" * 60)
print("  WellRing System Integrity Check")
print("=" * 60)

# ── 1. Health endpoint ────────────────────────────────────────
print("\n[1] Core API")
def _health():
    r = requests.get(f"{BASE}/health", timeout=5)
    return r.status_code == 200, f"HTTP {r.status_code} {r.json()}"
check("Health endpoint", _health)

# ── 2. Storage status endpoint ────────────────────────────────
def _storage_endpoint():
    r = requests.get(f"{BASE}/storage-status", headers=H, timeout=5)
    return r.status_code in (200, 404), f"HTTP {r.status_code}"
check("Storage status endpoint", _storage_endpoint)

# ── 3. Assessment flow (scoring + DB) ─────────────────────────
print("\n[2] Assessment Flow")
BOLNA_CALL_ID = f"integrity-test-{int(time.time())}"

def _assess():
    payload = {
        "patient_id": "integrity_check_001",
        "intent": "health_issue",
        "severity": "medium",
        "chest_pain": True,
        "pain_level": 6,
        "shortness_of_breath": True,
        "bolna_call_id": BOLNA_CALL_ID,
        "transcript": "Patient reports chest pain and shortness of breath since morning.",
        "emotion_analysis": "Anxious"
    }
    r = requests.post(f"{BASE}/assess", json=payload, headers=H, timeout=10)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    d = r.json()
    rl = d.get("risk_level", "?")
    sc = d.get("score", "?")
    iid = d.get("interaction_id", "?")
    return True, f"risk={rl} score={sc} id={iid}"
check("POST /assess (scoring)", _assess)

time.sleep(1)  # let DB write complete

# ── 4. DB: assessments table ──────────────────────────────────
print("\n[3] Database Persistence")
db_url = os.environ.get("DATABASE_URL", "")

def _db_assessment():
    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("""
        SELECT assessment_id, risk_level, score, bolna_call_id, transcript, emotion_analysis
        FROM assessments WHERE bolna_call_id = %s LIMIT 1
    """, (BOLNA_CALL_ID,))
    row = cur.fetchone()
    conn.close()
    if row:
        return True, f"assessment_id={row['assessment_id']} risk={row['risk_level']} has_transcript={bool(row['transcript'])}"
    return False, "Row not found in assessments table"

def _db_counts():
    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM assessments) AS ass,
            (SELECT COUNT(*) FROM conversations) AS conv,
            (SELECT COUNT(*) FROM alerts) AS alerts,
            (SELECT COUNT(*) FROM health_history) AS hh,
            (SELECT COUNT(*) FROM users) AS users
    """)
    row = cur.fetchone()
    conn.close()
    return True, f"assessments={row['ass']} conversations={row['conv']} alerts={row['alerts']} health_history={row['hh']} users={row['users']}"

if db_url:
    check("Assessment persisted to DB", _db_assessment)
    check("Table row counts", _db_counts)
else:
    print("  ⚠️  DATABASE_URL not set — skipping DB checks")

# ── 5. Gemini API ─────────────────────────────────────────────
print("\n[4] Gemini (Transcript Analysis)")
def _gemini():
    from google import genai
    gem_key = os.environ.get("GEMINI_API_KEY", "")
    if not gem_key:
        return False, "GEMINI_API_KEY not set"
    client = genai.Client(api_key=gem_key)
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents="Respond with exactly one word: HEALTHY"
    )
    txt = (resp.text or "").strip()
    return "HEALTHY" in txt, f"Response: '{txt}'"
check("Gemini API call", _gemini)

# ── 6. Bolna API ──────────────────────────────────────────────
print("\n[5] Bolna (Outbound Calling)")
def _bolna_api():
    bolna_key = os.environ.get("BOLNA_API_KEY", "")
    bolna_agent = os.environ.get("BOLNA_AGENT_ID", "")
    if not bolna_key or not bolna_agent:
        return False, "BOLNA_API_KEY or BOLNA_AGENT_ID not set"
    r = requests.get(
        f"https://api.bolna.dev/call/list/{bolna_agent}",
        headers={"Authorization": f"Bearer {bolna_key}"},
        timeout=10
    )
    if r.status_code == 200:
        calls = r.json()
        count = len(calls) if isinstance(calls, list) else "?"
        latest_id = calls[0].get("call_id") or calls[0].get("id") if isinstance(calls, list) and calls else "none"
        latest_status = calls[0].get("status") if isinstance(calls, list) and calls else "none"
        return True, f"{count} calls — latest: id={latest_id} status={latest_status}"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"
check("Bolna API reachable", _bolna_api)

def _bolna_agent():
    bolna_key = os.environ.get("BOLNA_API_KEY", "")
    bolna_agent = os.environ.get("BOLNA_AGENT_ID", "")
    if not bolna_key or not bolna_agent:
        return False, "creds not set"
    r = requests.get(
        f"https://api.bolna.dev/agent/{bolna_agent}",
        headers={"Authorization": f"Bearer {bolna_key}"},
        timeout=10
    )
    if r.status_code == 200:
        d = r.json()
        return True, f"agent name='{d.get('agent_name') or d.get('name','?')}'"
    return False, f"HTTP {r.status_code}"
check("Bolna agent config", _bolna_agent)

# ── 7. Twilio ─────────────────────────────────────────────────
print("\n[6] Twilio (Alerts)")
def _twilio():
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        return False, "Twilio creds not set"
    r = requests.get(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
        auth=(sid, token), timeout=10
    )
    if r.status_code == 200:
        d = r.json()
        return True, f"account='{d.get('friendly_name')}' status={d.get('status')}"
    return False, f"HTTP {r.status_code}: {r.text[:150]}"
check("Twilio account accessible", _twilio)

# ── 8. Backblaze B2 ───────────────────────────────────────────
print("\n[7] Backblaze B2 (Call Recordings)")
def _b2():
    # BACKBLAZE_* takes priority; AWS_* kept as fallback for legacy
    b2_key_id  = os.environ.get("BACKBLAZE_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID", "")
    b2_app_key = os.environ.get("BACKBLAZE_APP_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    endpoint   = os.environ.get("BACKBLAZE_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL", "")
    bucket     = os.environ.get("BACKBLAZE_BUCKET") or os.environ.get("AWS_BUCKET_NAME", "")
    region     = os.environ.get("BACKBLAZE_REGION") or os.environ.get("AWS_REGION", "us-east-005")
    if not all([b2_key_id, b2_app_key, endpoint, bucket]):
        return False, "B2 credentials incomplete"
    s3 = boto3.client("s3",
        endpoint_url=endpoint,
        aws_access_key_id=b2_key_id,
        aws_secret_access_key=b2_app_key,
        region_name=region
    )
    objs = s3.list_objects_v2(Bucket=bucket, MaxKeys=5)
    count = objs.get("KeyCount", 0)
    keys = [o["Key"] for o in objs.get("Contents", [])]
    return True, f"bucket accessible — {count} object(s): {keys}"
check("Backblaze B2 bucket", _b2)

# ── 9. Bolna webhook endpoint ─────────────────────────────────
print("\n[8] Bolna Webhook Endpoint")
def _webhook_reachable():
    # Just check the endpoint returns something (not 404/405)
    test_payload = {"call_id": "test", "event": "call_started"}
    r = requests.post(f"{BASE}/bolna-webhook", json=test_payload, headers=H, timeout=5)
    return r.status_code not in (404, 500), f"HTTP {r.status_code}"
check("POST /bolna-webhook reachable", _webhook_reachable)

# ── Summary ───────────────────────────────────────────────────
passed = sum(1 for v in results.values() if v)
total = len(results)
print(f"\n{'=' * 60}")
print(f"  Summary: {passed}/{total} checks passed")
if passed < total:
    failed = [k for k, v in results.items() if not v]
    print(f"  Failed: {', '.join(failed)}")
print("=" * 60 + "\n")
