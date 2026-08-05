"""
rowcount_monitor.py
===================
Scheduled row-count tracker for Phase 0.2 — prevents another silent data wipe.

Usage:
    # One-shot check (good for cron every 5 minutes)
    python -m src.db.rowcount_monitor

    # With divergence alert threshold (alert if any table drops by more than 10%)
    python -m src.db.rowcount_monitor --threshold 0.10

Cron entry (every 5 minutes):
    */5 * * * * cd /home/subaru/Documents/wellring-voice_agent && \
        python -m src.db.rowcount_monitor --threshold 0.10 >> /var/log/wellring_rowcount.log 2>&1

On detection of a suspicious drop, the script:
  1. Logs the event with a full timestamp
  2. Writes a snapshot to /tmp/wellring_rowcount_snapshot.json (can be picked up by a monitoring endpoint)
  3. Exits with code 1 so cron can email/alert on non-zero exit
"""

import os
import sys
import json
import argparse
import datetime
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ROWCOUNT] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rowcount_monitor")

# Which tables to track
TABLES = ["users", "assessments", "conversations", "alerts", "health_history", "reminders"]

# Where to persist the previous known-good snapshot
SNAPSHOT_PATH = Path("/tmp/wellring_rowcount_snapshot.json")


def load_snapshot() -> dict:
    """Load the last known-good rowcount snapshot, or empty dict."""
    if SNAPSHOT_PATH.exists():
        try:
            return json.loads(SNAPSHOT_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_snapshot(counts: dict):
    """Persist rowcounts for the next comparison."""
    SNAPSHOT_PATH.write_text(
        json.dumps({"timestamp": datetime.datetime.now().isoformat(), "counts": counts}, indent=2)
    )


def get_counts() -> dict:
    """
    Query PostgreSQL for row counts of all tracked tables.
    Falls back to SQLite if PG is unavailable.
    Returns: { "users": 42, "assessments": 7, ... }
    """
    db_url = os.environ.get("DATABASE_URL", "")
    counts = {}

    if db_url and not db_url.startswith("sqlite"):
        # PostgreSQL path
        try:
            import psycopg2
            import psycopg2.extras

            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            for table in TABLES:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                row = cur.fetchone()
                counts[table] = row[0] if row else 0
            conn.close()
            return counts
        except Exception as exc:
            logger.warning(f"PostgreSQL query failed ({exc}), falling back to SQLite...")
            # fall through to SQLite

    # SQLite fallback — use os.path.abspath to match database.py resolution
    db_path = os.path.abspath(os.environ.get("WELLRING_DB_PATH", "wellring.db"))

    try:
        import sqlite3

        sqlite_conn = sqlite3.connect(db_path)
        sqlite_cur = sqlite_conn.cursor()
        for table in TABLES:
            try:
                sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
                row = sqlite_cur.fetchone()
                counts[table] = row[0] if row else 0
            except sqlite3.OperationalError:
                counts[table] = -1  # table doesn't exist in this backend
        sqlite_conn.close()
    except sqlite3.OperationalError as exc:
        logger.error(f"SQLite connection failed: {exc}")
        counts = {t: -2 for t in TABLES}  # sentinel for "unreachable"

    return counts


def check_divergence(current: dict, previous: dict, threshold: float) -> bool:
    """
    Compare current rowcounts against previous snapshot.
    Returns True if any table dropped by more than `threshold` fraction.
    Logs details about each anomaly.
    """
    if not previous:
        logger.info("No previous snapshot — skipping divergence check (first run).")
        return False

    had_alert = False
    for table in TABLES:
        cur_val = current.get(table, 0)
        prev_val = previous.get(table, 0)

        if prev_val <= 0:
            continue  # no baseline for this table yet

        if cur_val < 0:
            logger.warning(f"[{table}] Cannot query — backend unreachable (code={cur_val})")
            continue

        # Did it drop?
        if cur_val < prev_val:
            drop_frac = 1.0 - (cur_val / prev_val) if prev_val > 0 else 1.0
            if drop_frac > threshold:
                logger.error(
                    f"[{table}] ⛔ DROP DETECTED: {prev_val} → {cur_val} "
                    f"({drop_frac*100:.1f}% loss, threshold={threshold*100:.0f}%)"
                )
                had_alert = True
            else:
                logger.info(
                    f"[{table}] Normal decrease: {prev_val} → {cur_val} "
                    f"({drop_frac*100:.1f}% — within threshold)"
                )
        elif cur_val > prev_val:
            logger.info(f"[{table}] Increased: {prev_val} → {cur_val}")
        else:
            logger.info(f"[{table}] Unchanged: {cur_val}")

    return had_alert


def main():
    parser = argparse.ArgumentParser(description="WellRing row-count monitor")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.10,
        help="Fractional drop threshold for alert (default: 0.10 = 10%%)",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Print counts as JSON and exit (no comparison, no snapshot save)",
    )
    args = parser.parse_args()

    counts = get_counts()

    # Validate we got something reasonable
    if all(v == -2 for v in counts.values()):
        logger.critical("All databases unreachable — check DATABASE_URL and network.")
        sys.exit(2)

    if args.oneshot:
        print(json.dumps({"timestamp": datetime.datetime.now().isoformat(), "counts": counts}, indent=2))
        return

    previous = load_snapshot()
    had_alert = check_divergence(counts, previous, args.threshold)
    save_snapshot(counts)

    if had_alert:
        logger.critical(f"Row-count divergence detected (threshold={args.threshold}).")
        sys.exit(1)

    logger.info(f"Rowcounts OK: {json.dumps(counts)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
