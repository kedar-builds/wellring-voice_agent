import sqlite3

def main():
    conn = sqlite3.connect("wellring.db")
    cur = conn.cursor()
    
    # List all tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]
    print("Tables:", tables)
    
    # Query each table's schema and content count
    for table in tables:
        cur.execute(f"PRAGMA table_info({table});")
        columns = [c[1] for c in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        count = cur.fetchone()[0]
        print(f"Table '{table}': {count} rows. Columns: {columns}")
        
        # If it has logs or audits in name, print some rows
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
