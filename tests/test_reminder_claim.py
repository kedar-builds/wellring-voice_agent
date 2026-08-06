"""
test_reminder_claim.py
======================
Verifies the atomic scheduler claim: when two scheduler replicas try to fire
the SAME reminder at the same time, exactly ONE wins the claim. This is the
regression test for the "many calls at the same time" bug where every replica
saw last_triggered IS NULL and placed N simultaneous calls.
"""
import os
import sqlite3

from src.database import (
    add_reminder,
    claim_reminder_trigger,
    get_reminders,
    release_reminder_trigger,
    update_reminder_trigger,
)


def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "reminder_claim.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            time TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'once',
            phone TEXT,
            notes TEXT,
            last_triggered TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_claim_wins_exactly_once(tmp_path):
    """Two replicas claiming the same reminder → only one succeeds."""
    db_path = _make_db(tmp_path)
    add_reminder(
        "call", "Test Call", "2026-08-06T10:00",
        "once", "+918421971145", "", db_path=db_path,
    )
    rid = get_reminders(db_path=db_path)[0]["id"]

    # Both replicas compute should_trigger=True and try to claim simultaneously.
    replica_a_won = claim_reminder_trigger(rid, "2026-08-06T10:00:01", db_path=db_path)
    replica_b_won = claim_reminder_trigger(rid, "2026-08-06T10:00:01", db_path=db_path)

    assert replica_a_won is True, "first claim should win"
    assert replica_b_won is False, "second claim must be rejected (no duplicate call)"

    # The winner's claim is persisted as last_triggered.
    reminders = get_reminders(db_path=db_path)
    assert reminders[0]["last_triggered"] == "2026-08-06T10:00:01"


def test_release_allows_retry(tmp_path):
    """After a failed call releases the claim, the next cycle can fire again."""
    db_path = _make_db(tmp_path)
    add_reminder(
        "call", "Retry Call", "2026-08-06T10:00",
        "once", "+918421971145", "", db_path=db_path,
    )
    rid = get_reminders(db_path=db_path)[0]["id"]
    ts = "2026-08-06T10:00:05"

    assert claim_reminder_trigger(rid, ts, db_path=db_path) is True
    # Call fails → release the claim so the next cycle can retry.
    assert release_reminder_trigger(rid, ts, db_path=db_path) is True

    reminders = get_reminders(db_path=db_path)
    assert reminders[0]["last_triggered"] is None

    # Next cycle can claim and fire again.
    assert claim_reminder_trigger(rid, ts, db_path=db_path) is True


def test_update_trigger_still_works(tmp_path):
    """The legacy unconditional marker still works (used by other callers)."""
    db_path = _make_db(tmp_path)
    add_reminder(
        "medicine", "Pill", "10:00",
        "daily", "+918421971145", "", db_path=db_path,
    )
    rid = get_reminders(db_path=db_path)[0]["id"]
    assert update_reminder_trigger(rid, "2026-08-06", db_path=db_path) is True
    assert get_reminders(db_path=db_path)[0]["last_triggered"] == "2026-08-06"
