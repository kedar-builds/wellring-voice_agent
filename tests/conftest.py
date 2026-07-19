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
# Set a test-only API key so auth functions don't 500 on missing env var.
# This value is not a secret — it exists only within the test process.
os.environ.setdefault("WELLRING_API_KEY", "wellring-test-key-local")


@pytest.fixture(scope="session")
def client():
    """
    Session-scoped FastAPI TestClient.
    Uses a temporary SQLite DB — Postgres is patched out so the real
    local database is never touched during tests.
    """
    from fastapi.testclient import TestClient
    from src.main import app
    import src.database as db_module

    # Force SQLite for the entire test session regardless of DATABASE_URL.
    # We patch at the function level (lazy check) so dotenv can't override it.
    with patch.object(db_module, "_use_postgres", return_value=False):
        with TestClient(app) as c:
            c.headers.update({"X-API-Key": os.environ["WELLRING_API_KEY"]})
            yield c

    # Cleanup the temp DB after all tests finish
    if os.path.exists(_tmp_db):
        os.remove(_tmp_db)

