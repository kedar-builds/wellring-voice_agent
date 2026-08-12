import os
import psycopg2
from dotenv import load_dotenv

load_dotenv('/home/subaru/Documents/wellring-voice_agent/.env')

db_url = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(db_url)
cur = conn.cursor()

# Query all assessments
cur.execute("""
    SELECT 
        a.assessment_id, 
        u.name as user_name, 
        u.phone as user_phone,
        a.intent, 
        a.symptoms, 
        a.severity, 
        a.score, 
        a.risk_level, 
        a.message as summary, 
        a.recording_url, 
        a.transcript, 
        a.bolna_call_id,
        a.assessed_at
    FROM assessments a
    LEFT JOIN users u ON a.user_id = u.user_id
    ORDER BY a.assessed_at DESC;
""")
assessments = cur.fetchall()

print(f"Total assessments found: {len(assessments)}")

for a in assessments:
    aid, uname, uphone, intent, symptoms, severity, score, risk_level, summary, rec_url, transcript, call_id, assessed_at = a
    print("="*80)
    print(f"ASSESSMENT ID: {aid}")
    print(f"User: {uname} ({uphone})")
    print(f"Date: {assessed_at}")
    print(f"Intent: {intent} | Symptoms: {symptoms} | Severity: {severity}")
    print(f"Score: {score} | Risk Level: {risk_level}")
    print(f"Bolna Call ID: {call_id}")
    print(f"Recording URL: {rec_url}")
    print(f"Summary/Message: {summary}")
    print("-" * 40)
    print("TRANSCRIPT:")
    print(transcript)
    print("-" * 40)
    
    # Query conversation turns for this assessment
    cur.execute("""
        SELECT role, content, spoken_at 
        FROM conversations 
        WHERE assessment_id = %s 
        ORDER BY spoken_at ASC;
    """, (aid,))
    turns = cur.fetchall()
    if turns:
        print("CONVERSATION LOG:")
        for role, content, spoken_at in turns:
            print(f"  [{spoken_at}] {role.upper()}: {content}")
    else:
        print("No conversation turns logged in database.")
    print("="*80)
    print("\n")

cur.close()
conn.close()
