"""
migrate_system_user.py
======================
One-time migration to add the `is_system` column to the `users` table and
insert the singleton Anonymous system user.

Usage:
    python -m src.db.migrate_system_user
"""

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

def migrate() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("[MIGRATE] ❌ DATABASE_URL is not set. Aborting.", file=sys.stderr)
        sys.exit(1)

    print("[MIGRATE] Connecting to PostgreSQL …")
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    
    with conn.cursor() as cursor:
        print("[MIGRATE] Adding is_system column if it doesn't exist...")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;")
        
        print("[MIGRATE] Creating unique index for system user...")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_system ON users(is_system) WHERE is_system = TRUE;")
        
        print("[MIGRATE] Upserting Anonymous system user...")
        cursor.execute("""
            INSERT INTO users (name, role, email, is_system)
            VALUES ('Anonymous', 'elderly', 'anonymous@wellring.internal', TRUE)
            ON CONFLICT (is_system) WHERE is_system = TRUE 
            DO UPDATE SET email = EXCLUDED.email;
        """)

    conn.close()
    print("[MIGRATE] ✅ System user migration applied successfully.")

if __name__ == "__main__":
    migrate()
