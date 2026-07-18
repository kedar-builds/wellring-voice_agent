"""
database.py
===========
Unified data-access layer for WellRing.

Backend priority (controlled by env vars):
  1. PostgreSQL  — if DATABASE_URL is set
  2. Supabase    — if USE_SUPABASE=true and SUPABASE_URL + SUPABASE_KEY are set
  3. SQLite      — local fallback (tests, offline dev)

Public API (signatures unchanged so existing code/tests keep working):
  init_db()
  log_interaction(data)           → int (row id)
  get_symptom_repeat_count(symptom, days) → int
  log_alert(...)
  add_reminder(...)
  get_reminders()
  delete_reminder(id)
  update_reminder_trigger(id, ts)

New Postgres-first functions:
  get_pg_conn()                   → psycopg2 connection (context manager)
  log_assessment_pg(data, user_id) → UUID str
  upsert_health_history(user_id, symptom, assessment_id, severity, risk_level)
  log_conversation_turn(user_id, role, content, bolna_call_id, channel)
"""

import datetime
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Union

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend selection flags
# ---------------------------------------------------------------------------
# DATABASE_URL is read lazily (at call time) so that test fixtures that call
# os.environ.pop("DATABASE_URL") before importing src.database still work.

def _use_postgres() -> bool:
    """Return True if a Postgres DATABASE_URL is currently configured."""
    return bool(os.environ.get("DATABASE_URL", ""))

USE_SUPABASE: bool = os.environ.get("USE_SUPABASE", "false").lower() == "true"
SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY", "")

# SQLite fallback path
DB_PATH: str = os.environ.get("WELLRING_DB_PATH", "wellring.db")

# ---------------------------------------------------------------------------
# Optional imports (Postgres / Supabase)
# ---------------------------------------------------------------------------
try:
    import psycopg2
    import psycopg2.extras  # for RealDictCursor, UUID support
    psycopg2.extras.register_uuid()
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

try:
    from supabase import create_client, Client as SupabaseClient
    _SUPABASE_AVAILABLE = True
except ImportError:
    SupabaseClient = Any  # type: ignore[misc,assignment]
    _SUPABASE_AVAILABLE = False


# ===========================================================================
# PostgreSQL helpers
# ===========================================================================

@contextmanager
def get_pg_conn() -> Generator:
    """
    Context manager that yields a psycopg2 connection.
    Commits on clean exit, rolls back and re-raises on exception.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not (_PG_AVAILABLE and db_url):
        raise RuntimeError("PostgreSQL is not configured (DATABASE_URL missing or psycopg2 not installed).")
    conn = psycopg2.connect(db_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_cursor(conn):
    """Return a RealDictCursor so rows come back as dicts."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_pg_tables() -> None:
    """
    Create all required PostgreSQL tables (CREATE TABLE IF NOT EXISTS).
    Safe to call on every startup — it's idempotent.
    """
    if not (_PG_AVAILABLE and os.environ.get("DATABASE_URL", "")):
        return
    try:
        import pathlib
        schema_path = pathlib.Path(__file__).parent / "db" / "schema.sql"
        if schema_path.exists():
            sql = schema_path.read_text()
            try:
                with get_pg_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                logger.info("[PG] PostgreSQL tables initialized using schema.sql.")
            except Exception as schema_err:
                # Non-fatal: tables may already exist with slightly different definitions
                logger.warning(f"[PG] schema.sql partial error (continuing): {schema_err}")
        else:
            logger.error(f"[PG] schema.sql not found at {schema_path}")

        # Self-healing migrations for column updates
        columns_to_ensure = {
            "users": [
                ("firebase_uid", "TEXT UNIQUE"),
                ("name", "TEXT NOT NULL DEFAULT 'Elderly'"),
                ("age", "INTEGER"),
                ("role", "TEXT NOT NULL DEFAULT 'elderly'"),
                ("phone", "TEXT"),
                ("email", "TEXT"),
                ("medical_conditions", "TEXT[]"),
                ("medications", "TEXT[]"),
                ("medical_notes", "TEXT"),
                ("caregiver_for_user_id", "UUID REFERENCES users(user_id) ON DELETE SET NULL"),
                ("relationship", "TEXT"),
                ("caregiver_name", "TEXT"),
                ("caregiver_phone", "TEXT"),
                ("caregiver_email", "TEXT"),
                ("voice_id", "TEXT"),
                ("tts_provider", "TEXT NOT NULL DEFAULT 'elevenlabs'"),
                ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
                ("updated_at", "TIMESTAMPTZ NOT NULL DEFAULT now()")
            ],
            "assessments": [
                ("intent", "TEXT NOT NULL DEFAULT 'health_issue'"),
                ("symptoms", "TEXT[] NOT NULL DEFAULT '{}'"),
                ("severity", "TEXT NOT NULL DEFAULT 'low'"),
                ("confidence", "NUMERIC(4,3) NOT NULL DEFAULT 1.000"),
                ("score", "INTEGER NOT NULL DEFAULT 0"),
                ("base_score", "INTEGER NOT NULL DEFAULT 0"),
                ("risk_level", "TEXT NOT NULL DEFAULT 'LOW'"),
                ("category", "TEXT NOT NULL DEFAULT 'GENERAL'"),
                ("action", "TEXT NOT NULL DEFAULT 'monitor'"),
                ("message", "TEXT NOT NULL DEFAULT ''"),
                ("steps", "TEXT[] NOT NULL DEFAULT '{}'"),
                ("breakdown", "TEXT[] NOT NULL DEFAULT '{}'"),
                ("bolna_call_id", "TEXT"),
                ("recording_url", "TEXT"),
                ("transcript", "TEXT"),
                ("emotion_analysis", "TEXT"),
                ("assessed_at", "TIMESTAMPTZ NOT NULL DEFAULT now()")
            ]
        }
        
        # Handle rename: vapi_call_id → bolna_call_id (for databases created before Bolna migration)
        # Covers both assessments and conversations tables — each in its own transaction
        for _tbl in ("assessments", "conversations"):
            try:
                with get_pg_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = %s AND column_name = 'vapi_call_id'
                        """, (_tbl,))
                        if cur.fetchone():
                            cur.execute(f"ALTER TABLE {_tbl} RENAME COLUMN vapi_call_id TO bolna_call_id;")
                            logger.info(f"[PG] Renamed {_tbl}.vapi_call_id → bolna_call_id.")
            except Exception as rename_err:
                logger.debug(f"[PG] {_tbl} rename skipped (likely already done): {rename_err}")

        # Add missing columns — each in its own transaction so one failure doesn't abort others
        for table, cols in columns_to_ensure.items():
            for col_name, col_type in cols:
                try:
                    with get_pg_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
                            )
                except Exception as col_err:
                    logger.debug(f"[PG] Column {table}.{col_name} already exists or incompatible: {col_err}")
        logger.info("[PG] PostgreSQL columns verified and upgraded successfully.")
    except Exception as e:
        logger.error(f"[PG] Failed to initialize/migrate Postgres tables: {e}")




# ===========================================================================
# Supabase helper
# ===========================================================================

def get_supabase() -> Optional['SupabaseClient']:
    if USE_SUPABASE and _SUPABASE_AVAILABLE and SUPABASE_URL and SUPABASE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    return None


# ===========================================================================
# SQLite helpers (fallback)
# ===========================================================================

def _resolve_db_path(db_path: Optional[str]) -> str:
    if db_path is not None:
        return db_path
    return os.environ.get("WELLRING_DB_PATH", DB_PATH)


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initialize the SQLite schema (used when Postgres is NOT configured).
    For Postgres: run `python -m src.db.migrate` instead.
    """
    if _use_postgres():
        logger.info("PostgreSQL backend active — still initializing SQLite for reminders table.")


    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            intent          TEXT    NOT NULL,
            symptoms        TEXT    NOT NULL,
            severity        TEXT    NOT NULL,
            confidence      REAL    NOT NULL,
            score           INTEGER NOT NULL,
            risk_level      TEXT    NOT NULL,
            category        TEXT    NOT NULL,
            action          TEXT    NOT NULL,
            message         TEXT    NOT NULL,
            user_id         TEXT,
            recording_url   TEXT,
            transcript      TEXT,
            emotion_analysis TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id             TEXT PRIMARY KEY,
            firebase_uid        TEXT UNIQUE,
            name                TEXT NOT NULL,
            phone               TEXT,
            age                 INTEGER,
            medical_conditions  TEXT,
            medical_notes       TEXT,
            role                TEXT,
            caregiver_for_user_id TEXT,
            relationship        TEXT,
            voice_id            TEXT,
            tts_provider        TEXT,
            updated_at          TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            interaction_id      INTEGER,
            timestamp           TEXT    NOT NULL,
            risk_level          TEXT    NOT NULL,
            notification_type   TEXT    NOT NULL,
            status              TEXT    NOT NULL,
            FOREIGN KEY(interaction_id) REFERENCES interactions(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            type            TEXT NOT NULL,
            title           TEXT NOT NULL,
            time            TEXT NOT NULL,
            frequency       TEXT NOT NULL,
            phone           TEXT NOT NULL,
            notes           TEXT,
            last_triggered  TEXT
        )
    """)

    conn.commit()
    # Migrate any pre-existing DB with old schema
    _migrate_sqlite_schema(conn)
    conn.close()
    logger.info(f"SQLite database initialized at {db_path}")


def _migrate_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Upgrade legacy 'users' table if it was created with the old schema."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in cur.fetchall()}

    # Columns that must exist in the new schema but may be absent in old DBs
    needed_cols = {
        "firebase_uid": "TEXT",
        "phone": "TEXT",
        "medical_notes": "TEXT",
        "role": "TEXT",
        "caregiver_for_user_id": "TEXT",
        "relationship": "TEXT",
        "updated_at": "TEXT",
    }
    for col, col_type in needed_cols.items():
        if col not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                logger.info(f"[DB-MIGRATE] Added column '{col}' to users table")
            except Exception as e:
                logger.warning(f"[DB-MIGRATE] Could not add column '{col}': {e}")

    # Rename legacy PK 'id' -> 'user_id' (SQLite 3.25+ supports RENAME COLUMN)
    if "id" in existing_cols and "user_id" not in existing_cols:
        try:
            cur.execute("ALTER TABLE users RENAME COLUMN id TO user_id")
            logger.info("[DB-MIGRATE] Renamed column 'id' -> 'user_id'")
        except Exception as e:
            logger.warning(f"[DB-MIGRATE] Could not rename id->user_id: {e}")

    # Add transcript column to interactions if missing
    cur.execute("PRAGMA table_info(interactions)")
    existing_int_cols = {row[1] for row in cur.fetchall()}
    if "transcript" not in existing_int_cols:
        try:
            cur.execute("ALTER TABLE interactions ADD COLUMN transcript TEXT")
            logger.info("[DB-MIGRATE] Added column 'transcript' to interactions table")
        except Exception as e:
            logger.warning(f"[DB-MIGRATE] Could not add column 'transcript' to interactions: {e}")

    conn.commit()


# ===========================================================================
# log_interaction  (backward-compatible entry point)
# ===========================================================================

def log_interaction(data: Dict[str, Any], db_path: Optional[str] = None) -> Union[int, str]:
    """
    Log an assessment result.

    Returns:
        UUID string (Postgres) or integer row id (SQLite/Supabase).
    """
    # -- Postgres --
    if _use_postgres() and _PG_AVAILABLE:
        return _log_interaction_pg(data)

    # -- Supabase --
    if USE_SUPABASE and _SUPABASE_AVAILABLE:
        result = _log_interaction_supabase(data)
        if result is not None:
            return result

    # -- SQLite fallback --
    return _log_interaction_sqlite(data, db_path)


def _log_interaction_pg(data: Dict[str, Any]) -> str:
    """Insert into Postgres `assessments` table, returns UUID string."""
    user_id = data.get("user_id")  # may be None if anonymous

    # If no user_id supplied, use (or create) the anonymous sentinel user
    if user_id in (None, "", "anonymous"):
        user_id = _ensure_anonymous_user_pg()

    sql = """
        INSERT INTO assessments (
            user_id, intent, symptoms, severity, confidence,
            score, base_score, risk_level, category, action,
            message, steps, breakdown, bolna_call_id, recording_url, transcript, emotion_analysis
        ) VALUES (
            %(user_id)s, %(intent)s, %(symptoms)s, %(severity)s, %(confidence)s,
            %(score)s, %(base_score)s, %(risk_level)s, %(category)s, %(action)s,
            %(message)s, %(steps)s, %(breakdown)s, %(bolna_call_id)s, %(recording_url)s, %(transcript)s, %(emotion_analysis)s
        )
        RETURNING assessment_id
    """
    params = {
        "user_id":       user_id,
        "intent":        data.get("intent", "health_issue"),
        "symptoms":      data.get("symptoms", []),
        "severity":      (data.get("severity") or "low").lower(),
        "confidence":    data.get("confidence", 1.0),
        "score":         data.get("score", 0),
        "base_score":    data.get("base_score", 0),
        "risk_level":    data.get("risk_level", "LOW"),
        "category":      data.get("category", "UNKNOWN"),
        "action":        data.get("action", "monitor"),
        "message":       data.get("message", ""),
        "steps":         data.get("steps", []),
        "breakdown":     data.get("breakdown", []),
        "bolna_call_id":  data.get("bolna_call_id"),
        "recording_url": data.get("recording_url"),
        "transcript":    data.get("transcript"),
        "emotion_analysis": data.get("emotion_analysis"),
    }

    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            assessment_id = str(row["assessment_id"])

    logger.info(f"[PG] Assessment logged: {assessment_id}")
    return assessment_id


def _ensure_anonymous_user_pg() -> str:
    """
    Return the UUID of the 'anonymous' sentinel user, creating it if needed.
    """
    sql_select = "SELECT user_id FROM users WHERE email = 'anonymous@wellring.internal' LIMIT 1"
    sql_insert = """
        INSERT INTO users (name, role, email)
        VALUES ('Anonymous', 'elderly', 'anonymous@wellring.internal')
        ON CONFLICT DO NOTHING
        RETURNING user_id
    """
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(sql_select)
            row = cur.fetchone()
            if row:
                return str(row["user_id"])
            cur.execute(sql_insert)
            row = cur.fetchone()
            return str(row["user_id"]) if row else ""


# Sentinel UUID for unmatched callers. History-based multipliers must NOT
# accumulate on this account — it is a shared bucket, not a single patient.
# Fetched once at import; safe since it's create-if-not-exists.
try:
    ANONYMOUS_USER_ID: str = _ensure_anonymous_user_pg() if _use_postgres() and _PG_AVAILABLE else ""
except Exception:
    ANONYMOUS_USER_ID = ""


def _log_interaction_supabase(data: Dict[str, Any]) -> Optional[int]:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        res = supabase.table("interactions").insert({
            "timestamp":     data.get("timestamp", datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z"),
            "intent":        data.get("intent", ""),
            "symptoms":      data.get("symptoms", []),
            "severity":      data.get("severity", ""),
            "confidence":    data.get("confidence", 1.0),
            "score":         data.get("score", 0),
            "risk_level":    data.get("risk_level", "LOW"),
            "category":      data.get("category", ""),
            "action":        data.get("action", ""),
            "message":       data.get("message", ""),
            "user_id":       data.get("user_id"),
            "recording_url": data.get("recording_url"),
            "transcript":    data.get("transcript"),
            "emotion_analysis": data.get("emotion_analysis"),
        }).execute()
        if res.data:
            return int(res.data[0]["id"]) # type: ignore
    except Exception as exc:
        logger.error(f"Supabase insert failed: {exc}. Falling back to SQLite.")
    return None


def _log_interaction_sqlite(data: Dict[str, Any], db_path: Optional[str]) -> int:
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO interactions (
            timestamp, intent, symptoms, severity, confidence,
            score, risk_level, category, action, message, user_id, recording_url, transcript, emotion_analysis
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("timestamp", datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z"),
        data.get("intent", ""),
        json.dumps(data.get("symptoms", [])),
        data.get("severity", ""),
        data.get("confidence", 1.0),
        data.get("score", 0),
        data.get("risk_level", "LOW"),
        data.get("category", ""),
        data.get("action", ""),
        data.get("message", ""),
        data.get("user_id"),
        data.get("recording_url"),
        data.get("transcript"),
        data.get("emotion_analysis"),
    ))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(row_id) if row_id else 0


# ===========================================================================
# get_symptom_repeat_count
# ===========================================================================

def get_symptom_repeat_count(symptom: str, days: int = 3, db_path: Optional[str] = None, user_id: Optional[str] = None) -> int:
    """
    Returns how many times a symptom was logged in the last `days` days.
    Used by the scoring engine to compute the history escalation multiplier.
    """
    if _use_postgres() and _PG_AVAILABLE:
        return _symptom_count_pg(symptom, days, user_id)

    if USE_SUPABASE and _SUPABASE_AVAILABLE:
        result = _symptom_count_supabase(symptom, days)
        if result >= 0:
            return result

    return _symptom_count_sqlite(symptom, days, db_path)


def _symptom_count_pg(symptom: str, days: int, user_id: Optional[str]) -> int:
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    sql = """
        SELECT COUNT(*) AS cnt
        FROM   assessments
        WHERE  %(symptom)s = ANY(symptoms)
          AND  assessed_at >= %(cutoff)s
    """
    params: Dict[str, Any] = {"symptom": symptom, "cutoff": cutoff}
    if user_id:
        sql += " AND user_id = %(user_id)s"
        params["user_id"] = user_id

    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(sql, params)
            return int(cur.fetchone()["cnt"])


def _symptom_count_supabase(symptom: str, days: int) -> int:
    supabase = get_supabase()
    if not supabase:
        return -1
    try:
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat() + "Z"
        res = (
            supabase.table("interactions")
            .select("id", count="exact") # type: ignore
            .gte("timestamp", cutoff)
            .contains("symptoms", [symptom])
            .execute()
        )
        return res.count if res.count is not None else 0
    except Exception as exc:
        logger.error(f"Supabase symptom count failed: {exc}")
        return -1


def _symptom_count_sqlite(symptom: str, days: int, db_path: Optional[str]) -> int:
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(DISTINCT i.id)
        FROM   interactions i, json_each(i.symptoms) je
        WHERE  je.value = ?
          AND  i.timestamp >= datetime('now', ? || ' days')
        """,
        (symptom, f"-{days}"),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


# ===========================================================================
# log_alert
# ===========================================================================

def log_alert(
    interaction_id: Union[int, str],
    timestamp: str,
    risk_level: str,
    notification_type: str,
    status: str,
    db_path: Optional[str] = None,
    recipient_name: Optional[str] = None,
    recipient_phone: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> None:
    """Log a sent alert / notification."""

    if _use_postgres() and _PG_AVAILABLE:
        _log_alert_pg(interaction_id, risk_level, notification_type, status,
                      recipient_name, recipient_phone, recipient_email)
        return

    if USE_SUPABASE and _SUPABASE_AVAILABLE:
        supabase = get_supabase()
        if supabase:
            try:
                supabase.table("alerts_log").insert({
                    "interaction_id":   interaction_id,
                    "timestamp":        timestamp,
                    "risk_level":       risk_level,
                    "notification_type": notification_type,
                    "status":           status,
                }).execute()
                return
            except Exception as exc:
                logger.error(f"Supabase alert log failed: {exc}")

    # SQLite fallback
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO alerts_log (interaction_id, timestamp, risk_level, notification_type, status)
        VALUES (?, ?, ?, ?, ?)
    """, (interaction_id, timestamp, risk_level, notification_type, status))
    conn.commit()
    conn.close()


def _log_alert_pg(
    assessment_id: Union[int, str],
    risk_level: str,
    alert_type: str,
    status: str,
    recipient_name: Optional[str],
    recipient_phone: Optional[str],
    recipient_email: Optional[str],
) -> None:
    # Normalize alert_type to lowercase (DB check constraint requires lowercase)
    _VALID_ALERT_TYPES = {"sms", "call", "email", "push", "emergency_services", "in_app"}
    alert_type_norm = alert_type.lower()
    if alert_type_norm not in _VALID_ALERT_TYPES:
        alert_type_norm = "sms"  # safe fallback

    # 'mock' is used in tests / dry-runs — treat as 'sent' for DB purposes
    _VALID_STATUSES = {"pending", "sent", "delivered", "failed"}
    status_norm = status.lower()
    if status_norm not in _VALID_STATUSES:
        status_norm = "sent"

    sql = """
        INSERT INTO alerts (
            assessment_id, alert_type, status,
            recipient_name, recipient_phone, recipient_email
        ) VALUES (
            %(assessment_id)s, %(alert_type)s, %(status)s,
            %(recipient_name)s, %(recipient_phone)s, %(recipient_email)s
        )
    """
    import uuid as _uuid
    try:
        _uuid.UUID(str(assessment_id))
        valid_uuid = True
    except (ValueError, AttributeError):
        valid_uuid = False

    if not valid_uuid:
        logger.info(f"[PG] Skipping alert log — interaction_id '{assessment_id}' is not a UUID (manual/test call).")
        return

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "assessment_id":  str(assessment_id),
                "alert_type":     alert_type_norm,
                "status":         status_norm,
                "recipient_name":  recipient_name,
                "recipient_phone": recipient_phone,
                "recipient_email": recipient_email,
            })



# ===========================================================================
# upsert_health_history  (new, Postgres-first)
# ===========================================================================

def upsert_health_history(
    user_id: str,
    symptom: str,
    assessment_id: Optional[str] = None,
    severity: Optional[str] = None,
    risk_level: Optional[str] = None,
    window_days: int = 3,
) -> None:
    """
    Insert or update the rolling health_history record for (user, symptom).
    Called automatically after every Postgres assessment write.
    """
    if not (_use_postgres() and _PG_AVAILABLE):
        return  # No-op for SQLite/Supabase

    now = datetime.datetime.now(datetime.UTC)
    window_start = now - datetime.timedelta(days=window_days)

    sql = """
        INSERT INTO health_history (
            user_id, symptom, window_start, window_end,
            occurrence_count, peak_severity, peak_risk_level, last_assessment_id
        )
        VALUES (
            %(user_id)s, %(symptom)s, %(window_start)s, %(now)s,
            1, %(severity)s, %(risk_level)s, %(assessment_id)s
        )
        ON CONFLICT DO NOTHING
    """
    # We use a simple "always insert" strategy; the scoring engine counts rows.
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "user_id":       user_id,
                "symptom":       symptom,
                "window_start":  window_start,
                "now":           now,
                "severity":      (severity or "low").lower(),
                "risk_level":    (risk_level or "LOW").upper(),
                "assessment_id": assessment_id,
            })


# ===========================================================================
# log_conversation_turn  (new, Postgres-first)
# ===========================================================================

def log_conversation_turn(
    user_id: str,
    role: str,
    content: str,
    bolna_call_id: Optional[str] = None,
    channel: str = "web",
    assessment_id: Optional[str] = None,
    audio_url: Optional[str] = None,
) -> Optional[str]:
    """
    Persist a single conversation message to the `conversations` table.
    Returns the UUID of the inserted row (Postgres only).
    """
    if not (_use_postgres() and _PG_AVAILABLE):
        return None

    sql = """
        INSERT INTO conversations (
            user_id, assessment_id, bolna_call_id,
            channel, role, content, audio_url
        ) VALUES (
            %(user_id)s, %(assessment_id)s, %(bolna_call_id)s,
            %(channel)s, %(role)s, %(content)s, %(audio_url)s
        )
        RETURNING conversation_id
    """
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(sql, {
                "user_id":       user_id,
                "assessment_id": assessment_id,
                "bolna_call_id":  bolna_call_id,
                "channel":       channel,
                "role":          role,
                "content":       content,
                "audio_url":     audio_url,
            })
            return str(cur.fetchone()["conversation_id"])


# ===========================================================================
# Reminders (Multi-backend)
# ===========================================================================

def add_reminder(
    type_val: str, title: str, time_val: str,
    frequency: str, phone: str,
    notes: Optional[str] = None, db_path: Optional[str] = None,
) -> int:
    if _use_postgres() and _PG_AVAILABLE:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute("""
                    INSERT INTO reminders (type, title, time, frequency, phone, notes, last_triggered)
                    VALUES (%(type)s, %(title)s, %(time)s, %(frequency)s, %(phone)s, %(notes)s, NULL)
                    RETURNING id
                """, {
                    "type": type_val, "title": title, "time": time_val,
                    "frequency": frequency, "phone": phone, "notes": notes
                })
                row = cur.fetchone()
                return int(row["id"]) if row else 0

    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO reminders (type, title, time, frequency, phone, notes, last_triggered)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
    """, (type_val, title, time_val, frequency, phone, notes))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(row_id) if row_id else 0


def get_reminders(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    if _use_postgres() and _PG_AVAILABLE:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute("SELECT * FROM reminders")
                return [dict(r) for r in cur.fetchall()]

    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM reminders")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def delete_reminder(reminder_id: int, db_path: Optional[str] = None) -> bool:
    if _use_postgres() and _PG_AVAILABLE:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
                return cur.rowcount > 0

    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def update_reminder_trigger(reminder_id: int, timestamp: str, db_path: Optional[str] = None) -> bool:
    if _use_postgres() and _PG_AVAILABLE:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute("UPDATE reminders SET last_triggered = %s WHERE id = %s", (timestamp, reminder_id))
                return cur.rowcount > 0

    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE reminders SET last_triggered = ? WHERE id = ?", (timestamp, reminder_id))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ===========================================================================
# History Retrieval and Stats (Unified Multi-backend)
# ===========================================================================

def _get_assessments_pg(limit: int = 50, risk_level: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = """
        SELECT assessment_id AS id,
               assessment_id,
               assessed_at AS timestamp,
               assessed_at,
               intent,
               symptoms,
               severity,
               confidence,
               score,
               base_score,
               risk_level,
               category,
               action,
               message,
               user_id,
               recording_url,
               bolna_call_id,
               transcript,
               emotion_analysis
        FROM assessments
    """
    params: List[Any] = []
    if risk_level:
        sql += " WHERE risk_level = %s"
        params.append(risk_level.upper())
    
    sql += " ORDER BY assessed_at DESC LIMIT %s"
    params.append(limit)
    
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            
    result = []
    for r in rows:
        r_dict = dict(r)
        if r_dict.get("confidence") is not None:
            r_dict["confidence"] = float(r_dict["confidence"])
        if r_dict.get("timestamp") is not None:
            if hasattr(r_dict["timestamp"], "isoformat"):
                r_dict["timestamp"] = r_dict["timestamp"].isoformat()
        if r_dict.get("assessed_at") is not None:
            if hasattr(r_dict["assessed_at"], "isoformat"):
                r_dict["assessed_at"] = r_dict["assessed_at"].isoformat()
        if r_dict.get("id") is not None:
            r_dict["id"] = str(r_dict["id"])
        if r_dict.get("assessment_id") is not None:
            r_dict["assessment_id"] = str(r_dict["assessment_id"])
        if r_dict.get("user_id") is not None:
            r_dict["user_id"] = str(r_dict["user_id"])
        if r_dict.get("symptoms") is None:
            r_dict["symptoms"] = []
        result.append(r_dict)
    return result


def _get_assessment_stats_pg() -> Dict[str, Any]:
    sql = """
        SELECT 
            COUNT(*) as total_today,
            SUM(CASE WHEN risk_level = 'LOW' THEN 1 ELSE 0 END) as low,
            SUM(CASE WHEN risk_level = 'MEDIUM' THEN 1 ELSE 0 END) as medium,
            SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END) as high,
            SUM(CASE WHEN risk_level = 'CRITICAL' THEN 1 ELSE 0 END) as critical
        FROM assessments 
        WHERE assessed_at::date = CURRENT_DATE
    """
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(sql)
            row = cur.fetchone()
            
    if not row:
        return {"total_today": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}
        
    return {
        "total_today": int(row["total_today"] or 0),
        "low": int(row["low"] or 0),
        "medium": int(row["medium"] or 0),
        "high": int(row["high"] or 0),
        "critical": int(row["critical"] or 0)
    }


def _get_assessments_supabase(limit: int = 50, risk_level: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        q = supabase.table("interactions").select("*")
        if risk_level:
            q = q.eq("risk_level", risk_level.upper())
        res = q.order("timestamp", desc=True).limit(limit).execute()
        
        result = []
        for r in (res.data or []):
            r_dict = dict(r) # type: ignore
            if isinstance(r_dict.get("symptoms"), str):
                try:
                    r_dict["symptoms"] = json.loads(str(r_dict["symptoms"]))
                except Exception:
                    pass
            elif r_dict.get("symptoms") is None:
                r_dict["symptoms"] = []
            result.append(r_dict)
        return result
    except Exception as exc:
        logger.error(f"Supabase get_assessments failed: {exc}")
    return None


def _get_assessment_stats_supabase() -> Optional[Dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        today_start = datetime.datetime.now(datetime.UTC).date().isoformat() + "T00:00:00Z"
        res = supabase.table("interactions").select("risk_level").gte("timestamp", today_start).execute()
        
        counts = {"total_today": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}
        for r in (res.data or []):
            rl = str(dict(r).get("risk_level", "")).upper() # type: ignore
            counts["total_today"] += 1
            if rl == "LOW":
                counts["low"] += 1
            elif rl == "MEDIUM":
                counts["medium"] += 1
            elif rl == "HIGH":
                counts["high"] += 1
            elif rl == "CRITICAL":
                counts["critical"] += 1
        return counts
    except Exception as exc:
        logger.error(f"Supabase get_assessment_stats failed: {exc}")
    return None


def _get_assessments_sqlite(limit: int = 50, risk_level: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='interactions'
    """)
    if not cursor.fetchone():
        conn.close()
        return []
    
    query = "SELECT * FROM interactions ORDER BY timestamp DESC LIMIT ?"
    params = [limit]
    
    if risk_level:
        query = "SELECT * FROM interactions WHERE risk_level = ? ORDER BY timestamp DESC LIMIT ?"
        params = [risk_level.upper(), limit] # type: ignore
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        r_dict = dict(row)
        if isinstance(r_dict.get("symptoms"), str):
            try:
                r_dict["symptoms"] = json.loads(r_dict["symptoms"])
            except Exception:
                pass
        elif r_dict.get("symptoms") is None:
            r_dict["symptoms"] = []
        result.append(r_dict)
        
    return result


def _get_assessment_stats_sqlite(db_path: Optional[str] = None) -> Dict[str, Any]:
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='interactions'
    """)
    if not cursor.fetchone():
        conn.close()
        return {"total_today": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}
        
    cursor.execute("""
        SELECT 
            COUNT(*) as total_today,
            SUM(CASE WHEN risk_level = 'LOW' THEN 1 ELSE 0 END) as low,
            SUM(CASE WHEN risk_level = 'MEDIUM' THEN 1 ELSE 0 END) as medium,
            SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END) as high,
            SUM(CASE WHEN risk_level = 'CRITICAL' THEN 1 ELSE 0 END) as critical
        FROM interactions 
        WHERE date(timestamp) = date('now')
    """)
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return {"total_today": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}
        
    return {
        "total_today": row[0] or 0,
        "low": row[1] or 0,
        "medium": row[2] or 0,
        "high": row[3] or 0,
        "critical": row[4] or 0
    }


def get_assessments_list(limit: int = 50, risk_level: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve interactions/assessments based on the configured database backend."""
    # -- Postgres --
    if _use_postgres() and _PG_AVAILABLE:
        return _get_assessments_pg(limit, risk_level)

    # -- Supabase --
    if USE_SUPABASE and _SUPABASE_AVAILABLE:
        result = _get_assessments_supabase(limit, risk_level)
        if result is not None:
            return result

    # -- SQLite fallback --
    return _get_assessments_sqlite(limit, risk_level, db_path)


def get_assessment_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve interaction statistics based on the configured database backend."""
    # -- Postgres --
    if _use_postgres() and _PG_AVAILABLE:
        return _get_assessment_stats_pg()

    # -- Supabase --
    if USE_SUPABASE and _SUPABASE_AVAILABLE:
        result = _get_assessment_stats_supabase()
        if result is not None:
            return result

    # -- SQLite fallback --
    return _get_assessment_stats_sqlite(db_path)


# ===========================================================================
# get_user_health_context  — used by /call to inject memory into Bolna prompt
# ===========================================================================

def get_user_health_context(phone: str, days: int = 7) -> Dict[str, Any]:
    """
    Fetch a user's recent health history by phone number.

    Returns a dict with:
        user_id          — UUID of the user (or None if not found)
        user_name        — user's name (or "the patient")
        recent_symptoms  — deduplicated list of symptom keys from last `days` days
        last_risk_level  — most recent risk level string (LOW/MEDIUM/HIGH/CRITICAL)
        last_assessment  — ISO timestamp of the last assessment
        summary_lines    — list of human-readable strings describing recent history
        has_history      — bool: True if any assessments found
    """
    if _use_postgres() and _PG_AVAILABLE:
        return _get_user_health_context_pg(phone, days)

    # SQLite / no-history fallback
    return {
        "user_id": None,
        "user_name": "the patient",
        "recent_symptoms": [],
        "last_risk_level": None,
        "last_assessment": None,
        "summary_lines": [],
        "has_history": False,
        "medical_conditions": [],
        "medical_notes": "",
    }


def _get_user_health_context_pg(phone: str, days: int) -> Dict[str, Any]:
    """PostgreSQL implementation of get_user_health_context."""
    # Normalise: strip spaces / dashes so +91 90042 61186 == +919004261186
    phone_clean = phone.replace(" ", "").replace("-", "")

    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)

    # 1. Look up user by phone (try both raw and cleaned)
    user_sql = """
        SELECT user_id, name, age, medical_conditions, medical_notes, caregiver_phone
        FROM   users
        WHERE  phone = %(phone)s OR phone = %(phone_clean)s
        LIMIT  1
    """
    # 2. Fetch recent assessments for this user
    assess_sql = """
        SELECT symptoms, severity, risk_level, assessed_at
        FROM   assessments
        WHERE  user_id = %(user_id)s
          AND  assessed_at >= %(cutoff)s
        ORDER  BY assessed_at DESC
        LIMIT  20
    """

    result: Dict[str, Any] = {
        "user_id": None,
        "user_name": "the patient",
        "recent_symptoms": [],
        "last_risk_level": None,
        "last_assessment": None,
        "summary_lines": [],
        "has_history": False,
        "medical_conditions": [],
        "medical_notes": "",
    }

    try:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                # Find user
                cur.execute(user_sql, {"phone": phone, "phone_clean": phone_clean})
                user_row = cur.fetchone()

                if not user_row:
                    logger.info(f"[CTX] No user found for phone {phone_clean}")
                    return result

                user_id  = str(user_row["user_id"])
                user_name = user_row["name"] or "the patient"
                result["user_id"]   = user_id
                result["user_name"] = user_name
                result["medical_conditions"] = user_row.get("medical_conditions") or []
                result["medical_notes"] = user_row.get("medical_notes") or ""

                # Fetch assessments
                cur.execute(assess_sql, {"user_id": user_id, "cutoff": cutoff})
                rows = cur.fetchall()

                if not rows:
                    logger.info(f"[CTX] No recent assessments for user {user_id}")
                    return result

                result["has_history"] = True
                result["last_risk_level"] = rows[0]["risk_level"]
                result["last_assessment"] = rows[0]["assessed_at"].isoformat() if rows[0]["assessed_at"] else None

                # Collect unique symptoms across all recent assessments
                seen: set = set()
                all_symptoms: List[str] = []
                for row in rows:
                    for sym in (row["symptoms"] or []):
                        if sym and sym not in seen:
                            seen.add(sym)
                            all_symptoms.append(sym)

                result["recent_symptoms"] = all_symptoms

                # Build natural-language summary lines
                summary: List[str] = []

                # Group by day for "yesterday", "2 days ago" etc.
                now_date = datetime.datetime.now(datetime.UTC).date()
                day_buckets: Dict[int, List[str]] = {}
                for row in rows:
                    if not row["assessed_at"]:
                        continue
                    row_date = row["assessed_at"].date() if hasattr(row["assessed_at"], "date") else datetime.datetime.fromisoformat(str(row["assessed_at"])).date()
                    days_ago = (now_date - row_date).days
                    bucket_syms = row["symptoms"] or []
                    if bucket_syms:
                        day_buckets.setdefault(days_ago, []).extend(bucket_syms)

                for days_ago_key in sorted(day_buckets.keys()):
                    syms = list(dict.fromkeys(day_buckets[days_ago_key]))  # dedup, preserve order
                    sym_str = ", ".join(s.replace("_", " ") for s in syms)
                    if days_ago_key == 0:
                        label = "earlier today"
                    elif days_ago_key == 1:
                        label = "yesterday"
                    else:
                        label = f"{days_ago_key} days ago"
                    summary.append(f"{label.capitalize()}: {sym_str}")

                result["summary_lines"] = summary
                logger.info(f"[CTX] Built health context for {user_name}: {summary}")

    except Exception as exc:
        logger.error(f"[CTX] Error fetching health context for {phone}: {exc}")

    return result


# ===========================================================================
# Onboarding & User Profile Management
# ===========================================================================

def upsert_user_profile(firebase_uid: str, name: str, phone: str, age: Optional[int], 
                        conditions: List[str], notes: str,
                        voice_id: Optional[str] = None, tts_provider: str = "elevenlabs") -> str:
    """Upsert the elder user's profile, returns the Postgres UUID."""
    if not _use_postgres() or not _PG_AVAILABLE:
        # SQLite fallback
        import uuid
        import json
        import datetime
        db_path = _resolve_db_path(None)
        user_id = str(uuid.uuid4())
        now_str = datetime.datetime.utcnow().isoformat()
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, firebase_uid, name, phone, age, medical_conditions, medical_notes, role, voice_id, tts_provider, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'elderly', ?, ?, ?)
            ON CONFLICT(firebase_uid) DO UPDATE SET
                name = excluded.name,
                phone = excluded.phone,
                age = excluded.age,
                medical_conditions = excluded.medical_conditions,
                medical_notes = excluded.medical_notes,
                voice_id = excluded.voice_id,
                tts_provider = excluded.tts_provider,
                updated_at = excluded.updated_at
        """, (user_id, firebase_uid, name, phone, age, json.dumps(conditions), notes, voice_id, tts_provider, now_str))
        conn.commit()
        # Fetch actual user_id in case of update
        cur.execute("SELECT user_id FROM users WHERE firebase_uid = ?", (firebase_uid,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else ""
        
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                INSERT INTO users (firebase_uid, name, phone, age, medical_conditions, medical_notes, role, voice_id, tts_provider)
                VALUES (%(uid)s, %(name)s, %(phone)s, %(age)s, %(conds)s, %(notes)s, 'elderly', %(voice_id)s, %(tts_provider)s)
                ON CONFLICT (firebase_uid) DO UPDATE SET
                    name = EXCLUDED.name,
                    phone = EXCLUDED.phone,
                    age = EXCLUDED.age,
                    medical_conditions = EXCLUDED.medical_conditions,
                    medical_notes = EXCLUDED.medical_notes,
                    voice_id = EXCLUDED.voice_id,
                    tts_provider = EXCLUDED.tts_provider,
                    updated_at = now()
                RETURNING user_id
            """, {
                "uid": firebase_uid,
                "name": name,
                "phone": phone,
                "age": age,
                "conds": conditions,
                "notes": notes,
                "voice_id": voice_id,
                "tts_provider": tts_provider
            })
            row = cur.fetchone()
            return str(row["user_id"]) if row else ""

def get_user_by_phone(phone: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look up a user profile by their phone number."""
    if _use_postgres() and _PG_AVAILABLE:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute("SELECT * FROM users WHERE phone = %s", (phone,))
                row = cur.fetchone()
                if row:
                    return dict(row)
        return None
    
    # SQLite fallback
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE phone = ?", (phone,))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_user_profile(firebase_uid: str) -> Optional[Dict[str, Any]]:
    """Retrieve an elder's profile by Firebase UID."""
    if not _use_postgres() or not _PG_AVAILABLE:
        import json
        db_path = _resolve_db_path(None)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, firebase_uid, name, phone, age, medical_conditions, medical_notes, voice_id, tts_provider
            FROM users
            WHERE firebase_uid = ? AND role = 'elderly'
        """, (firebase_uid,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        res = dict(row)
        if res.get("medical_conditions"):
            try:
                res["medical_conditions"] = json.loads(res["medical_conditions"])
            except Exception:
                res["medical_conditions"] = []
        return res
        
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                SELECT user_id, firebase_uid, name, phone, age, medical_conditions, medical_notes, voice_id, tts_provider
                FROM users
                WHERE firebase_uid = %s AND role = 'elderly'
            """, (firebase_uid,))
            row = cur.fetchone()
            return dict(row) if row else None

def upsert_family_contacts(elder_user_id: str, contacts: List[Dict[str, str]]) -> None:
    """Insert or update family caregiver contacts for an elder. This deletes existing contacts first for simplicity."""
    if not _use_postgres() or not _PG_AVAILABLE:
        import uuid
        db_path = _resolve_db_path(None)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE caregiver_for_user_id = ? AND role = 'caregiver'", (elder_user_id,))
        for c in contacts:
            cur.execute("""
                INSERT INTO users (user_id, name, phone, role, caregiver_for_user_id, relationship)
                VALUES (?, ?, ?, 'caregiver', ?, ?)
            """, (str(uuid.uuid4()), c.get("name"), c.get("phone"), elder_user_id, c.get("relationship", "Other")))
        conn.commit()
        conn.close()
        return
        
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            # Delete existing caregivers
            cur.execute("""
                DELETE FROM users
                WHERE caregiver_for_user_id = %s AND role = 'caregiver'
            """, (elder_user_id,))
            
            # Insert new ones
            for c in contacts:
                cur.execute("""
                    INSERT INTO users (name, phone, role, caregiver_for_user_id, relationship)
                    VALUES (%(name)s, %(phone)s, 'caregiver', %(elder_id)s, %(rel)s)
                """, {
                    "name": c.get("name"),
                    "phone": c.get("phone"),
                    "elder_id": elder_user_id,
                    "rel": c.get("relationship", "Other")
                })

def add_single_family_contact(elder_user_id: str, name: str, phone: str, relationship: str) -> None:
    """Add a single family contact without deleting existing ones."""
    if not _use_postgres() or not _PG_AVAILABLE:
        import uuid
        db_path = _resolve_db_path(None)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, name, phone, role, caregiver_for_user_id, relationship)
            VALUES (?, ?, ?, 'caregiver', ?, ?)
        """, (str(uuid.uuid4()), name, phone, elder_user_id, relationship))
        conn.commit()
        conn.close()
        return

    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                INSERT INTO users (name, phone, role, caregiver_for_user_id, relationship)
                VALUES (%(name)s, %(phone)s, 'caregiver', %(elder_id)s, %(rel)s)
            """, {
                "name": name,
                "phone": phone,
                "elder_id": elder_user_id,
                "rel": relationship
            })

def get_family_contacts(elder_user_id: str) -> List[Dict[str, Any]]:
    """Fetch family contacts linked to an elder."""
    if not _use_postgres() or not _PG_AVAILABLE:
        db_path = _resolve_db_path(None)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, name, phone, relationship
            FROM users
            WHERE caregiver_for_user_id = ? AND role = 'caregiver'
        """, (elder_user_id,))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
        
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                SELECT user_id, name, phone, relationship
                FROM users
                WHERE caregiver_for_user_id = %s AND role = 'caregiver'
            """, (elder_user_id,))
            return [dict(r) for r in cur.fetchall()]

def delete_family_contact(contact_id: str) -> bool:
    """Delete a specific family contact."""
    if not _use_postgres() or not _PG_AVAILABLE:
        db_path = _resolve_db_path(None)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id = ? AND role = 'caregiver'", (contact_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                DELETE FROM users
                WHERE user_id = %s AND role = 'caregiver'
                RETURNING user_id
            """, (contact_id,))
            return bool(cur.fetchone())


# ===========================================================================
# get_all_patients  (for /patients endpoint)
# ===========================================================================

def get_all_patients(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all elderly patient profiles."""
    if _use_postgres() and _PG_AVAILABLE:
        return _get_all_patients_pg()
    return _get_all_patients_sqlite(db_path)


def _get_all_patients_pg() -> List[Dict[str, Any]]:
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                SELECT user_id, name, age, phone, medical_conditions,
                       medical_notes, caregiver_name, caregiver_phone
                FROM users
                WHERE role = 'elderly'
                ORDER BY name
            """)
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        result.append({
            "id": str(d["user_id"]),
            "name": d.get("name", ""),
            "age": d.get("age"),
            "conditions": d.get("medical_conditions") or [],
            "emergency_contact": d.get("caregiver_phone") or d.get("phone") or "",
            "status": "active",
        })
    return result


def _get_all_patients_sqlite(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='users'
    """)
    if not cur.fetchone():
        conn.close()
        return []
    cur.execute("SELECT * FROM users")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "age": r.get("age"),
            "conditions": r.get("medical_conditions", "").split(",") if r.get("medical_conditions") else [],
            "emergency_contact": r.get("caregiver_phone", ""),
            "status": "active",
        })
    return result


# ===========================================================================
# Call Timeline  (conversation history keyed by phone number)
# ===========================================================================

def _make_diary_line(name: str, symptoms: List[str], risk_level: str, message: str) -> str:
    """
    Convert raw assessment data into a natural, human-readable diary sentence.

    Examples:
        "Mr. Sharma was feeling good and well"
        "Mr. Sharma was experiencing fever and headache"
        "Mr. Sharma reported chest pain — urgent attention needed"
    """
    name = name or "the patient"
    risk = (risk_level or "LOW").upper()
    syms = [s.strip().replace("_", " ") for s in (symptoms or []) if s]

    if risk == "CRITICAL":
        if syms:
            return f"{name} reported {', '.join(syms[:3])} — emergency attention required"
        return f"{name} had a critical health event — emergency attention required"

    if risk == "HIGH":
        if syms:
            return f"{name} reported {', '.join(syms[:3])} — caregiver was notified"
        return f"{name} had a high-risk health event — caregiver was notified"

    if risk == "MEDIUM":
        if syms:
            return f"{name} was experiencing {', '.join(syms[:3])}"
        return f"{name} had some discomfort — routine monitoring advised"

    # LOW
    if syms:
        return f"{name} mentioned {', '.join(syms[:2])} but was otherwise doing okay"
    return f"{name} was feeling good and well"


def get_call_timeline(phone: str, limit: int = 365) -> List[Dict[str, Any]]:
    """
    Fetch the full conversation/call history for an elder by phone number.

    Returns a list ordered oldest → newest, each entry containing:
        assessment_id  — unique ID
        date           — "1 Jan 2026"
        time           — "10:00 AM"
        diary_line     — human-readable sentence: "Mr. Sharma was feeling good"
        risk_level     — LOW / MEDIUM / HIGH / CRITICAL
        symptoms       — list of symptom strings
        score          — integer risk score
    """
    if _use_postgres() and _PG_AVAILABLE:
        return _get_call_timeline_pg(phone, limit)
    return _get_call_timeline_sqlite(phone, limit)


def _get_call_timeline_pg(phone: str, limit: int) -> List[Dict[str, Any]]:
    """PostgreSQL implementation."""
    phone_clean = phone.replace(" ", "").replace("-", "")

    user_sql = """
        SELECT user_id, name
        FROM   users
        WHERE  phone = %(phone)s OR phone = %(phone_clean)s
        LIMIT  1
    """
    assess_sql = """
        SELECT assessment_id, assessed_at, symptoms, risk_level,
               score, message, severity
        FROM   assessments
        WHERE  user_id = %(user_id)s
        ORDER  BY assessed_at ASC
        LIMIT  %(limit)s
    """

    try:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute(user_sql, {"phone": phone, "phone_clean": phone_clean})
                user_row = cur.fetchone()
                if not user_row:
                    return []
                user_id   = str(user_row["user_id"])
                user_name = user_row["name"] or "the patient"

                cur.execute(assess_sql, {"user_id": user_id, "limit": limit})
                rows = cur.fetchall()

        result = []
        for r in rows:
            ts = r["assessed_at"]
            if ts is None:
                continue
            # Convert timezone-aware timestamp to IST for display
            ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            ts_ist = ts.astimezone(ist) if ts.tzinfo else ts

            symptoms = list(r["symptoms"] or [])
            risk     = r["risk_level"] or "LOW"
            score    = r["score"] or 0
            msg      = r["message"] or ""

            result.append({
                "assessment_id": str(r["assessment_id"]),
                "date":          ts_ist.strftime("%-d %b %Y"),        # "1 Jan 2026"
                "time":          ts_ist.strftime("%-I:%M %p"),         # "10:00 AM"
                "iso_timestamp": ts_ist.isoformat(),
                "diary_line":    _make_diary_line(user_name, symptoms, risk, msg),
                "risk_level":    risk,
                "symptoms":      symptoms,
                "score":         score,
                "elder_name":    user_name,
            })
        return result

    except Exception as exc:
        logger.error(f"[TIMELINE] Postgres query failed: {exc}")
        return []


def _get_call_timeline_sqlite(phone: str, limit: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """SQLite fallback — looks up user by phone, then queries interactions table."""
    db_path = _resolve_db_path(db_path)
    phone_clean = phone.replace(" ", "").replace("-", "")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Find user by phone
        cur.execute(
            "SELECT user_id, name FROM users WHERE phone = ? OR phone = ? LIMIT 1",
            (phone, phone_clean),
        )
        user_row = cur.fetchone()

        if user_row:
            user_id   = str(user_row["user_id"])
            user_name = user_row["name"] or "the patient"
            cur.execute(
                """
                SELECT id, timestamp, symptoms, risk_level, score, message
                FROM   interactions
                WHERE  user_id = ?
                ORDER  BY timestamp ASC
                LIMIT  ?
                """,
                (user_id, limit),
            )
        else:
            # Fallback: no user filter, return all interactions
            user_name = "the patient"
            cur.execute(
                """
                SELECT id, timestamp, symptoms, risk_level, score, message
                FROM   interactions
                ORDER  BY timestamp ASC
                LIMIT  ?
                """,
                (limit,),
            )

        rows = cur.fetchall()
        conn.close()

        result = []
        for r in rows:
            ts_raw = r["timestamp"] or ""
            try:
                # Parse ISO string — strip trailing Z for fromisoformat compat
                ts = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                ts_ist = ts.astimezone(ist)
                date_str = ts_ist.strftime("%-d %b %Y")
                time_str = ts_ist.strftime("%-I:%M %p")
                iso_str  = ts_ist.isoformat()
            except Exception:
                date_str = ts_raw[:10]
                time_str = ts_raw[11:16]
                iso_str  = ts_raw

            try:
                symptoms = json.loads(r["symptoms"] or "[]")
            except Exception:
                symptoms = []

            risk  = r["risk_level"] or "LOW"
            score = r["score"] or 0
            msg   = r["message"] or ""

            result.append({
                "assessment_id": str(r["id"]),
                "date":          date_str,
                "time":          time_str,
                "iso_timestamp": iso_str,
                "diary_line":    _make_diary_line(user_name, symptoms, risk, msg),
                "risk_level":    risk,
                "symptoms":      symptoms,
                "score":         score,
                "elder_name":    user_name,
            })
        return result

    except Exception as exc:
        logger.error(f"[TIMELINE] SQLite query failed: {exc}")
        return []
