"""
test_webhook_quality.py
=======================
Regression tests for the dashboard "latest conversation" quality fixes:

1. `dedupe_transcript` — collapses repeated / truncated lines in the transcript
   (the "garbled transcript" seen on the dashboard).
2. `assessment_exists_for_call` — the webhook only logs each Bolna call once
   (Bolna retries on HTTP 500 → duplicate assessment rows previously flooded
   the dashboard feed/timeline).
3. Webhook integration — when the payload carries in-call Bolna extraction
   data, it is used instead of a post-hoc Gemini re-analysis (which routinely
   under-extracted: empty symptoms for a call where the patient reported fever).
"""
import json
import os
import sqlite3

from src.database import assessment_exists_for_call
from src.main import dedupe_transcript


# ---------------------------------------------------------------------------
# dedupe_transcript
# ---------------------------------------------------------------------------

def test_dedupe_collapses_exact_consecutive_repeats():
    txn = (
        "assistant: Hello!\n"
        "user: I have a fever\n"
        "assistant: Just give me a moment.\n"
        "assistant: Just give me a moment.\n"
        "assistant: Goodbye!\n"
    )
    out = dedupe_transcript(txn)
    assert out.count("Just give me a moment.") == 1
    # Non-consecutive lines are untouched
    assert out.count("assistant: Hello!") == 1
    assert out.count("Goodbye!") == 1


def test_dedupe_keeps_non_consecutive_repeats():
    txn = (
        "assistant: Take your medicines.\n"
        "user: ok\n"
        "assistant: Take your medicines.\n"
    )
    assert dedupe_transcript(txn).count("Take your medicines.") == 2


def test_dedupe_drops_truncated_repeat():
    txn = (
        "assistant: I will notify your family immediately. Till then take your medicines.\n"
        "assistant: I will notify your family immediately. Til\n"
    )
    out = dedupe_transcript(txn)
    assert out.count("I will notify your family") == 1
    assert "Til" not in out.splitlines()[-1] or len(out.splitlines()) == 1


def test_dedupe_handles_empty():
    assert dedupe_transcript("") == ""
    assert dedupe_transcript(None) is None


# ---------------------------------------------------------------------------
# assessment_exists_for_call
# ---------------------------------------------------------------------------

def test_assessment_exists_for_call_sqlite(tmp_path):
    db_path = os.path.join(str(tmp_path), "ax.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, intent TEXT, symptoms TEXT, severity TEXT, confidence REAL,
            score INTEGER, risk_level TEXT, category TEXT, action TEXT, message TEXT,
            user_id TEXT, recording_url TEXT, transcript TEXT, emotion_analysis TEXT,
            bolna_call_id TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO interactions (timestamp, bolna_call_id) VALUES ('2026-08-07', 'call-abc')"
    )
    conn.commit()
    conn.close()

    assert assessment_exists_for_call("call-abc", db_path=db_path) is True
    assert assessment_exists_for_call("call-xyz", db_path=db_path) is False
    assert assessment_exists_for_call("", db_path=db_path) is False


def test_assessment_exists_for_call_missing_column(tmp_path):
    """SQLite tables without bolna_call_id must fail open (return False)."""
    db_path = os.path.join(str(tmp_path), "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, intent TEXT, symptoms TEXT, severity TEXT, confidence REAL,
            score INTEGER, risk_level TEXT, category TEXT, action TEXT, message TEXT,
            user_id TEXT, recording_url TEXT, transcript TEXT, emotion_analysis TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    assert assessment_exists_for_call("call-abc", db_path=db_path) is False


# ---------------------------------------------------------------------------
# Webhook integration: prefers Bolna extraction + dedupes the transcript
# ---------------------------------------------------------------------------

def _webhook_payload(call_id="call-integration-1", **overrides):
    payload = {
        "status": "completed",
        "recipient_phone_number": "+919004261186",
        "call_id": call_id,
        "metadata": {"user_id": "webhook-test-user"},
        "extraction_data": {"symptoms": ["fever"], "severity": "low", "intent": "health_check"},
        "transcript": (
            "assistant: Hello, how are you feeling today?\n"
            "user: I have received the fever\n"
            "assistant: Just give me a moment, I'll be back with you.\n"
            "assistant: Just give me a moment, I'll be back with you.\n"
        ),
    }
    payload.update(overrides)
    return payload


def _latest_interaction_for_call(call_id: str):
    db_path = os.environ["WELLRING_DB_PATH"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Data is written to the 'assessments' table by _log_interaction_sqlite.
    row = conn.execute(
        "SELECT * FROM assessments WHERE bolna_call_id = ? ORDER BY id DESC LIMIT 1",
        (call_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def test_webhook_uses_bolna_extraction_and_dedupes_transcript(client, mock_twilio_send):
    """The stored assessment must carry the in-call Bolna symptoms (not empty)
    and a deduped transcript."""
    r = client.post("/bolna-webhook", json=_webhook_payload())
    assert r.status_code == 200

    row = _latest_interaction_for_call("call-integration-1")
    assert row is not None, "assessment should have been logged"

    symptoms = json.loads(row["symptoms"])
    assert "fever" in symptoms
    assert row["severity"] == "low"
    # Transcript is deduped: the repeated line appears once.
    assert row["transcript"].count("Just give me a moment") == 1
    assert row["bolna_call_id"] == "call-integration-1"


def test_webhook_second_delivery_is_idempotent(client, mock_twilio_send):
    """A retried webhook delivery must NOT create a second assessment row."""
    call_id = "call-integration-2"  # distinct from the extraction test's call
    payload = _webhook_payload(call_id=call_id)
    assert client.post("/bolna-webhook", json=payload).status_code == 200
    assert client.post("/bolna-webhook", json=payload).status_code == 200

    db_path = os.environ["WELLRING_DB_PATH"]
    conn = sqlite3.connect(db_path)
    # Data is written to the 'assessments' table by _log_interaction_sqlite.
    n = conn.execute(
        "SELECT COUNT(*) FROM assessments WHERE bolna_call_id = ?",
        (call_id,),
    ).fetchone()[0]
    conn.close()
    assert n == 1, f"expected exactly 1 assessment for the call, got {n}"
