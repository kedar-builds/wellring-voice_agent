from src.main import process_assessment_data
import os

os.environ["USE_TWILIO"] = "true"
os.environ["USE_WHATSAPP"] = "true"

print("Triggering emergency health assessment (chest_pain) to test WhatsApp...")

try:
    response = process_assessment_data(
        intent="health_issue",
        symptoms=["chest_pain"],
        severity="low",
        confidence=1.0,  # Ensure no discount on score
        user_id=None,
        recording_url=None
    )

    print(f"\nScore: {response['score']}")
    print(f"Risk Level: {response['risk_level']}")
    print(f"Action: {response['action']}")
    print("\nWhatsApp trigger initiated! Check your phone.")
    
except Exception as e:
    print(f"Error triggering alert: {e}")
