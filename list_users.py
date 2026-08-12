from src.database import get_db_connection
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("SELECT * FROM users")
print(cursor.fetchall())
