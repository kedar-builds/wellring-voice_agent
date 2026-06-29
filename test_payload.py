from src.main import VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_PHONE
import json

payload_phone = "+918421971145"
user_name = "Test User"
dynamic_prompt = "..."

vapi_payload = {
    "assistantId": VAPI_ASSISTANT_ID,
    "assistantOverrides": {
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "systemPrompt": dynamic_prompt
        },
        "firstMessage": "Hello"
    },
    "customer": {
        "number": payload_phone,
        "name": user_name
    }
}
if VAPI_PHONE_NUMBER_ID:
    vapi_payload["phoneNumberId"] = VAPI_PHONE_NUMBER_ID
elif TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_PHONE:
    vapi_payload["phoneNumber"] = {
        "twilioPhoneNumber": TWILIO_FROM_PHONE,
        "twilioAccountSid": TWILIO_ACCOUNT_SID,
        "twilioAuthToken": TWILIO_AUTH_TOKEN,
    }

print(json.dumps(vapi_payload, indent=2))
