#!/usr/bin/env python3
"""
smoke_test_login.py
===================
End-to-end login-flow smoke test for the WellRing backend.

Runs the real FastAPI app in-process (TestClient) against an isolated
temporary SQLite database and exercises the full caregiver login →
onboarding → dashboard flow:

  /health → /setup-profile (create elder) → /setup-profile (read back) →
  /reminders (create/list/delete) → /family-contacts (add/list/delete) →
  /patients → /assess (simulated check-in call) → /assessments →
  /assessments/stats → /timeline  +  the data-isolation guard
  (no uid → empty results).

Modes:
  * Dev mode (default)        — CLERK_SECRET_KEY unset: legacy `clerk_id`
                                scoping drives everything.
  * Production mode           — set CLERK_SECRET_KEY in the environment AND
                                pass a real Clerk session JWT with --token.
                                Asserts requests WITHOUT the token get 401
                                and that the full flow works with it.

Getting a real Clerk session token (production mode):
  1. Log in to the frontend in your browser.
  2. Open DevTools → Console and run:   await window.Clerk.session.getToken()
  3. Copy the returned JWT and pass it via --token.

Usage:
  venv/bin/python scripts/smoke_test_login.py                       # dev mode
  CLERK_SECRET_KEY=sk_... venv/bin/python scripts/smoke_test_login.py \\
      --token eyJhbGciOi...
"""

import argparse
import os
import sys
import tempfile

# Make the project root importable when this script is run directly
# (venv/bin/python scripts/smoke_test_login.py → sys.path[0] == scripts/).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Isolated test environment — MUST be set before importing src.main (whose
# load_dotenv() never overrides already-set variables).
# ---------------------------------------------------------------------------
_tmpdir = tempfile.mkdtemp(prefix="wellring_smoke_")
os.environ["WELLRING_DB_PATH"] = os.path.join(_tmpdir, "smoke.db")
os.environ["DATABASE_URL"] = ""                       # force SQLite
os.environ["WELLRING_API_KEY"] = "wellring-smoke-key-local"
os.environ["RATE_LIMIT_REQUESTS_PER_MINUTE"] = "100000"
os.environ["RATE_LIMIT_FAILURES_PER_WINDOW"] = "100000"
# Never make real external calls during a smoke test — regardless of .env.
os.environ["USE_TWILIO"] = "false"
os.environ["USE_WHATSAPP"] = "false"
os.environ["USE_ROUTINE_UPDATES"] = "false"
os.environ["OPENROUTER_API_KEY"] = ""  # keep the Nemotron watchdog dormant

import shutil  # noqa: E402  (used only at the end)

from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402

API_KEY = os.environ["WELLRING_API_KEY"]
SMOKE_UID = "smoke_login_uid_001"
ELDER_PHONE = "+919000000000"

_failures = []


def step(name: str, ok: bool, detail: str = ""):
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def _headers(token: str = "") -> dict:
    h = {"X-API-Key": API_KEY}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def run_flow(client, token: str = "") -> None:
    """Run the full login→onboarding→dashboard flow."""
    h = _headers(token)

    r = client.get("/health")
    step("GET /health", r.status_code == 200, f"HTTP {r.status_code}")

    # -- Onboarding: create the elder profile -----------------------------
    r = client.post(
        "/setup-profile",
        headers=h,
        json={
            "clerk_id": SMOKE_UID,
            "elder_name": "Smoke Elder",
            "elder_phone": ELDER_PHONE,
            "elder_age": 72,
            "medical_conditions": ["Hypertension"],
            "medical_notes": "Smoke test profile",
            "family_contacts": [{"name": "Smoke Son", "phone": "+919000000001", "relationship": "son"}],
        },
    )
    ok = r.status_code == 200
    user_id = (r.json() or {}).get("user_id", "") if ok else ""
    step("POST /setup-profile (create elder)", ok, f"HTTP {r.status_code}, user_id={user_id!r}")
    if not ok:
        return  # nothing else can pass without a profile

    r = client.get(f"/setup-profile?clerk_id={SMOKE_UID}", headers=h)
    ok = r.status_code == 200 and (r.json() or {}).get("name") == "Smoke Elder"
    step("GET /setup-profile (read back)", ok, f"HTTP {r.status_code}, name={(r.json() or {}).get('name') if r.status_code == 200 else None}")

    # -- Reminders ----------------------------------------------------------
    r = client.post(
        "/reminders",
        headers=h,
        json={
            "type": "medicine",
            "title": "Amlodipine 5mg",
            "time": "09:00",
            "frequency": "daily",
            "phone": ELDER_PHONE,
            "notes": "Take with breakfast",
            "clerk_id": SMOKE_UID,
        },
    )
    ok = r.status_code == 201
    reminder_id = (r.json() or {}).get("id") if ok else None
    step("POST /reminders (create)", ok, f"HTTP {r.status_code}, id={reminder_id!r}")
    if ok:
        r = client.get(f"/reminders?clerk_id={SMOKE_UID}", headers=h)
        step("GET /reminders (list)", r.status_code == 200 and len(r.json()) >= 1, f"HTTP {r.status_code}, count={len(r.json()) if r.status_code == 200 else '?'}")

    # -- Family contacts ----------------------------------------------------
    r = client.post(
        "/family-contacts",
        headers=h,
        json={"clerk_id": SMOKE_UID, "name": "Smoke Daughter", "phone": "+919000000002", "relationship": "daughter"},
    )
    ok = r.status_code == 200
    step("POST /family-contacts (add)", ok, f"HTTP {r.status_code}")
    if ok:
        r = client.get(f"/family-contacts?clerk_id={SMOKE_UID}", headers=h)
        ok = r.status_code == 200 and len(r.json()) >= 1
        contact_id = r.json()[0].get("id", "") if ok and r.json() else ""
        step("GET /family-contacts (list)", ok, f"HTTP {r.status_code}, count={len(r.json()) if r.status_code == 200 else '?'}")
        if contact_id:
            r = client.delete(f"/family-contacts/{contact_id}", headers=h)
            step("DELETE /family-contacts (own contact)", r.status_code == 200, f"HTTP {r.status_code}")

    # -- Patients -----------------------------------------------------------
    r = client.get(f"/patients?clerk_id={SMOKE_UID}", headers=h)
    ok = r.status_code == 200 and len(r.json()) >= 1
    step("GET /patients (elder list)", ok, f"HTTP {r.status_code}, count={len(r.json()) if r.status_code == 200 else '?'}")

    # -- Simulated check-in call → assessment ------------------------------
    r = client.post(
        "/assess",
        headers=h,
        json={
            "intent": "health_issue",
            "symptoms": ["fever"],
            "severity": "medium",
            "confidence": 0.9,
            "user_id": user_id,
        },
    )
    ok = r.status_code == 200
    step("POST /assess (simulated call)", ok, f"HTTP {r.status_code}")
    if ok:
        r = client.get(f"/assessments?clerk_id={SMOKE_UID}&limit=5", headers=h)
        ok = r.status_code == 200 and len(r.json()) >= 1
        step("GET /assessments (dashboard feed)", ok, f"HTTP {r.status_code}, count={len(r.json()) if r.status_code == 200 else '?'}")

        r = client.get(f"/assessments/stats?clerk_id={SMOKE_UID}", headers=h)
        step("GET /assessments/stats", r.status_code == 200, f"HTTP {r.status_code}")

        r = client.get(f"/timeline?phone={ELDER_PHONE}&clerk_id={SMOKE_UID}", headers=h)
        ok = r.status_code == 200 and (r.json() or {}).get("total", 0) >= 1
        step("GET /timeline (call history)", ok, f"HTTP {r.status_code}, total={(r.json() or {}).get('total') if r.status_code == 200 else '?'}")

    # -- Cleanup + data isolation ------------------------------------------
    if reminder_id:
        r = client.delete(f"/reminders/{reminder_id}", headers=h)
        step("DELETE /reminders (own reminder)", r.status_code == 200, f"HTTP {r.status_code}")

    r = client.get("/assessments", headers=h)  # no clerk_id / no token
    step("Data isolation: /assessments without uid → empty", r.status_code == 200 and r.json() == [], f"HTTP {r.status_code}, len={len(r.json()) if r.status_code == 200 else '?'}")


def probe_live(base_url: str, api_key: str) -> int:
    """
    Probe the DEPLOYED backend's auth fail-closed contract (no Clerk token).

    Verifies the exact state that matters for login security: whether a
    request with only the static API key can read dashboard data (insecure)
    or is rejected with 401 (secure). Re-run after every redeploy.
    """
    import httpx

    print(f"Probing live backend: {base_url}")

    def _get(path: str, **kw) -> httpx.Response:
        return httpx.get(f"{base_url}{path}", timeout=15, **kw)

    r = _get("/health")
    step("live: GET /health", r.status_code == 200, f"HTTP {r.status_code}")

    r = _get("/health/auth")
    data = {}
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            data = r.json()
        except Exception:
            data = {}
    secure = data.get("secure")
    if r.status_code == 503:
        step("live: /health/auth flags insecure deploy (503)", True, f"HTTP {r.status_code} mode={data.get('mode')} clerk={data.get('clerk_secret_key')}")
    elif r.status_code == 200 and secure is True:
        step("live: /health/auth reports secure", True, f"HTTP {r.status_code} mode={data.get('mode')} clerk={data.get('clerk_secret_key')}")
    else:
        step("live: /health/auth reports insecure", r.status_code == 503, f"HTTP {r.status_code} {data}")

    h = {"X-API-Key": api_key}
    r = _get("/assessments?limit=1", headers=h)
    ok = r.status_code == 401
    step(
        "live: /assessments without token → 401 (fail-closed)",
        ok,
        f"HTTP {r.status_code} — {'SECURE' if ok else '⚠️ INSECURE: dashboard data readable with just the API key'}",
    )

    r = _get("/config-check", headers=h)
    step("live: /config-check without token → 401", r.status_code == 401, f"HTTP {r.status_code}")

    r = _get("/timeline?phone=%2B919000000000", headers=h)
    step("live: /timeline without token → 401", r.status_code == 401, f"HTTP {r.status_code}")

    r = httpx.post(f"{base_url}/notify", json={"phone": "+919000000000"}, headers=h, timeout=15)
    step("live: POST /notify without token → 401", r.status_code == 401, f"HTTP {r.status_code}")

    print()
    if _failures:
        print(f"❌ LIVE PROBE FAILED — {len(_failures)} check(s) failed. The backend is NOT fail-closed; "
              "set CLERK_SECRET_KEY (+ ENV=production) and redeploy, then re-run this probe.")
        for f in _failures:
            print(f"   - {f}")
        return 1
    print("✅ LIVE PROBE PASSED — the deployed backend is fail-closed (secure).")
    return 0


def _env_key_from_dotenv(path: str = ".env") -> str:
    """Read WELLRING_API_KEY from a .env file (for --live without --api-key)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("WELLRING_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end login-flow smoke test for the WellRing backend.")
    parser.add_argument("--token", default="", help="Real Clerk session JWT (required when CLERK_SECRET_KEY is set).")
    parser.add_argument("--live", metavar="BASE_URL", default="",
                        help="Probe a DEPLOYED backend (e.g. https://wellring-backend-production.up.railway.app) "
                             "for the auth fail-closed contract instead of running in-process.")
    parser.add_argument("--api-key", default="", help="API key for --live mode (defaults to WELLRING_API_KEY in .env).")
    args = parser.parse_args()

    if args.live:
        live_key = args.api_key or _env_key_from_dotenv()
        if not live_key:
            print("ERROR: --live needs an API key. Pass --api-key or set WELLRING_API_KEY in .env.")
            return 2
        return probe_live(args.live, live_key)

    secret_configured = bool(os.environ.get("CLERK_SECRET_KEY"))
    mode = "production" if secret_configured else "development"
    print(f"Mode: {mode}  (CLERK_SECRET_KEY {'set' if secret_configured else 'NOT set'})")
    if mode == "production" and not args.token:
        print("ERROR: CLERK_SECRET_KEY is set but no --token was provided. Get a token from")
        print("  the browser console:  await window.Clerk.session.getToken()")
        return 2

    with TestClient(app) as client:
        if mode == "production":
            # Fail-closed contract: without a token every dashboard call is 401.
            r = client.get("/assessments", headers=_headers())
            step("Prod fail-closed: /assessments without token → 401", r.status_code == 401, f"HTTP {r.status_code}")
            r = client.get(f"/timeline?phone={ELDER_PHONE}", headers=_headers())
            step("Prod fail-closed: /timeline without token → 401", r.status_code == 401, f"HTTP {r.status_code}")
            r = client.delete("/reminders/1", headers=_headers())
            step("Prod fail-closed: DELETE /reminders without token → 401", r.status_code == 401, f"HTTP {r.status_code}")
            r = client.get("/setup-profile", headers=_headers())
            step("Prod fail-closed: GET /setup-profile without token → 401", r.status_code == 401, f"HTTP {r.status_code}")

        run_flow(client, token=args.token)

    print()
    if _failures:
        print(f"❌ SMOKE TEST FAILED — {len(_failures)} failed step(s):")
        for f in _failures:
            print(f"   - {f}")
        return 1

    print("✅ SMOKE TEST PASSED — full login → onboarding → dashboard flow works.")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(code)
