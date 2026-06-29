from dotenv import load_dotenv

load_dotenv()

from src.notifications import send_whatsapp_alert

recipient = "+918421971145"

print(f"Sending WhatsApp alert to {recipient}...")
response_data = {
    "risk_level": "CRITICAL",
    "score": 236,
    "symptoms": ["Chest Pain", "Breathing Problem"],
    "action": "Notify Caregiver And Emergency Services",
    "steps": [
        "Call emergency services immediately (112 / local emergency number).",
        "Notify registered caregiver via SMS and push notification.",
        "Keep the user calm and on the line.",
        "Do NOT let the user move unless instructed by emergency services."
    ]
}

success = send_whatsapp_alert(
    interaction_id="test_id_123",
    response_data=response_data,
    to_phone=recipient,
    patient_name="the patient",
    caregiver_name="Caregiver"
)
print(f"Success: {success}")
