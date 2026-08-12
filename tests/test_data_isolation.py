"""
test_data_isolation.py
======================
Regression tests for the remaining cross-account data-leak surfaces beyond
the dashboard feed/stats (covered in test_assessments_dashboard.py):

1. /timeline        — phone lookups must not leak another account's history,
                      and an unknown phone must return [] (never "all data").
2. /reminders       — reminders are owned by the caregiver's elder; another
                      account must never see them, even when phones collide.
3. /recordings/{id} — recordings must be scoped to the requesting user's elder
                      when clerk_id is supplied.
"""

import os
import sqlite3
from unittest.mock import patch, MagicMock

from src.database import log_interaction

UID_ALPHA = "isolation_uid_alpha"
UID_BETA = "isolation_uid_beta"
ELDER_ALPHA_ID = "isolation_elder_alpha"
ELDER_BETA_ID = "isolation_elder_beta"
PHONE_ALPHA = "+919876000001"
PHONE_BETA = "+919876000002"


def _seed_elders():
    """Insert two elders (one per account) into the SQLite test DB."""
    db_path = os.environ["WELLRING_DB_PATH"]
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, clerk_id, name, phone, role, is_system) "
        "VALUES (?, ?, ?, ?, 'elderly', 0)",
        (ELDER_ALPHA_ID, UID_ALPHA, "Elder Alpha", PHONE_ALPHA),
    )
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, clerk_id, name, phone, role, is_system) "
        "VALUES (?, ?, ?, ?, 'elderly', 0)",
        (ELDER_BETA_ID, UID_BETA, "Elder Beta", PHONE_BETA),
    )
    conn.commit()
    conn.close()


def _log_for(user_id: str, risk: str = "LOW"):
    log_interaction({
        "risk_level": risk,
        "symptoms": ["fatigue"],
        "confidence": 1.0,
        "severity": "low",
        "score": 10,
        "action": "monitor",
        "message": "Keep monitoring",
        "steps": ["Step 1"],
        "breakdown": ["Base score = 10"],
        "user_id": user_id,
    })


# ---------------------------------------------------------------------------
# /timeline isolation
# ---------------------------------------------------------------------------

def test_timeline_scopes_to_owning_clerk_id(client):
    _seed_elders()
    _log_for(ELDER_ALPHA_ID)
    _log_for(ELDER_BETA_ID)

    # Owner uid → sees the elder's own history.
    r = client.get(f"/timeline?phone={PHONE_ALPHA}&clerk_id={UID_ALPHA}")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["entries"][0]["elder_name"] == "Elder Alpha"

    # Another account's uid → must NOT see alpha's history by guessing the phone.
    r = client.get(f"/timeline?phone={PHONE_ALPHA}&clerk_id={UID_BETA}")
    assert r.status_code == 200
    assert r.json()["total"] == 0

    # Legacy behaviour without clerk_id still works (phone-scoped).
    r = client.get(f"/timeline?phone={PHONE_ALPHA}")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_timeline_unknown_phone_returns_empty(client):
    """An unmatched phone must return [] — never every account's interactions."""
    _seed_elders()
    _log_for(ELDER_ALPHA_ID)
    _log_for(ELDER_BETA_ID)

    r = client.get("/timeline?phone=%2B999999999999")
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert r.json()["entries"] == []


# ---------------------------------------------------------------------------
# /reminders isolation
# ---------------------------------------------------------------------------

def test_reminders_are_owner_scoped(client):
    _seed_elders()

    # Reminder created by alpha's account → owned by alpha's elder.
    r = client.post("/reminders", json={
        "type": "medicine",
        "title": "BP Pill",
        "time": "09:00",
        "frequency": "daily",
        "phone": PHONE_ALPHA,
        "notes": "With breakfast",
        "clerk_id": UID_ALPHA,
    })
    assert r.status_code == 201

    # Beta must not see alpha's reminder (different phone AND different owner).
    r = client.get(f"/reminders?clerk_id={UID_BETA}")
    assert r.status_code == 200
    assert r.json() == []

    # Alpha sees it.
    r = client.get(f"/reminders?clerk_id={UID_ALPHA}")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["title"] == "BP Pill"

    # New account with no profile → no reminders at all.
    r = client.get("/reminders?clerk_id=brand_new_account_never_onboarded")
    assert r.status_code == 200
    assert r.json() == []


def test_reminder_creation_requires_known_elder_when_uid_given(client):
    _seed_elders()
    r = client.post("/reminders", json={
        "type": "general",
        "title": "Orphan",
        "time": "10:00",
        "frequency": "once",
        "phone": PHONE_ALPHA,
        "clerk_id": "uid_with_no_profile",
    })
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /recordings isolation (Postgres path mocked)
# ---------------------------------------------------------------------------

def test_recording_ownership_enforced_with_clerk_id():
    mock_row = {"recording_url": "permanent/b2/path/audio.wav"}
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = mock_row
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn

    with patch("src.database._use_postgres", return_value=True), \
         patch("src.database._PG_AVAILABLE", return_value=True), \
         patch("src.database.get_pg_conn", return_value=mock_conn), \
         patch("src.database._pg_cursor") as mock_cursor_ctx, \
         patch("src.main.get_presigned_url", return_value="https://signed.example/url"):
        from fastapi.testclient import TestClient
        from src.main import app
        import src.database as db_module
        with patch.object(db_module, "_use_postgres", return_value=True):
            with TestClient(app) as c:
                c.headers.update({"X-API-Key": os.environ["WELLRING_API_KEY"]})
                mock_cursor_ctx.return_value.__enter__.return_value = mock_cursor

                r = c.get(f"/recordings/assess-1?clerk_id={UID_ALPHA}")
                assert r.status_code == 200
                assert r.json()["presigned_url"].startswith("https://signed.example")

                # The executed SQL must carry the ownership subquery + the uid.
                executed_sql, executed_params = mock_cursor.execute.call_args.args
                assert "user_id IN (SELECT user_id FROM users" in executed_sql
                assert UID_ALPHA in executed_params

                # Non-owner (no matching elder) → 404, not a data leak.
                mock_cursor.fetchone.return_value = None
                r = c.get(f"/recordings/assess-1?clerk_id={UID_BETA}")
                assert r.status_code == 404
