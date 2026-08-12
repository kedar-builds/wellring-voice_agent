import os
import psycopg2
import asyncio
from src.storage import upload_recording_to_b2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")

async def main():
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Get the latest assessment
        cur.execute("SELECT assessment_id, recording_url FROM assessments ORDER BY assessed_at DESC LIMIT 1;")
        row = cur.fetchone()
        
        if row and "api.bolna.ai" in row[1]:
            assessment_id, bolna_url = row
            print(f"Found Bolna URL in DB: {bolna_url}")
            
            # Upload to B2
            b2_url = await upload_recording_to_b2(bolna_url, "user")
            print(f"Uploaded to B2: {b2_url}")
            
            # Update DB
            cur.execute("UPDATE assessments SET recording_url = %s WHERE assessment_id = %s;", (b2_url, assessment_id))
            conn.commit()
            print("Database updated successfully.")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
