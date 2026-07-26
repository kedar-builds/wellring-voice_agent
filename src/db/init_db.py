"""
init_db.py
==========
Database initialization script that applies schema migration and system user setup.

Usage:
    python -m src.db.init_db
"""

from src.db.migrate import migrate
from src.db.migrate_system_user import migrate as migrate_system_user

def init_db() -> None:
    print("[INIT DB] Running schema migration...")
    migrate()
    print("[INIT DB] Running system user migration...")
    migrate_system_user()
    print("[INIT DB] ✅ Database initialization complete.")

if __name__ == "__main__":
    init_db()
