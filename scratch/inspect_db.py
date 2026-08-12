import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get("DATABASE_URL")
print("DATABASE_URL:", db_url)

if not db_url:
    print("Error: DATABASE_URL not set in .env")
    exit(1)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

print("\n--- Tables ---")
cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
for row in cur.fetchall():
    print(row)

print("\n--- Users (first 10) ---")
cur.execute("SELECT user_id, name, phone, email, clerk_id, is_system FROM users LIMIT 10")
for row in cur.fetchall():
    print(row)

print("\n--- Assessments (first 10) ---")
cur.execute("SELECT assessment_id, user_id, bolna_call_id, recording_url, assessed_at FROM assessments LIMIT 10")
for row in cur.fetchall():
    print(row)

cur.close()
conn.close()
