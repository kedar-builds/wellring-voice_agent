"""
test_phone_normalization.py
===========================
Regression tests for consistent phone normalization across the call path and
DB lookups.

Problem fixed: `_do_bolna_call` normalises caller numbers to "+91..." but the
profile/history lookups used exact matches, so a phone stored as "9004261186",
"919004261186" or "+91 90042 61186" silently missed → no health context
injected into the prompt, no family contacts, assessment logged anonymous.

These tests verify:
1. normalize_phone() canonicalises every stored variant.
2. phone_match_candidates() includes the digits-only form.
3. get_user_by_phone() finds a user stored with an unformatted number when
   queried with the normalized call phone (and vice-versa).
"""
import os
import sqlite3

from src.database import normalize_phone, phone_match_candidates, get_user_by_phone


def _make_db(tmp_path: str) -> str:
    db_path = os.path.join(tmp_path, "phones.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            user_id     TEXT PRIMARY KEY,
            name        TEXT,
            phone       TEXT,
            role        TEXT,
            firebase_uid TEXT,
            created_at  TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _seed_user(db_path: str, user_id: str, name: str, phone: str, firebase_uid=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO users (user_id, name, phone, role, firebase_uid, created_at) "
        "VALUES (?, ?, ?, 'elderly', ?, '2026-01-01')",
        (user_id, name, phone, firebase_uid),
    )
    conn.commit()
    conn.close()


def test_normalize_phone_variants():
    assert normalize_phone("9004261186") == "+919004261186"
    assert normalize_phone("919004261186") == "+919004261186"
    assert normalize_phone("+91 90042 61186") == "+919004261186"
    assert normalize_phone("00919004261186") == "+919004261186"
    assert normalize_phone("+14155551234") == "+14155551234"
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""


def test_phone_match_candidates_cover_all_stored_forms():
    cands = phone_match_candidates("+919004261186")
    assert "919004261186" in cands   # digits-only form
    assert "+919004261186" in cands  # canonical form
    assert len(cands) == len(set(cands))  # de-duplicated


def test_get_user_by_phone_matches_unformatted_stored_phone(tmp_path):
    """Phone stored as bare digits must be found via the normalized call phone."""
    db_path = _make_db(str(tmp_path))
    _seed_user(db_path, "u1", "Sharma", "919004261186")  # stored without '+'

    user = get_user_by_phone("+919004261186", db_path=db_path)  # normalized call phone
    assert user is not None
    assert user["name"] == "Sharma"

    user2 = get_user_by_phone("90042 61186", db_path=db_path)
    assert user2 is not None
    assert user2["user_id"] == "u1"


def test_get_user_by_phone_matches_spaced_stored_phone(tmp_path):
    """Phone stored with spaces must match the digits-only caller phone."""
    db_path = _make_db(str(tmp_path))
    _seed_user(db_path, "u2", "Krishnan", "+91 90042 61186")

    user = get_user_by_phone("9004261186", db_path=db_path)
    assert user is not None
    assert user["name"] == "Krishnan"


def test_get_user_by_phone_returns_none_for_unknown(tmp_path):
    db_path = _make_db(str(tmp_path))
    _seed_user(db_path, "u3", "Rao", "+919004261186")

    assert get_user_by_phone("+919000000000", db_path=db_path) is None


def test_duplicate_phone_prefers_onboarded_profile(tmp_path):
    """When two users share a phone, the firebase_uid profile wins the lookup
    (the dashboard is bound to it) over a firebase_uid-less test row."""
    db_path = _make_db(str(tmp_path))
    # Test row created LAST (would win with plain created_at DESC)
    _seed_user(db_path, "test-row", "Test User", "+919004261186", firebase_uid=None)
    # Real onboarded profile created EARLIER
    _seed_user(db_path, "real-user", "Mr. Sharma", "+919004261186", firebase_uid="demo_sharma_001")

    user = get_user_by_phone("+919004261186", db_path=db_path)
    assert user is not None
    assert user["user_id"] == "real-user"
    assert user["name"] == "Mr. Sharma"
