import os
import sys
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("DATABASE_URL not set")
    sys.exit(1)

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("""
        SELECT assessment_id, bolna_call_id, transcript, message, steps, breakdown, intent, risk_level, action
        FROM assessments
        ORDER BY assessed_at DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        print("=== Most Recent Assessment ===")
        print(f"Assessment ID: {row['assessment_id']}")
        print(f"Bolna Call ID: {row['bolna_call_id']}")
        print(f"Risk Level: {row['risk_level']}")
        print(f"Intent: {row['intent']}")
        print(f"Action: {row['action']}")
        print("\n--- Summary ---")
        print(f"Message: {row['message']}")
        print(f"Steps: {row['steps']}")
        print(f"Breakdown: {row['breakdown']}")
        print("\n--- Transcript ---")
        print(row['transcript'])
    else:
        print("No assessments found.")
    
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error connecting to database: {e}")
