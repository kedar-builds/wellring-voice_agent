"""
test_auth_health.py
===================
Tests for the auth-health watchdog (src/auth_health.py) and the
GET /health/auth status endpoint.
"""

import os
import time
import uuid
from unittest.mock import AsyncMock

import pytest
from clerk_backend_api.security import types as cst

import src.auth_health as ah


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


def _reset():
    with ah._lock:
        ah._rejections.clear()


# ---------------------------------------------------------------------------
# Rejection counters
# ---------------------------------------------------------------------------

def test_record_and_count_rejections():
    _reset()
    ah.record_clerk_rejection()
    ah.record_clerk_rejection()
    assert ah.rejection_count() == 2


def test_rejection_count_prunes_old_entries():
    _reset()
    ah.record_clerk_rejection()
    # The deque is oldest-on-the-left; an ancient entry must be at the left to
    # be pruned (as it would be in real usage, where timestamps are append-only).
    with ah._lock:
        ah._rejections.appendleft(time.time() - 1000)
    assert ah.rejection_count() == 1


# ---------------------------------------------------------------------------
# Alert conditions
# ---------------------------------------------------------------------------

def test_current_alerts_missing_secret(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    _reset()
    assert "missing-secret" in ah.current_alerts()


def test_current_alerts_rejection_spike(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    _reset()
    with ah._lock:
        now = time.time()
        for _ in range(ah.REJECTION_SPIKE_THRESHOLD):
            ah._rejections.append(now)
    assert "rejection-spike" in ah.current_alerts()


def test_no_alerts_when_healthy(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    _reset()
    assert ah.current_alerts() == []


def test_production_like_env_detection(monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    for env, expected in (("production", True), ("preview", True), ("staging", True), ("development", False), ("", False)):
        monkeypatch.setenv("ENV", env)
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
        monkeypatch.delenv("VERCEL_ENV", raising=False)
        assert ah.production_like_env() is expected, f"ENV={env!r}"


# ---------------------------------------------------------------------------
# GET /health/auth endpoint (via the real app)
# ---------------------------------------------------------------------------

def test_health_auth_development_mode(client, monkeypatch):
    # Be explicit about the environment so a dev shell with CLERK_SECRET_KEY
    # or a platform env var exported can't make this test flaky.
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    _reset()
    r = client.get("/health/auth")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "development"
    assert data["clerk_secret_key"] == "missing"
    assert data["secure"] is True                 # dev mode → no alerts
    assert data["alerts"] == []


def test_health_auth_production_missing_secret(client, monkeypatch):
    """Insecure (auth disabled) → 503 so a Railway healthcheck flags the deploy."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    r = client.get("/health/auth")
    assert r.status_code == 503
    data = r.json()
    assert data["mode"] == "production"
    assert data["clerk_secret_key"] == "missing"
    assert data["secure"] is False
    assert "missing-secret" in data["alerts"]


def test_health_auth_production_with_secret(client, monkeypatch):
    """Configured + prod env → 200, secure."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    _reset()
    r = client.get("/health/auth")
    assert r.status_code == 200
    assert r.json()["secure"] is True
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# auth_events persistence + /auth/events endpoint
# ---------------------------------------------------------------------------

def test_log_and_get_auth_events():
    from src.database import get_auth_events, log_auth_event
    # Unique event types per run so this test genuinely validates the write
    # path instead of picking up rows written by other tests (e.g. the rate-
    # limiter middleware tests log the same 'rate_limit_block' type).
    tag = uuid.uuid4().hex[:8]
    evt_a = f"rate_limit_block_{tag}"
    evt_b = f"missing_clerk_secret_{tag}"
    log_auth_event(evt_a, "IP 1.2.3.4 blocked", ip="1.2.3.4")
    log_auth_event(evt_b, "verification disabled")
    events = get_auth_events()
    types = [e["event_type"] for e in events]
    assert evt_a in types
    assert evt_b in types
    # newest first
    assert events[0]["created_at"]


def test_auth_events_ddl_is_per_backend():
    """
    Regression guard: the auth_events DDL must be split per backend.
    A single shared DDL silently killed the feature on Postgres —
    AUTOINCREMENT is a SQLite-only keyword and now() can't go into a TEXT
    column. Production runs on Postgres, so this bug meant the ops dashboard
    recorded nothing.
    """
    from src.database import _AUTH_EVENTS_DDL_PG, _AUTH_EVENTS_DDL_SQLITE
    # Postgres form: no SQLite-isms, timestamp column is TIMESTAMPTZ.
    assert "AUTOINCREMENT" not in _AUTH_EVENTS_DDL_PG
    assert "BIGSERIAL" in _AUTH_EVENTS_DDL_PG
    assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in _AUTH_EVENTS_DDL_PG
    # SQLite form: AUTOINCREMENT + TEXT timestamp.
    assert "AUTOINCREMENT" in _AUTH_EVENTS_DDL_SQLITE
    assert "TEXT NOT NULL" in _AUTH_EVENTS_DDL_SQLITE


def test_auth_events_requires_token_in_prod(client, prod_mode):
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_out_state())
    r = client.get("/auth/events", headers=_headers())
    assert r.status_code == 401


def test_auth_events_readable_with_token(client, prod_mode):
    from src.database import log_auth_event
    log_auth_event("rate_limit_block", "IP 9.9.9.9 blocked for 900s", ip="9.9.9.9")
    prod_mode.authenticate_request_async = AsyncMock(return_value=_signed_in_state())
    r = client.get(
        "/auth/events",
        headers={**_headers(), "Authorization": "Bearer valid.jwt.token"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    assert any(e["event_type"] == "rate_limit_block" for e in data["events"])
