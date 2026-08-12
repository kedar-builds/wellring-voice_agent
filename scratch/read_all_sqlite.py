import sqlite3

def main():
    conn = sqlite3.connect("wellring.db")
    cur = conn.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]
    
    for table in tables:
        cur.execute(f"SELECT * FROM {table};")
        rows = cur.fetchall()
        print(f"Table '{table}' has {len(rows)} rows:")
        for r in rows:
            print(r)
        print("="*40)
        
    conn.close()

if __name__ == "__main__":
    main()
