"""
test_clerk_auth.py
==================
Clerk session verification — the "secure backend auth" contract.

Behavior:
- CLERK_SECRET_KEY unset  → dev mode: no verification; the legacy `clerk_id`
  request field (query param / body / form) drives scoping as before.
- CLERK_SECRET_KEY set    → production mode: a valid Bearer session JWT is
  REQUIRED on every dashboard/outbound endpoint. Missing or invalid tokens are
  rejected with 401 (fail closed), and the verified user id (the JWT `sub`)
  OVERRIDES any `clerk_id` the client sends — spoofing another account's id
  is impossible because the caller's identity comes from the verified token.

The SDK's network fetch of JWKS keys is monkeypatched so these tests never
touch the network or real Clerk.
"""

import os

import pytest
from unittest.mock import AsyncMock

from clerk_backend_api.security import types as cst


def _headers():
    return {"X-API-Key": os.environ.get("WELLRING_API_KEY", "")}


def _signed_out_state():
    return cst.RequestState(
        status=cst.AuthStatus.SIGNED_OUT,
        reason=cst.AuthErrorReason.SESSION_TOKEN_MISSING,
    )


def _signed_in_state(sub="user_verified_123"):
    return cst.RequestState(
        status=cst.AuthStatus.SIGNED_IN,
        payload={"sub": sub},
        token="fake.jwt.token",
    )


@pytest.fixture()
def prod_mode(monkeypatch):
    """Simulate production: CLERK_SECRET_KEY set + SDK verification mocked."""
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fake")
    import clerk_backend_api.security.authenticaterequest as ar
    return ar


# ---------------------------------------------------------------------------
# Dev mode (no CLERK_SECRET_KEY): legacy clerk_id param drives everything
# ---------------------------------------------------------------------------

def test_dev_mode_uses_clerk_id_param(client, monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    r = client.get("/assessments?clerk_id=user_a")
    assert r.status_code == 200
    # Unknown user → empty list, never every user's data
    assert r.json() == []


def test_dev_mode_empty_without_clerk_id(client, monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    r = client.get("/assessments")
    assert r.status_code == 200
    assert r.json() == []


def test_dev_mode_reminders_post_with_clerk_id(client, monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    # No elder profile for this uid → 404 (same contract as before)
    r = client.post("/reminders", json={
        "type": "medicine",
        "title": "Amlodipine",
        "time": "09:00",
        "frequency": "daily",
        "phone": "+919876543210",
        "clerk_id": "user_a",
    })
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Production mode (CLERK_SECRET_KEY set): token required, uid overrides param
# ---------------------------------------------------------------------------

def test_prod_mode_rejects_missing_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get("/assessments?clerk_id=user_a", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_rejects_invalid_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get(
        "/assessments?clerk_id=user_a",
        headers={**_headers(), "Authorization": "Bearer garbage"},
    )
    assert r.status_code == 401


def test_prod_mode_accepts_valid_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_in_state())
    r = client.get(
        "/assessments?clerk_id=user_a",
        headers={**_headers(), "Authorization": "Bearer valid.jwt.token"},
    )
    assert r.status_code == 200


def test_prod_mode_verified_uid_overrides_clerk_id_param(client, prod_mode):
    """A spoofed clerk_id query param cannot read another account's data."""
    prod_mode.authenticate_request_async = AsyncMock(
        return_value=_signed_in_state(sub="user_real")
    )
    r = client.get(
        "/assessments?clerk_id=user_attacker",
        headers={**_headers(), "Authorization": "Bearer valid.jwt.token"},
    )
    assert r.status_code == 200
    # user_real has no assessments → empty; the attacker's id was NOT used
    assert r.json() == []


def test_prod_mode_setup_profile_uses_verified_uid(client, prod_mode):
    """POST /setup-profile stores the profile under the verified uid, not the
    clerk_id the client claims in the body."""
    prod_mode.authenticate_request_async = AsyncMock(
        return_value=_signed_in_state(sub="user_real")
    )
    r = client.post(
        "/setup-profile",
        headers={**_headers(), "Authorization": "Bearer valid.jwt.token"},
        json={
            "clerk_id": "user_attacker",
            "elder_name": "Test Elder",
            "elder_phone": "+919999999999",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "success"

    # The profile was stored under the verified uid (user_real), so the owner
    # can read it back with NO clerk_id param at all.
    r2 = client.get(
        "/setup-profile",
        headers={**_headers(), "Authorization": "Bearer valid.jwt.token"},
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "Test Elder"

    # A DIFFERENT caller whose token verifies to user_attacker cannot read the
    # profile even when claiming clerk_id=user_attacker (no such profile exists
    # there) — the profile lives under user_real, the id in the original JWT.
    prod_mode.authenticate_request_async = AsyncMock(
        return_value=_signed_in_state(sub="user_attacker")
    )
    r3 = client.get(
        "/setup-profile?clerk_id=user_attacker",
        headers={**_headers(), "Authorization": "Bearer attacker.jwt.token"},
    )
    assert r3.status_code == 404


def test_prod_mode_timeline_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get("/timeline?phone=%2B919876543210", headers=_headers())
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Production mode: remaining endpoints also fail closed without a token
# ---------------------------------------------------------------------------

def _seed_elder_with_contact(db_path, uid, phone="+919000000000"):
    """Seed an elder profile + one family-contact row; return (elder_id, contact_id)."""
    import sqlite3
    import uuid
    elder_id = str(uuid.uuid4())
    contact_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, clerk_id, name, phone, role, is_system) "
        "VALUES (?, ?, ?, ?, 'elderly', 0)",
        (elder_id, uid, f"Elder {uid}", phone),
    )
    conn.execute(
        "INSERT OR REPLACE INTO users "
        "(user_id, name, phone, role, caregiver_for_user_id, relationship) "
        "VALUES (?, ?, ?, 'caregiver', ?, 'son')",
        (contact_id, "Son", "+919000000001", elder_id),
    )
    conn.commit()
    conn.close()
    return elder_id, contact_id


def _seed_reminder_for_elder(elder_user_id: str, phone: str = "+919000000000"):
    from src.database import add_reminder
    return add_reminder(
        type_val="medicine",
        title="Amlodipine",
        time_val="09:00",
        frequency="daily",
        phone=phone,
        notes="Take with breakfast",
        user_id=elder_user_id,
    )


def test_prod_mode_delete_family_contact_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.delete("/family-contacts/abc-123", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_delete_family_contact_ownership(client, prod_mode):
    """A verified user can only delete their own elder's contacts."""
    db_path = os.environ["WELLRING_DB_PATH"]
    elder_a, contact_a = _seed_elder_with_contact(db_path, "user_family_a")
    _, contact_b = _seed_elder_with_contact(db_path, "user_family_b", phone="+919000000002")

    prod_mode.authenticate_request_async = AsyncMock(
        return_value=_signed_in_state(sub="user_family_a")
    )
    headers = {**_headers(), "Authorization": "Bearer valid.jwt.token"}

    from src.database import get_family_contacts
    # Deleting another account's contact → 404, and the row survives.
    r = client.delete(f"/family-contacts/{contact_b}", headers=headers)
    assert r.status_code == 404
    assert len(get_family_contacts(elder_a)) == 1

    # Deleting own contact → success.
    r = client.delete(f"/family-contacts/{contact_a}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert len(get_family_contacts(elder_a)) == 0


def test_prod_mode_reminder_delete_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.delete("/reminders/1", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_reminder_delete_ownership(client, prod_mode):
    """A verified user cannot delete another account's reminder."""
    db_path = os.environ["WELLRING_DB_PATH"]
    _, _ = _seed_elder_with_contact(db_path, "user_rem_a", phone="+919000000010")
    elder_b, _ = _seed_elder_with_contact(db_path, "user_rem_b", phone="+919000000011")
    rem_b = _seed_reminder_for_elder(elder_b, phone="+919000000011")

    prod_mode.authenticate_request_async = AsyncMock(
        return_value=_signed_in_state(sub="user_rem_a")
    )
    headers = {**_headers(), "Authorization": "Bearer valid.jwt.token"}

    r = client.delete(f"/reminders/{rem_b}", headers=headers)
    assert r.status_code == 404

    # B's reminder still exists for its owner.
    from src.database import get_reminders
    assert any(str(rr["id"]) == str(rem_b) for rr in get_reminders(clerk_id="user_rem_b"))


def test_prod_mode_watchdog_logs_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get("/watchdog/logs", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_config_check_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get("/config-check", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_outbound_call_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.post("/call", json={"phone": "+919876543210"}, headers=_headers())
    assert r.status_code == 401


def test_prod_mode_recording_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get("/recordings/assess-1", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_upload_document_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.post("/upload-document", headers=_headers())
    assert r.status_code == 401


def test_prod_mode_notify_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.post("/notify", json={"phone": "+919876543210"}, headers=_headers())
    assert r.status_code == 401


def test_prod_mode_test_whatsapp_requires_token(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.post("/test-whatsapp", json={"to_phone": "+919876543210"}, headers=_headers())
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Production startup guard
# ---------------------------------------------------------------------------

def test_startup_guard_refuses_production_without_secret(monkeypatch):
    """Production-like env + no CLERK_SECRET_KEY → fail fast."""
    import src.main as main
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_DEV_AUTH", raising=False)
    for env in ("production", "prod", "preview", "staging"):
        with pytest.raises(RuntimeError, match="CLERK_SECRET_KEY"):
            main._clerk_auth_startup_guard(env)


def test_startup_guard_allows_development_without_secret(monkeypatch):
    """Dev env (or unset) without CLERK_SECRET_KEY → boots (legacy behavior)."""
    import src.main as main
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    for env in ("", "development", "dev", "local", "test"):
        main._clerk_auth_startup_guard(env)  # must not raise


def test_startup_guard_allows_production_with_secret(monkeypatch):
    """Production env WITH CLERK_SECRET_KEY → boots."""
    import src.main as main
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fake")
    main._clerk_auth_startup_guard("production")  # must not raise


def test_startup_guard_override_waives_fail_fast(monkeypatch):
    """ALLOW_INSECURE_DEV_AUTH=true overrides the guard (emergency only)."""
    import src.main as main
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_DEV_AUTH", "true")
    main._clerk_auth_startup_guard("production")  # must not raise


def test_prod_mode_authorized_parties_passed_to_sdk(client, prod_mode, monkeypatch):
    """CLERK_AUTHORIZED_PARTIES is parsed and handed to the SDK options."""
    monkeypatch.setenv(
        "CLERK_AUTHORIZED_PARTIES",
        "https://wellring-frontend.vercel.app, http://localhost:5173",
    )
    captured = {}

    async def fake_authenticate(request, options):
        captured["options"] = options
        return _signed_in_state()

    prod_mode.authenticate_request_async = fake_authenticate
    r = client.get(
        "/assessments",
        headers={**_headers(), "Authorization": "Bearer valid.jwt.token"},
    )
    assert r.status_code == 200
    assert captured["options"].authorized_parties == [
        "https://wellring-frontend.vercel.app",
        "http://localhost:5173",
    ]
