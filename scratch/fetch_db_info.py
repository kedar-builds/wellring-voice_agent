import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("DATABASE_URL not set")
    exit(1)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

print("--- USERS ---")
cur.execute("SELECT user_id, name, phone, age FROM users;")
for r in cur.fetchall():
    print(r)

print("\n--- ASSESSMENTS ---")
cur.execute("SELECT assessment_id, assessed_at, severity, risk_level, recording_url, transcript FROM assessments;")
for r in cur.fetchall():
    print(r)

conn.close()
