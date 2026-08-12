import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SELECT user_id, name, phone, caregiver_phone, caregiver_name FROM users;")
for row in cur.fetchall():
    print(row)
conn.close()
