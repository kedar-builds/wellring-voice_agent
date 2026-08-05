"""
test_history_sentinel_exclusion.py
====================================
Regression tests for sentinel / orphan exclusion in the symptom-history
repeat-count logic.

Problem that was fixed
----------------------
The anonymous sentinel user (is_system=1) is a shared, permanent bucket
used when no real caller can be identified.  If its historical assessment
rows were included in `get_symptom_repeat_count`, the history-based
escalation multiplier would inflate unfairly for every subsequent caller,
producing inflated risk scores with zero real history.

Orphan rows (interactions with NULL user_id) must also be excluded because
they cannot be attributed to any real patient.

What these tests verify
-----------------------
1. Sentinel rows are NOT counted by `_symptom_count_sqlite`.
2. Orphan rows  (NULL user_id) are NOT counted.
3. Real-user rows ARE counted when they exist.
4. Scoping to a specific user_id only returns that user's rows.
"""

import datetime
import os
import sqlite3

import pytest

from src.database import _symptom_count_sqlite


# ---------------------------------------------------------------------------
# Helper: build a minimal SQLite DB with users + interactions tables
# ---------------------------------------------------------------------------

def _make_test_db(tmp_path_str: str) -> str:
    """
    Create a minimal SQLite DB with the subset of columns needed for the
    sentinel exclusion tests.  Returns the path to the DB file.
    """
    db_path = os.path.join(tmp_path_str, "test_sentinel.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Minimal users table — only the columns _symptom_count_sqlite JOINs on
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   TEXT PRIMARY KEY,
            is_system INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Minimal interactions table — only the columns _symptom_count_sqlite uses
    cur.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT,
            symptoms  TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def _insert_user(db_path: str, user_id: str, is_system: int = 0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, is_system) VALUES (?, ?)",
        (user_id, is_system),
    )
    conn.commit()
    conn.close()


def _insert_interaction(db_path: str, user_id, symptoms: list, days_ago: int = 0):
    """Insert a row timestamped `days_ago` days in the past."""
    ts = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    ).strftime("%Y-%m-%d %H:%M:%S")
    import json
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO interactions (user_id, symptoms, timestamp) VALUES (?, ?, ?)",
        (user_id, json.dumps(symptoms), ts),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Provide a fresh, isolated DB for each test."""
    return _make_test_db(str(tmp_path))


# ---------------------------------------------------------------------------
# Test 1 — Sentinel rows are EXCLUDED from the count
# ---------------------------------------------------------------------------

def test_sentinel_rows_not_counted(tmp_db):
    """
    Rows belonging to the anonymous sentinel user (is_system=1) must
    contribute zero to `_symptom_count_sqlite`.
    """
    SENTINEL_ID = "sentinel-uuid-001"
    _insert_user(tmp_db, SENTINEL_ID, is_system=1)

    # Plant 3 dizziness rows on the sentinel user in the last 3 days
    for _ in range(3):
        _insert_interaction(tmp_db, SENTINEL_ID, ["dizziness"], days_ago=1)

    count = _symptom_count_sqlite("dizziness", days=7, db_path=tmp_db, user_id=None)
    assert count == 0, (
        f"Expected 0 — sentinel rows must be excluded. Got {count}."
    )


# ---------------------------------------------------------------------------
# Test 2 — Orphan rows (NULL user_id) are EXCLUDED
# ---------------------------------------------------------------------------

def test_orphan_rows_not_counted(tmp_db):
    """
    Interactions with a NULL user_id (e.g. logged before any user was
    resolved) must not contribute to the repeat count.
    """
    # Insert an interaction with no user_id link
    _insert_interaction(tmp_db, user_id=None, symptoms=["dizziness"], days_ago=1)

    count = _symptom_count_sqlite("dizziness", days=7, db_path=tmp_db, user_id=None)
    assert count == 0, (
        f"Expected 0 — orphan rows must be excluded. Got {count}."
    )


# ---------------------------------------------------------------------------
# Test 3 — Real-user rows ARE counted
# ---------------------------------------------------------------------------

def test_real_user_rows_counted(tmp_db):
    """
    Interactions belonging to a real (non-sentinel) user must be reflected
    in the repeat count.
    """
    REAL_ID = "real-user-uuid-001"
    _insert_user(tmp_db, REAL_ID, is_system=0)

    # 2 dizziness events in the last 3 days
    _insert_interaction(tmp_db, REAL_ID, ["dizziness"], days_ago=1)
    _insert_interaction(tmp_db, REAL_ID, ["dizziness"], days_ago=2)

    count = _symptom_count_sqlite("dizziness", days=7, db_path=tmp_db, user_id=None)
    assert count == 2, (
        f"Expected 2 real-user dizziness rows. Got {count}."
    )


# ---------------------------------------------------------------------------
# Test 4 — user_id scope only returns THAT user's rows
# ---------------------------------------------------------------------------

def test_user_scoped_count_is_isolated(tmp_db):
    """
    When a specific user_id is passed, the count must be scoped to that
    user only and must not include rows from other users — including the
    sentinel user.
    """
    USER_A = "user-uuid-a"
    USER_B = "user-uuid-b"
    SENTINEL = "sentinel-uuid-002"

    _insert_user(tmp_db, USER_A, is_system=0)
    _insert_user(tmp_db, USER_B, is_system=0)
    _insert_user(tmp_db, SENTINEL, is_system=1)

    # User A: 1 dizziness event
    _insert_interaction(tmp_db, USER_A, ["dizziness"], days_ago=1)
    # User B: 2 dizziness events (should NOT appear in User A's scoped count)
    _insert_interaction(tmp_db, USER_B, ["dizziness"], days_ago=1)
    _insert_interaction(tmp_db, USER_B, ["dizziness"], days_ago=2)
    # Sentinel: 5 dizziness events (must also be excluded)
    for _ in range(5):
        _insert_interaction(tmp_db, SENTINEL, ["dizziness"], days_ago=1)

    count_a = _symptom_count_sqlite("dizziness", days=7, db_path=tmp_db, user_id=USER_A)
    assert count_a == 1, (
        f"Expected 1 for USER_A only. Got {count_a}."
    )
