"""
users.py
========
User Profile system. Handles fetching patient and caregiver info
from either PostgreSQL or the local SQLite database.
"""

import sqlite3
import logging
from typing import Optional, Dict, Any
from src.database import (
    _resolve_db_path,
    _use_postgres, _PG_AVAILABLE, get_pg_conn, _pg_cursor
)

logger = logging.getLogger(__name__)

# Supabase was removed; constant kept for backward compatibility with tests.
USE_SUPABASE: bool = False

def get_user(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch user profile by UUID or string ID."""
    # -- Postgres --
    if _use_postgres() and _PG_AVAILABLE:
        try:
            with get_pg_conn() as conn:
                with _pg_cursor(conn) as cur:
                    cur.execute("SELECT * FROM users WHERE user_id = %s LIMIT 1", (user_id,))
                    row = cur.fetchone()
                    if row:
                        r_dict = dict(row)
                        r_dict["id"] = str(r_dict["user_id"])
                        return r_dict
        except Exception as e:
            logger.error(f"Postgres get_user failed: {e}. Falling back to SQLite.")

    # SQLite Fallback
    db_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    # Return rows as dicts
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None

def get_caregiver_phone(user_id: Optional[str], default_phone: str) -> str:
    """
    Get the caregiver's phone number for a given user.
    Falls back to `default_phone` if user or caregiver phone is missing.
    """
    if not user_id:
        return default_phone
        
    user = get_user(user_id)
    if user and user.get("caregiver_phone"):
        return user["caregiver_phone"]
        
    return default_phone
