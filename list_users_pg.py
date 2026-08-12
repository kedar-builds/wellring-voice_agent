from src.database import get_pg_conn, _pg_cursor

def list_all_users():
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("SELECT * FROM users")
            users = cur.fetchall()
            for u in users:
                # Convert datetime and UUID objects to strings for printing
                u = dict(u)
                print(u)

if __name__ == "__main__":
    list_all_users()
