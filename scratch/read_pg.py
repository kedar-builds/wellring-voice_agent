import os
import psycopg2
from dotenv import load_dotenv

def main():
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env")
        return
        
    print("Connecting to:", db_url)
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    # Get all tables
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public';
    """)
    tables = [r[0] for r in cur.fetchall()]
    print("Postgres Tables:", tables)
    
    # Query each table
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        count = cur.fetchone()[0]
        print(f"Table '{table}': {count} rows")
        
        # If it has log or audit or watchdog in name, print some rows
        if "log" in table or "audit" in table or "watchdog" in table:
            cur.execute(f"SELECT * FROM {table} LIMIT 10;")
            rows = cur.fetchall()
            print(f"--- Rows from {table} ---")
            for r in rows:
                print(r)
            print("-" * 30)
            
    conn.close()

if __name__ == "__main__":
    main()
