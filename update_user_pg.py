from src.database import get_pg_conn, _pg_cursor

def update_user_phone():
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute("""
                UPDATE users 
                SET phone = '+918421971145', caregiver_phone = '+919082487585' 
                WHERE user_id = 'b286e754-6603-4676-8438-2543f576a4a9'
            """)
        conn.commit()

if __name__ == "__main__":
    update_user_phone()
