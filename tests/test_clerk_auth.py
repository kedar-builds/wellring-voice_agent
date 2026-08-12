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
