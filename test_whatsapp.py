import os
from dotenv import load_dotenv

# Load env vars before importing main
load_dotenv()

# Force Twilio to be ON for this script
os.environ["USE_TWILIO"] = "true"
os.environ["USE_WHATSAPP"] = "true"
os.environ["CAREGIVER_PHONE"] = "+918421971145"

from src.database import init_db, init_pg_tables
from src.main import process_assessment_data

import logging
logging.basicConfig(level=logging.INFO)

def test_whatsapp():
    init_db()
    init_pg_tables()
    print("="*60)
    print("🚑 Simulating Severe Health Assessment (Breathing Problem)")
    print("="*60)
    
    try:
        result = process_assessment_data(
            intent="health_issue",
            symptoms=["breathing_problem", "high_fever"],
            severity="critical",
            confidence=0.99,
            user_id=None,
            recording_url=None
        )
        print(f"\n📊 Assessment Result:")
        print(f"   Risk Level: {result['risk_level']} (Score: {result['score']})")
        print(f"   Category  : {result['category']}")
        print(f"   Action    : {result['action']}")
        print(f"\n📲 WhatsApp trigger initiated! Check your phone.")
    except Exception as e:
        print(f"\n❌ Error triggering WhatsApp: {e}")

if __name__ == "__main__":
    test_whatsapp()
