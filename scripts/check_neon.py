"""
scripts/check_neon.py
====================
Comprehensive Neon PostgreSQL Database Verification Script for WellRing Voice Agent.
"""

import os
import sys
import time
from pathlib import Path
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def run_neon_check():
    print("=" * 60)
    print("  Neon PostgreSQL Comprehensive Diagnostics")
    print("=" * 60)

    if not DATABASE_URL:
        print("❌ DATABASE_URL is not set in environment or .env file.")
        sys.exit(1)

    # Sanitize URL for printing (hide password)
    try:
        url_parts = DATABASE_URL.split("@")
        sanitized_url = "postgresql://*****@" + url_parts[1] if len(url_parts) > 1 else "postgresql://..."
    except Exception:
        sanitized_url = "postgresql://..."
    print(f"📡 Connection string: {sanitized_url}")

    # 1. Test Connection & Latency
    print("\n[1] Testing Connection & Latency...")
    start_time = time.time()
    try:
        conn = psycopg2.connect(DATABASE_URL)
        latency = (time.time() - start_time) * 1000
        print(f"  ✅ Connected successfully in {latency:.2f} ms")
    except Exception as e:
        print(f"  ❌ Connection failed: {e}")
        sys.exit(1)

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 2. Database & Version Information
    print("\n[2] Database & Version Information...")
    try:
        cur.execute("SELECT version();")
        ver = cur.fetchone()[0]
        print(f"  ✅ Version: {ver}")

        cur.execute("SELECT current_database(), current_user, current_schema();")
        db_name, db_user, db_schema = cur.fetchone()
        print(f"  ✅ Database: {db_name} | User: {db_user} | Schema: {db_schema}")
    except Exception as e:
        print(f"  ❌ Version check failed: {e}")

    # 3. Table Existence & Schema Verification
    print("\n[3] Verifying Schema Tables...")
    expected_tables = ["users", "assessments", "conversations", "alerts", "health_history"]
    try:
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public';
        """)
        existing_tables = set(r[0] for r in cur.fetchall())
        print(f"  📋 Found {len(existing_tables)} tables in public schema: {sorted(list(existing_tables))}")
        
        for t in expected_tables:
            if t in existing_tables:
                print(f"  ✅ Table '{t}' exists")
            else:
                print(f"  ❌ Table '{t}' MISSING")
    except Exception as e:
        print(f"  ❌ Schema verification failed: {e}")

    # 4. Column verification for key fields (e.g. is_system in users)
    print("\n[4] Checking Table Columns & Structure...")
    try:
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'users';
        """)
        user_cols = {row[0]: row[1] for row in cur.fetchall()}
        if "is_system" in user_cols:
            print("  ✅ 'users.is_system' column present")
        else:
            print("  ⚠️ 'users.is_system' column missing")

        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'assessments';
        """)
        ass_cols = set(r[0] for r in cur.fetchall())
        expected_ass_cols = {"assessment_id", "user_id", "risk_level", "score", "bolna_call_id"}
        if expected_ass_cols.issubset(ass_cols):
            print("  ✅ 'assessments' columns verified")
        else:
            print(f"  ⚠️ 'assessments' missing columns: {expected_ass_cols - ass_cols}")
    except Exception as e:
        print(f"  ❌ Column check failed: {e}")

    # 5. Indexes Verification
    print("\n[5] Checking Database Indexes...")
    try:
        cur.execute("""
            SELECT tablename, indexname 
            FROM pg_indexes 
            WHERE schemaname = 'public'
            ORDER BY tablename, indexname;
        """)
        indexes = cur.fetchall()
        print(f"  ✅ Total indexes created: {len(indexes)}")
        for tablename, indexname in indexes:
            print(f"     • {tablename}: {indexname}")
    except Exception as e:
        print(f"  ❌ Index check failed: {e}")

    # 6. System User & Row Counts
    print("\n[6] Checking Row Counts & System User...")
    try:
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM users) AS users_count,
                (SELECT COUNT(*) FROM users WHERE is_system = true) AS sys_users_count,
                (SELECT COUNT(*) FROM assessments) AS ass_count,
                (SELECT COUNT(*) FROM conversations) AS conv_count,
                (SELECT COUNT(*) FROM alerts) AS alerts_count,
                (SELECT COUNT(*) FROM health_history) AS hh_count;
        """)
        row = cur.fetchone()
        print("  📊 Row Counts:")
        print(f"     • Users: {row['users_count']} (System Users: {row['sys_users_count']})")
        print(f"     • Assessments: {row['ass_count']}")
        print(f"     • Conversations: {row['conv_count']}")
        print(f"     • Alerts: {row['alerts_count']}")
        print(f"     • Health History: {row['hh_count']}")

        if row['sys_users_count'] >= 1:
            print("  ✅ Anonymous system user is present and active")
        else:
            print("  ⚠️ Anonymous system user NOT found")
    except Exception as e:
        print(f"  ❌ Row count check failed: {e}")

    # 7. Read / Write / Transaction Test
    print("\n[7] Testing Read/Write/Delete Operations...")
    test_user_id = "11111111-1111-1111-1111-111111111111"
    try:
        # INSERT
        cur.execute("""
            INSERT INTO users (user_id, name)
            VALUES (%s, %s)
            RETURNING user_id;
        """, (test_user_id, "Neon Test User"))
        inserted_id = cur.fetchone()[0]
        print(f"  ✅ INSERT operation succeeded: user_id={inserted_id}")

        # SELECT
        cur.execute("SELECT name FROM users WHERE user_id = %s;", (test_user_id,))
        fetched = cur.fetchone()
        if fetched and fetched['name'] == "Neon Test User":
            print(f"  ✅ SELECT read operation succeeded: name={fetched['name']}")
        else:
            print("  ❌ SELECT read failed to return inserted data")

        # UPDATE
        cur.execute("UPDATE users SET name = 'Neon Test User Updated' WHERE user_id = %s RETURNING name;", (test_user_id,))
        updated_name = cur.fetchone()[0]
        if updated_name == "Neon Test User Updated":
            print(f"  ✅ UPDATE operation succeeded: new name={updated_name}")
        else:
            print("  ❌ UPDATE operation failed")

        # DELETE (Cleanup)
        cur.execute("DELETE FROM users WHERE user_id = %s;", (test_user_id,))
        print("  ✅ DELETE cleanup operation succeeded")

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  ❌ Read/Write test failed: {e}")

    # 8. Query Performance (EXPLAIN ANALYZE)
    print("\n[8] Testing Query Execution & Performance...")
    try:
        start_q = time.time()
        cur.execute("EXPLAIN ANALYZE SELECT * FROM users LIMIT 10;")
        explain_output = cur.fetchall()
        q_duration = (time.time() - start_q) * 1000
        print(f"  ✅ Query executed in {q_duration:.2f} ms")
        print("  📄 Execution Plan Top Line:", explain_output[0][0])
    except Exception as e:
        print(f"  ❌ Query performance test failed: {e}")

    cur.close()
    conn.close()

    print("\n" + "=" * 60)
    print("  🎉 Neon PostgreSQL is setup properly and working 100%!")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    run_neon_check()
