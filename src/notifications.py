"""
notifications.py
================
WhatsApp & SMS alerts for WellRing caregivers.

When an elderly user's health assessment comes in at HIGH or CRITICAL risk,
we immediately send a WhatsApp message to their child / caregiver using
Twilio's WhatsApp API (sandbox for dev, production for live).

Flow:
  /assess  →  trigger_alerts_if_needed()  →  send_whatsapp_alert()  →  Twilio → WhatsApp

Environment variables (set in .env):
    USE_TWILIO          = true  (enable real sending)
    USE_WHATSAPP        = true  (use WhatsApp channel instead of SMS)
    TWILIO_ACCOUNT_SID  = ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_AUTH_TOKEN   = your_auth_token
    TWILIO_FROM_PHONE   = +14155238886  (Twilio sandbox WhatsApp number)
    CAREGIVER_PHONE     = +91xxxxxxxxxx (default fallback caregiver number)
"""

import logging
import os
import datetime
from typing import Dict, Any, Optional
from src.database import log_alert, get_family_contacts
from src.users import get_caregiver_phone, get_user

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credentials — loaded from environment (never hardcoded)
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_PHONE  = os.environ.get("TWILIO_FROM_PHONE", "+14155238886")
CAREGIVER_PHONE    = os.environ.get("CAREGIVER_PHONE", "")
USE_TWILIO         = os.environ.get("USE_TWILIO", "false").lower() == "true"
USE_WHATSAPP       = os.environ.get("USE_WHATSAPP", "false").lower() == "true"

DASHBOARD_URL = "https://wellring-frontend.vercel.app"


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _risk_emoji(risk_level: str) -> str:
    return {
        "LOW":      "🟢",
        "MEDIUM":   "🟡",
        "HIGH":     "🔴",
        "CRITICAL": "🆘",
    }.get(risk_level.upper(), "⚠️")


def build_alert_message(
    patient_name: str,
    risk_level: str,
    score: int,
    symptoms: list,
    action: str,
    steps: list,
    caregiver_name: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """
    Build a clear, human-readable WhatsApp message for the caregiver.
    Example output:

    🔴 WellRing Health Alert

    Hi Ramani,

    Your elderly patient *Atharva* just had a health check-in with our AI
    assistant, Riley. Here is what was reported:

    🩺 *Risk Level:* HIGH (Score: 75)
    💊 *Symptoms:* Fever, Dizziness
    📋 *Recommended Action:* Notify caregiver & monitor

    *Next Steps:*
    1. Check on Atharva immediately
    2. Take temperature every 2 hours
    3. Ensure they drink plenty of fluids

    ⏰ Reported at: 11:02 AM, 17 Jun 2026

    View full report: https://wellring-frontend.vercel.app
    — WellRing Team
    """
    emoji = _risk_emoji(risk_level)
    sym_str = ", ".join(s.replace("_", " ").title() for s in symptoms) if symptoms else "None reported"
    
    greeting = f"Hi {caregiver_name}," if caregiver_name else "Hello,"
    
    ts = timestamp or datetime.datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    steps_block = ""
    if steps:
        steps_block = "\n*Next Steps:*\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps[:4]))

    action_clean = action.replace("_", " ").title()

    msg = (
        f"{emoji} *WellRing Health Alert*\n\n"
        f"{greeting}\n\n"
        f"Your elderly patient *{patient_name}* just had a health check-in with Riley (WellRing AI). "
        f"Here is what was reported:\n\n"
        f"🩺 *Risk Level:* {risk_level} (Score: {score})\n"
        f"💊 *Symptoms:* {sym_str}\n"
        f"📋 *Action:* {action_clean}"
        f"{steps_block}\n\n"
        f"⏰ Reported at: {ts}\n\n"
        f"👉 View full report: {DASHBOARD_URL}\n"
        f"— WellRing Team"
    )
    return msg


def build_routine_update_message(
    patient_name: str,
    caregiver_name: Optional[str] = None,
    symptoms: Optional[list] = None,
    risk_level: str = "LOW",
    timestamp: Optional[str] = None,
) -> str:
    """
    Routine (LOW/MEDIUM) check-in update for caregivers.
    Less urgent tone, just a daily summary.
    """
    greeting = f"Hi {caregiver_name}," if caregiver_name else "Hello,"
    emoji = _risk_emoji(risk_level)
    ts = timestamp or datetime.datetime.now().strftime("%I:%M %p, %d %b %Y")

    if symptoms:
        sym_str = ", ".join(s.replace("_", " ").title() for s in symptoms)
        health_line = f"Yeah, {patient_name} has minor complaints ({sym_str}) but is generally fine and doing well."
    else:
        health_line = f"Yeah, {patient_name} is fine and doing well."

    msg = (
        f"{emoji} *WellRing Daily Update*\n\n"
        f"{greeting}\n\n"
        f"*{patient_name}* just completed their daily wellness check-in with Riley.\n\n"
        f"{health_line}\n\n"
        f"⏰ Check-in time: {ts}\n\n"
        f"👉 Full history: {DASHBOARD_URL}\n"
        f"— WellRing Team"
    )
    return msg


# ---------------------------------------------------------------------------
# Twilio send
# ---------------------------------------------------------------------------

def _twilio_send(to_phone: str, body: str, notification_type: str = "whatsapp") -> bool:
    """
    Internal: dispatch a message via Twilio.
    Handles WhatsApp and SMS channels.
    Returns True on success, False on failure.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logger.warning("[TWILIO] Credentials not configured — skipping send.")
        return False

    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        if USE_WHATSAPP or notification_type == "whatsapp":
            from_num = (
                TWILIO_FROM_PHONE
                if TWILIO_FROM_PHONE.startswith("whatsapp:")
                else f"whatsapp:{TWILIO_FROM_PHONE}"
            )
            to_num = (
                to_phone
                if to_phone.startswith("whatsapp:")
                else f"whatsapp:{to_phone}"
            )
        else:
            from_num = TWILIO_FROM_PHONE
            to_num   = to_phone

        message = client.messages.create(body=body, from_=from_num, to=to_num)
        logger.info(f"[TWILIO] Sent to {to_phone} | SID: {message.sid}")
        return True

    except Exception as exc:
        logger.error(f"[TWILIO] Send failed to {to_phone}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_whatsapp_alert(
    interaction_id,
    response_data: dict,
    to_phone: str,
    patient_name: str = "the patient",
    caregiver_name: Optional[str] = None,
) -> bool:
    """
    Send a WhatsApp alert to the caregiver for HIGH / CRITICAL events.
    Falls back to a mock log if USE_TWILIO is false (safe for dev/testing).
    """
    risk_level = response_data.get("risk_level", "UNKNOWN")
    score      = response_data.get("score", 0)
    symptoms   = response_data.get("symptoms", [])
    action     = response_data.get("action", "monitor")
    steps      = response_data.get("steps", [])

    body = build_alert_message(
        patient_name=patient_name,
        risk_level=risk_level,
        score=score,
        symptoms=symptoms,
        action=action,
        steps=steps,
        caregiver_name=caregiver_name,
    )

    sent = False
    if USE_TWILIO:
        sent = _twilio_send(to_phone, body)
    else:
        logger.info(f"[WHATSAPP MOCK → {to_phone}]\n{body}")
        sent = True  # treat mock as success so tests pass

    log_alert(
        interaction_id=interaction_id,
        timestamp=datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z",
        risk_level=risk_level,
        notification_type="whatsapp" if USE_WHATSAPP else "sms",
        status="sent" if sent else "failed",
        recipient_phone=to_phone,
        recipient_name=caregiver_name,
    )
    return sent


def send_unanswered_call_alert(
    to_phone: str,
    patient_name: str = "the patient",
    caregiver_name: Optional[str] = None,
) -> bool:
    """
    Send a WhatsApp alert to the caregiver when the patient misses an automated call.
    """
    greeting = f"Hi {caregiver_name}," if caregiver_name else "Hello,"
    ts = datetime.datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    body = (
        f"⚠️ *WellRing Missed Call Alert*\n\n"
        f"{greeting}\n\n"
        f"Riley tried to call *{patient_name}* for their scheduled check-in at {ts}, but they did not answer the phone.\n\n"
        f"Please try checking on them when you get a chance.\n\n"
        f"— WellRing Team"
    )

    sent = False
    if USE_TWILIO:
        sent = _twilio_send(to_phone, body)
    else:
        logger.info(f"[WHATSAPP MOCK → {to_phone}]\n{body}")
        sent = True

    return sent


def send_sms_alert(interaction_id, response_data: dict, to_phone: str) -> bool:
    """
    Legacy entry-point kept for backward compatibility.
    Routes to send_whatsapp_alert when USE_WHATSAPP=true.
    """
    return send_whatsapp_alert(interaction_id, response_data, to_phone)


def trigger_alerts_if_needed(
    interaction_id,
    response_data: dict,
    user_id: Optional[str] = None,
) -> None:
    """
    Called after every /assess.
    Sends WhatsApp to the user's registered caregiver for HIGH/CRITICAL.
    Also sends a quieter routine update for LOW/MEDIUM if USE_ROUTINE_UPDATES=true.
    """
    risk_level = response_data.get("risk_level", "LOW")

    # Resolve patient & caregiver info
    patient_name   = "the patient"
    family_contacts = []
    user = None  # define before conditional so fallback block can reference it
    
    if user_id:
        user = get_user(user_id)
        if user:
            patient_name = user.get("name", patient_name)
        
        # Fetch all family contacts
        contacts = get_family_contacts(user_id)
        for contact in contacts:
            if contact.get("phone"):
                family_contacts.append({
                    "name": contact.get("name"),
                    "phone": contact.get("phone")
                })
                
    # Fallback to caregiver phone if no family contacts found
    if not family_contacts:
        caregiver_phone = get_caregiver_phone(user_id, CAREGIVER_PHONE)
        caregiver_name = None
        if user:  # safe — always defined above
            caregiver_name = user.get("caregiver_name") or None
            if user.get("caregiver_phone"):
                caregiver_phone = user["caregiver_phone"]
        
        # Last resort — always use the env var CAREGIVER_PHONE
        if not caregiver_phone:
            caregiver_phone = CAREGIVER_PHONE
        
        if caregiver_phone:
            family_contacts.append({"name": caregiver_name, "phone": caregiver_phone})

    if not family_contacts:
        logger.warning("[NOTIFY] No caregiver/family phone found — skipping alert.")
        return

    if risk_level in ("HIGH", "CRITICAL"):
        for contact in family_contacts:
            phone = contact["phone"]
            name = contact["name"]
            logger.info(f"[NOTIFY] {risk_level} alert → {phone}")
            send_whatsapp_alert(
                interaction_id=interaction_id,
                response_data=response_data,
                to_phone=phone,
                patient_name=patient_name,
                caregiver_name=name,
            )

    else:
        # Routine update for LOW/MEDIUM check-ins — always notify family
        # that the elder answered and completed the check-in.
        for contact in family_contacts:
            phone = contact["phone"]
            name = contact["name"]
            logger.info(f"[NOTIFY] {risk_level} routine update → {phone}")
            body = build_routine_update_message(
                patient_name=patient_name,
                caregiver_name=name,
                symptoms=response_data.get("symptoms", []),
                risk_level=risk_level,
            )
            sent = False
            if USE_TWILIO:
                sent = _twilio_send(phone, body)
            else:
                logger.info(f"[ROUTINE MOCK → {phone}]\n{body}")
                sent = True  # treat mock as success

            # Always log the routine notification so it's traceable
            log_alert(
                interaction_id=interaction_id,
                timestamp=datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z",
                risk_level=risk_level,
                notification_type="whatsapp" if USE_WHATSAPP else "sms",
                status="sent" if sent else "failed",
                recipient_phone=phone,
                recipient_name=name,
            )


def send_whatsapp_reminder(to_phone: str, body: str) -> bool:
    """
    Send a WhatsApp reminder (called by the scheduler for medicine / checkup reminders).
    """
    if USE_TWILIO:
        return _twilio_send(to_phone, body)
    else:
        logger.info(f"[REMINDER MOCK → {to_phone}]\n{body}")
        return True


def send_test_whatsapp(to_phone: str, patient_name: str = "Atharva") -> dict:
    """
    Send a test WhatsApp message to verify the Twilio integration.
    Called by POST /test-whatsapp.
    """
    body = (
        f"✅ *WellRing WhatsApp Test*\n\n"
        f"Hello! This is a test message from WellRing.\n\n"
        f"If you receive this, WhatsApp alerts are working correctly.\n"
        f"You will get notifications here whenever *{patient_name}* "
        f"has a health check-in.\n\n"
        f"— WellRing Team 🏥"
    )

    if USE_TWILIO:
        success = _twilio_send(to_phone, body)
        return {
            "sent": success,
            "channel": "whatsapp" if USE_WHATSAPP else "sms",
            "to": to_phone,
            "message_preview": body[:100] + "..."
        }
    else:
        logger.info(f"[TEST MOCK → {to_phone}]\n{body}")
        return {
            "sent": False,
            "channel": "mock",
            "to": to_phone,
            "note": "USE_TWILIO=false — set to true with real credentials to actually send",
            "message_preview": body[:100] + "..."
        }
