"""
conftest.py
===========
Shared pytest fixtures for the WellRing FastAPI test suite.

Sets WELLRING_DB_PATH to a temporary file BEFORE any src modules are
imported, so database.py picks up the test path at module-load time.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

# ── Set the test DB path at module load (before any src import) ───────────
_tmp_db = tempfile.mktemp(suffix=".db", prefix="wellring_test_")
os.environ["WELLRING_DB_PATH"] = _tmp_db
os.environ["DATABASE_URL"] = ""
# Set a test-only API key so auth functions don't 500 on missing env var.
# This value is not a secret — it exists only within the test process.
os.environ.setdefault("WELLRING_API_KEY", "wellring-test-key-local")
# Rate limiting: keep the middleware ACTIVE in tests (so its code path runs)
# but never trip it — the TestClient shares one IP, the suite makes hundreds
# of requests, and several tests deliberately provoke 401s.
# The blocking behavior itself is unit-tested in tests/test_ratelimit.py.
os.environ.setdefault("RATE_LIMIT_REQUESTS_PER_MINUTE", "100000")
os.environ.setdefault("RATE_LIMIT_FAILURES_PER_WINDOW", "100000")
# The test session must NEVER make real external calls — the local .env sets
# USE_TWILIO/USE_ROUTINE_UPDATES=true and OPENROUTER_API_KEY, which made the
# suite spend minutes on real Twilio sends + Nemotron LLM calls. test_
# notifications.py controls these flags per-test via patch(), so forcing them
# off here is safe. (Set BEFORE src.* is imported — load_dotenv never
# overrides already-set variables. Direct assignment, NOT setdefault: a
# developer shell exporting USE_TWILIO=true or OPENROUTER_API_KEY must not
# leak those values into the suite — that was a real external-call regression.)
os.environ["USE_TWILIO"] = "false"
os.environ["USE_WHATSAPP"] = "false"
os.environ["USE_ROUTINE_UPDATES"] = "false"
os.environ["OPENROUTER_API_KEY"] = ""


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """
    Session-scoped autouse fixture.

    Patches _use_postgres → False and calls init_db() ONCE at the start
    of the test session.  This ensures the nemotron_audits table (and all
    other tables) are present in the temp SQLite file for tests that call
    DB functions directly, without requiring the `client` fixture.
    """
    import src.database as db_module

    with patch.object(db_module, "_use_postgres", return_value=False):
        db_module.init_db()
        yield

    # Cleanup the temp DB after all tests finish
    if os.path.exists(_tmp_db):
        os.remove(_tmp_db)


@pytest.fixture(scope="session")
def client(_init_test_db):
    """
    Session-scoped FastAPI TestClient.
    Uses a temporary SQLite DB — Postgres is patched out so the real
    local database is never touched during tests.

    NOTE: Twilio is NOT mocked here at the session level to avoid
    interfering with test_notifications.py unit tests.  The webhook
    integration tests that trigger notifications patch _twilio_send
    locally via the `mock_twilio_send` fixture.
    """
    from fastapi.testclient import TestClient
    from src.main import app
    import src.database as db_module

    # Force SQLite for the entire test session regardless of DATABASE_URL.
    with patch.object(db_module, "_use_postgres", return_value=False):
        with TestClient(app) as c:
            c.headers.update({"X-API-Key": os.environ["WELLRING_API_KEY"]})
            yield c


@pytest.fixture()
def mock_twilio_send():
    """
    Function-scoped fixture: patches src.notifications._twilio_send to a
    no-op so individual webhook integration tests never make real Twilio
    API calls (avoids HTTP 429 rate-limit errors in CI).
    """
    with patch("src.notifications._twilio_send", return_value=(True, None)):
        yield

