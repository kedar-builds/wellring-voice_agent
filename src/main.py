"""
main.py
=======
WellRing Voice Agent — FastAPI backend.

Exposes the scoring and alert engine over HTTP so that Kedar's voice
pipeline can send parsed LLM output and receive a structured risk
assessment and escalation action.

Run locally:
    uvicorn src.main:app --reload --port 8000

Interactive docs:
    http://localhost:8000/docs
"""

# Load .env file before anything else reads os.environ
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Security, Depends, status, Request, UploadFile, File, Form
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
import datetime
import os
import asyncio
import logging
import sqlite3
import json
import httpx

logger = logging.getLogger(__name__)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def get_api_key(api_key_header: str = Security(api_key_header)):
    expected_key = os.environ.get("WELLRING_API_KEY", "***REMOVED***")
    if api_key_header == expected_key:
        return api_key_header
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key"
    )

from src.scoring_engine import calculate_score, determine_action, SYMPTOM_WEIGHTS
from src.database import (
    init_db, log_interaction, get_symptom_repeat_count,
    add_reminder, get_reminders, delete_reminder, update_reminder_trigger,
    get_assessments_list, get_assessment_stats, get_user_health_context,
    upsert_user_profile, get_user_profile, upsert_family_contacts, get_family_contacts, delete_family_contact,
    add_single_family_contact
)
from src.notifications import trigger_alerts_if_needed, send_whatsapp_reminder, send_test_whatsapp

# ---------------------------------------------------------------------------
# Background Reminder Scheduler
# ---------------------------------------------------------------------------

async def run_reminder_scheduler():
    logger.info("Starting background reminder scheduler...")
    while True:
        try:
            await asyncio.sleep(15)  # check every 15 seconds for responsive testing
            reminders = get_reminders()
            if not reminders:
                continue
                
            ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now = datetime.datetime.now(ist_tz)
            current_time_str = now.strftime("%H:%M")
            current_date_str = now.strftime("%Y-%m-%d")
            
            for reminder in reminders:
                rem_id = reminder["id"]
                rem_type = reminder["type"]
                rem_title = reminder["title"]
                rem_time = reminder["time"]
                rem_freq = reminder["frequency"]
                rem_phone = reminder["phone"]
                rem_notes = reminder["notes"] or ""
                last_trig = reminder["last_triggered"]
                
                should_trigger = False
                trigger_timestamp = ""
                
                if rem_freq == "once" or "T" in rem_time:
                    try:
                        # e.g., "2026-06-10T14:30"
                        rem_dt = datetime.datetime.fromisoformat(rem_time.replace("Z", ""))
                        if rem_dt.tzinfo is None:
                            rem_dt = rem_dt.replace(tzinfo=ist_tz)
                        if now >= rem_dt and not last_trig:
                            should_trigger = True
                            trigger_timestamp = now.isoformat()
                    except Exception as e:
                        logger.error(f"Error parsing date {rem_time}: {e}")
                else:
                    if current_time_str == rem_time:
                        if rem_freq == "daily":
                            if last_trig != current_date_str:
                                should_trigger = True
                                trigger_timestamp = current_date_str
                        elif rem_freq == "monthly":
                            current_month = now.strftime("%Y-%m")
                            if last_trig != current_month:
                                should_trigger = True
                                trigger_timestamp = current_month
                        elif rem_freq == "yearly":
                            current_year = now.strftime("%Y")
                            if last_trig != current_year:
                                should_trigger = True
                                trigger_timestamp = current_year
                
                if should_trigger:
                    logger.info(f"Triggering reminder {rem_id} ({rem_title}) for {rem_phone}")
                    if rem_type == "call":
                        body = f"📞 WellRing check-in call is ringing you now..."
                        send_whatsapp_reminder(rem_phone, body)
                        try:
                            req = CallRequest(phone=rem_phone, user_name=None)
                            await initiate_call(req, api_key="internal")
                        except Exception as e:
                            logger.error(f"Error initiating voice call for reminder: {e}")
                        update_reminder_trigger(rem_id, trigger_timestamp)
                    else:
                        if rem_type == "medicine":
                            body = f"💊 WellRing Medicine Reminder:\nPlease take {rem_title}.\nNotes: {rem_notes}"
                        elif rem_type == "checkup":
                            body = f"🏥 WellRing Health Checkup Reminder:\nYou have '{rem_title}' scheduled.\nNotes: {rem_notes}"
                        else:
                            body = f"⏰ WellRing Reminder: {rem_title}.\nNotes: {rem_notes}"
                        
                        success = send_whatsapp_reminder(rem_phone, body)
                        if success:
                            update_reminder_trigger(rem_id, trigger_timestamp)
        except asyncio.CancelledError:
            logger.info("Reminder scheduler task cancelled.")
            break
        except Exception as ex:
            logger.error(f"Error in reminder scheduler: {ex}")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

scheduler_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler_task
    init_db()
    scheduler_task = asyncio.create_task(run_reminder_scheduler())
    yield
    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="WellRing Health Risk API",
    description=(
        "Receives voice-extracted health data from the LLM pipeline and "
        "returns a risk score, risk level, and escalation action plan."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class AssessRequest(BaseModel):
    """
    Payload sent by Kedar's LLM module after parsing the user's speech.
    """
    intent: str = Field(
        ...,
        description="Intent extracted from speech. e.g. 'health_issue', 'general_query'",
        examples=["health_issue"],
    )
    symptoms: List[str] = Field(
        default=[],
        description=(
            "Symptom identifiers extracted from the user's speech. "
            "Valid keys: dizziness, fever, medicine_missed, fall_detected, "
            "chest_pain, breathing_problem, unconscious, stroke_symptoms."
        ),
        examples=[["chest_pain", "breathing_problem"]],
    )
    severity: str = Field(
        ...,
        description="Overall severity label: low | medium | high | critical",
        examples=["high"],
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="LLM confidence in its symptom extraction, range [0.0, 1.0]",
        examples=[0.95],
    )
    user_id: Optional[str] = Field(None, description="UUID of the user (patient)")
    recording_url: Optional[str] = Field(None, description="URL to the audio recording of the assessment")

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "critical"}
        if v.lower().strip() not in allowed:
            raise ValueError(
                f"severity must be one of {sorted(allowed)}, got '{v}'"
            )
        return v.lower().strip()


class AssessResponse(BaseModel):
    """
    Full risk assessment returned to the voice pipeline.
    """
    # --- Score info ---
    score: int         = Field(..., description="Final risk score after confidence scaling")
    base_score: int    = Field(..., description="Raw score before confidence scaling")
    confidence: float  = Field(..., description="Echoed LLM confidence value")

    # --- Classification ---
    risk_level: str    = Field(..., description="LOW | MEDIUM | HIGH | CRITICAL")
    category: str      = Field(..., description="Primary clinical category (e.g. CARDIAC)")
    symptoms: List[str]= Field(..., description="Recognised symptom keys only")
    severity: str      = Field(..., description="Normalised severity label")

    # --- Escalation ---
    action: str        = Field(..., description="Escalation action identifier")
    message: str       = Field(..., description="Human-readable summary of the action")
    steps: List[str]   = Field(..., description="Ordered list of escalation steps")

    # --- Explainability ---
    breakdown: List[str] = Field(..., description="Per-component score breakdown for explainability")

    # --- Meta ---
    timestamp: str     = Field(..., description="ISO 8601 UTC timestamp of the assessment")
    recording_url: Optional[str] = Field(None, description="URL to the audio recording if provided")


class HealthResponse(BaseModel):
    status: str
    version: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_model=HealthResponse, tags=["Health"])
def root():
    """Health check — confirms the API is running."""
    return HealthResponse(status="ok", version="1.0.0")


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health():
    """Alias health check endpoint."""
    return HealthResponse(status="ok", version="1.0.0")


@app.post("/assess", tags=["Risk Assessment"])
async def assess(request: Request, api_key: str = Depends(get_api_key)):
    """
    Core endpoint. Accepts the LLM-parsed voice input (either flat or wrapped in Vapi's webhook format)
    and returns a health risk assessment with escalation steps.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Check if this is a Vapi tool call request
    is_vapi = False
    tool_call_id = None
    
    # Check for Vapi tool-calls format
    if isinstance(body, dict) and "message" in body:
        msg = body["message"]
        if isinstance(msg, dict) and msg.get("type") in ("tool-calls", "function-call"):
            is_vapi = True
            tool_calls = msg.get("toolCalls", [])
            if not tool_calls and "call" in msg:
                tool_calls = [msg.get("call")]
            
            if tool_calls:
                first_call = tool_calls[0]
                tool_call_id = first_call.get("id")
                func = first_call.get("function", {})
                args = func.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                
                # Extract fields from Vapi tool call arguments
                intent = args.get("intent", "health_issue")
                symptoms = args.get("symptoms", [])
                severity = args.get("severity", "medium")
                confidence = args.get("confidence", 1.0)
                user_id = args.get("user_id")
                recording_url = args.get("recording_url")
            else:
                raise HTTPException(status_code=400, detail="Vapi tool-calls empty")
    else:
        # Standard direct AssessRequest payload
        try:
            payload = AssessRequest(**body)
            intent = payload.intent
            symptoms = payload.symptoms
            severity = payload.severity
            confidence = payload.confidence
            user_id = payload.user_id
            recording_url = payload.recording_url
        except Exception as err:
            raise HTTPException(status_code=422, detail=str(err))

    # Normalize symptoms (Vapi LLM may output symptoms with spaces or minor spelling variations)
    normalized_symptoms = []
    for s in symptoms:
        s_norm = s.lower().strip().replace(" ", "_").replace("-", "_")
        # Direct aliases
        if s_norm == "dizzy":
            s_norm = "dizziness"
        elif s_norm == "fall":
            s_norm = "fall_detected"
        elif s_norm == "stroke":
            s_norm = "stroke_symptoms"
        elif s_norm in ("short_of_breath", "difficulty_breathing", "breathing_difficulty"):
            s_norm = "breathing_problem"
        
        # Add if it matches a valid symptom key in SYMPTOM_WEIGHTS
        if s_norm in SYMPTOM_WEIGHTS:
            normalized_symptoms.append(s_norm)
        else:
            # Check for partial matches
            matched = False
            for valid_key in SYMPTOM_WEIGHTS.keys():
                if valid_key in s_norm or s_norm in valid_key:
                    normalized_symptoms.append(valid_key)
                    matched = True
                    break
            if not matched:
                normalized_symptoms.append(s)

    # Normalize severity
    severity_lower = severity.lower().strip()
    if severity_lower not in {"low", "medium", "high", "critical"}:
        severity_lower = "medium"

    try:
        # Build history counts for each symptom from the last 3 days
        history_counts = {
            s: get_symptom_repeat_count(s, days=3)
            for s in normalized_symptoms
        }

        score_result = calculate_score(
            symptoms=normalized_symptoms,
            severity=severity_lower,
            confidence=confidence,
            history_counts=history_counts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    alert_result = determine_action(score_result["score"], confidence)

    response_data = {
        "score": score_result["score"],
        "base_score": score_result["base_score"],
        "confidence": score_result["confidence"],
        "risk_level": score_result["risk_level"],
        "category": score_result["category"],
        "symptoms": score_result["symptoms"],
        "severity": score_result.get("severity", severity_lower),
        "action": alert_result["action"],
        "message": alert_result["message"],
        "steps": alert_result["steps"],
        "breakdown": score_result["breakdown"],
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "recording_url": recording_url,
    }
    
    # Log interaction to database
    log_data = response_data.copy()
    log_data["intent"] = intent
    log_data["user_id"] = user_id
    interaction_id = log_interaction(log_data)
    
    # Trigger alerts if necessary
    trigger_alerts_if_needed(interaction_id, response_data, user_id)

    # Return structure based on who called it
    if is_vapi:
        return {
            "results": [
                {
                    "toolCallId": tool_call_id,
                    "result": {
                        "score": response_data["score"],
                        "risk_level": response_data["risk_level"],
                        "category": response_data["category"],
                        "action": response_data["action"],
                        "message": response_data["message"],
                        "steps": response_data["steps"]
                    }
                }
            ]
        }
    else:
        return AssessResponse(**response_data)


@app.get("/symptoms", tags=["Reference"])
def list_symptoms():
    """
    Returns the full list of recognised symptom keys and their weights.
    Useful for Kedar's LLM module to know which symptom labels to output.
    """
    from src.scoring_engine.rules import SYMPTOM_WEIGHTS, SYMPTOM_CATEGORIES
    return {
        "symptoms": [
            {
                "key": k,
                "weight": SYMPTOM_WEIGHTS[k],
                "category": SYMPTOM_CATEGORIES.get(k, "UNKNOWN"),
            }
            for k in SYMPTOM_WEIGHTS
        ]
    }


@app.get("/risk-levels", tags=["Reference"])
def list_risk_levels():
    """
    Returns the risk level thresholds and what action each triggers.
    """
    from src.scoring_engine.alerts import _ESCALATION
    from src.scoring_engine.baseline import RiskLevel
    return {
        "levels": [
            {
                "level": level.value,
                "score_range": ranges,
                "action": _ESCALATION[level]["action"],
                "message": _ESCALATION[level]["message"],
            }
            for level, ranges in [
                (RiskLevel.LOW,      "0–30"),
                (RiskLevel.MEDIUM,   "31–60"),
                (RiskLevel.HIGH,     "61–100"),
                (RiskLevel.CRITICAL, "101+"),
            ]
        ]
    }


# ---------------------------------------------------------------------------
# Dashboard Endpoints
# ---------------------------------------------------------------------------

@app.get("/assessments", tags=["Dashboard"])
def get_assessments(limit: int = 50, risk_level: Optional[str] = None, api_key: str = Depends(get_api_key)):
    """Returns recent assessments (interactions) for the dashboard feed."""
    return get_assessments_list(limit=limit, risk_level=risk_level)


@app.get("/assessments/stats", tags=["Dashboard"])
def get_assessment_stats_endpoint(api_key: str = Depends(get_api_key)):
    """Returns counts for dashboard cards."""
    return get_assessment_stats()


@app.get("/patients", tags=["Dashboard"])
def get_patients(api_key: str = Depends(get_api_key)):
    """Hardcoded for demo — replace with real DB later."""
    return [
        {
            "id": 1,
            "name": "Mr. Sharma",
            "age": 72,
            "conditions": ["Hypertension", "Diabetes"],
            "emergency_contact": "+91-9876543210",
            "language": "English",
            "status": "active"
        }
    ]


class ReminderCreate(BaseModel):
    type: str = Field(..., description="call | medicine | checkup")
    title: str = Field(..., description="Title/name of the reminder")
    time: str = Field(..., description="Time (HH:MM) or datetime (ISO string)")
    frequency: str = Field(..., description="daily | monthly | yearly | once")
    phone: str = Field(..., description="WhatsApp phone number")
    notes: Optional[str] = None


@app.get("/reminders", tags=["Reminders"])
def list_reminders(api_key: str = Depends(get_api_key)):
    """Retrieve all reminders."""
    return get_reminders()


@app.post("/reminders", tags=["Reminders"], status_code=status.HTTP_201_CREATED)
def create_reminder(payload: ReminderCreate, api_key: str = Depends(get_api_key)):
    """Create a new reminder schedule."""
    reminder_id = add_reminder(
        type_val=payload.type,
        title=payload.title,
        time_val=payload.time,
        frequency=payload.frequency,
        phone=payload.phone,
        notes=payload.notes
    )
    return {"id": reminder_id, "message": "Reminder scheduled successfully"}


@app.delete("/reminders/{reminder_id}", tags=["Reminders"])
def remove_reminder(reminder_id: int, api_key: str = Depends(get_api_key)):
    """Delete a reminder schedule."""
    success = delete_reminder(reminder_id)
    if not success:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"message": "Reminder deleted successfully"}


# ---------------------------------------------------------------------------
# Outbound Call Endpoint (context-aware)
# ---------------------------------------------------------------------------

BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY", "")
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID", "ab32d1ed-a6ae-4581-a712-c7f5e235f9f0")

BASE_SYSTEM_PROMPT = """You are Riley, a warm and caring health assistant from WellRing calling to check on an elderly patient.

Your role:
- Greet the patient by name if known, then gently ask how they are feeling
- If they had symptoms recently, ask a specific follow-up (e.g. "Yesterday you mentioned fever — has your temperature come down?")
- Listen carefully for any new or worsening symptoms
- If they mention chest pain, difficulty breathing, stroke symptoms, or unconsciousness — immediately say: "This sounds urgent. Please call emergency services at 112 right now. I will also alert your caregiver."
- After talking, call the assess_health_risk tool to log the outcome

Speaking style:
- Speak in short, simple, warm sentences
- Never use medical jargon
- Be patient — they may speak slowly"""


class CallRequest(BaseModel):
    phone: str = Field(..., description="Phone number to call, e.g. +919004261186")
    user_name: Optional[str] = Field(None, description="Override patient name")


@app.post("/call", tags=["Outbound Calls"])
async def initiate_call(payload: CallRequest, api_key: str = Depends(get_api_key)):
    """
    Initiate a context-aware outbound call via Bolna.

    Steps:
      1. Fetch the user's health history from the DB by phone number.
      2. Build a personalised system prompt that references recent symptoms.
      3. POST to Bolna /call with the dynamic agent_prompts override.
    """
    if not BOLNA_API_KEY:
        raise HTTPException(status_code=500, detail="BOLNA_API_KEY not configured")

    # 1. Fetch health context from DB
    ctx = get_user_health_context(payload.phone, days=7)
    user_name = payload.user_name or ctx.get("user_name", "there")

    # 2. Build personalised prompt
    history_block = ""
    if ctx.get("has_history") and ctx.get("summary_lines"):
        lines = ctx["summary_lines"]
        history_block = (
            f"\n\nIMPORTANT — This patient's recent health history:\n"
            + "\n".join(f"  • {line}" for line in lines)
            + "\n\nStart the call by warmly asking a specific follow-up about the most recent symptoms listed above."
        )
    else:
        history_block = (
            "\n\nThis is a routine wellness check. No recent health history found. "
            "Start by asking: 'How have you been feeling lately?'"
        )

    # 2b. Add Medical Context (if available)
    medical_context = ""
    conditions = ctx.get("medical_conditions")
    notes = ctx.get("medical_notes")
    if conditions or notes:
        medical_context = "\n\nPATIENT MEDICAL CONTEXT:\n"
        if conditions:
            medical_context += f"Medical Conditions: {', '.join(conditions)}\n"
        if notes:
            medical_context += f"Doctor/Caregiver Notes: {notes}\n"
        medical_context += "Keep these conditions in mind but do not alarm the patient. If symptoms relate to these conditions, you may ask gently if they think it's related."

    dynamic_prompt = BASE_SYSTEM_PROMPT + medical_context + history_block

    logger.info(f"[CALL] Initiating call to {payload.phone} | history={ctx.get('has_history')} | user={user_name}")

    # 3. Call Bolna API
    bolna_payload = {
        "agent_id": BOLNA_AGENT_ID,
        "recipient_phone_number": payload.phone,
        "agent_prompts": {
            "task_1": {
                "system_prompt": dynamic_prompt
            }
        }
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.bolna.ai/call",
            headers={
                "Authorization": f"Bearer {BOLNA_API_KEY}",
                "Content-Type": "application/json"
            },
            json=bolna_payload
        )

    if resp.status_code not in (200, 201, 202):
        logger.error(f"[CALL] Bolna API error {resp.status_code}: {resp.text}")
        raise HTTPException(
            status_code=502,
            detail=f"Bolna API returned {resp.status_code}: {resp.text}"
        )

    resp_data = resp.json()
    logger.info(f"[CALL] Bolna call queued: {resp_data}")

    return {
        "status": "queued",
        "run_id": resp_data.get("run_id") or resp_data.get("execution_id"),
        "phone": payload.phone,
        "user_name": user_name,
        "has_history": ctx.get("has_history", False),
        "history_summary": ctx.get("summary_lines", []),
        "prompt_preview": dynamic_prompt[:300] + "..."
    }


# ---------------------------------------------------------------------------
# WhatsApp Test & Notification Endpoints
# ---------------------------------------------------------------------------

class TestWhatsAppRequest(BaseModel):
    to_phone: str = Field(..., description="Phone number to send test to, e.g. +919004261186")
    patient_name: Optional[str] = Field("Atharva", description="Patient name for the test message")


@app.post("/test-whatsapp", tags=["WhatsApp"])
def test_whatsapp(payload: TestWhatsAppRequest, api_key: str = Depends(get_api_key)):
    """
    Send a test WhatsApp message to verify Twilio integration.
    Set USE_TWILIO=true and USE_WHATSAPP=true in .env to actually send.
    """
    result = send_test_whatsapp(
        to_phone=payload.to_phone,
        patient_name=payload.patient_name or "the patient",
    )
    return result


@app.post("/notify", tags=["WhatsApp"])
async def manual_notify(request: Request, api_key: str = Depends(get_api_key)):
    """
    Manually trigger a WhatsApp alert to a caregiver.
    Useful for testing the full alert pipeline without a real call.

    Body: { phone: '+91...', risk_level: 'HIGH', symptoms: ['fever'], patient_name: 'Atharva' }
    """
    body = await request.json()
    phone        = body.get("phone", os.environ.get("CAREGIVER_PHONE", ""))
    risk_level   = body.get("risk_level", "HIGH")
    symptoms     = body.get("symptoms", ["fever"])
    patient_name = body.get("patient_name", "the patient")
    caregiver_name = body.get("caregiver_name")

    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")

    from src.notifications import send_whatsapp_alert
    fake_assessment = {
        "risk_level": risk_level,
        "score": 75,
        "symptoms": symptoms,
        "action": "notify_caregiver",
        "steps": [
            "Check on the patient immediately",
            "Monitor temperature every 2 hours",
            "Ensure they stay hydrated",
        ],
    }
    sent = send_whatsapp_alert(
        interaction_id="manual-test",
        response_data=fake_assessment,
        to_phone=phone,
        patient_name=patient_name,
        caregiver_name=caregiver_name,
    )
    return {
        "sent": sent,
        "to": phone,
        "risk_level": risk_level,
        "symptoms": symptoms,
    }


# ===========================================================================
# Onboarding & Profile Routes
# ===========================================================================

class ProfileSetupRequest(BaseModel):
    firebase_uid: str
    elder_name: str
    elder_phone: str
    elder_age: Optional[int] = None
    medical_conditions: List[str] = []
    medical_notes: str = ""
    family_contacts: List[Dict[str, str]] = []

@app.post("/setup-profile")
async def setup_profile(req: ProfileSetupRequest, api_key: str = Depends(get_api_key)):
    """Upsert profile and family contacts from the frontend onboarding."""
    try:
        user_id = upsert_user_profile(
            firebase_uid=req.firebase_uid,
            name=req.elder_name,
            phone=req.elder_phone,
            age=req.elder_age,
            conditions=req.medical_conditions,
            notes=req.medical_notes
        )
        if not user_id:
            raise HTTPException(status_code=500, detail="Database error or PostgreSQL not active.")
            
        if req.family_contacts:
            upsert_family_contacts(user_id, req.family_contacts)
            
        return {"status": "success", "user_id": user_id}
    except Exception as e:
        logger.error(f"Error in setup_profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/setup-profile")
async def get_profile(firebase_uid: str, api_key: str = Depends(get_api_key)):
    profile = get_user_profile(firebase_uid)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile

class FamilyContactRequest(BaseModel):
    firebase_uid: str
    name: str
    phone: str
    relationship: str

@app.post("/family-contacts")
async def add_family_contact(req: FamilyContactRequest, api_key: str = Depends(get_api_key)):
    """Add a single family contact."""
    try:
        profile = get_user_profile(req.firebase_uid)
        if not profile:
            raise HTTPException(status_code=404, detail="Elder profile not found")
            
        add_single_family_contact(str(profile["user_id"]), req.name, req.phone, req.relationship)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error adding family contact: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/family-contacts")
async def get_family_contacts_route(firebase_uid: str, api_key: str = Depends(get_api_key)):
    try:
        profile = get_user_profile(firebase_uid)
        if not profile:
            raise HTTPException(status_code=404, detail="Elder profile not found")
            
        contacts = get_family_contacts(str(profile["user_id"]))
        
        # Format the response for frontend
        formatted_contacts = []
        for c in contacts:
            formatted_contacts.append({
                "id": str(c["user_id"]),
                "name": c["name"],
                "phone": c["phone"],
                "relationship": c.get("relationship", "Other")
            })
        return formatted_contacts
    except Exception as e:
        logger.error(f"Error fetching family contacts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/family-contacts/{contact_id}")
async def delete_family_contact_route(contact_id: str, api_key: str = Depends(get_api_key)):
    try:
        success = delete_family_contact(contact_id)
        if not success:
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error deleting family contact: {e}")
        raise HTTPException(status_code=500, detail=str(e))

import google.generativeai as genai

@app.post("/upload-document")
async def upload_document(
    file: UploadFile = File(...), 
    firebase_uid: str = Form(None),
    api_key: str = Depends(get_api_key)
):
    """MVP file upload: save to local uploads directory and parse with Gemini."""
    try:
        upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        
        # Create a unique filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        safe_filename = file.filename.replace(" ", "_")
        unique_filename = f"{timestamp}_{safe_filename}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(await file.read())
            
        extracted_notes = ""
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key and firebase_uid:
            try:
                genai.configure(api_key=gemini_key)
                uploaded_file = genai.upload_file(path=file_path)
                model = genai.GenerativeModel(model_name="gemini-1.5-flash")
                prompt = "Please extract the patient's medical history, current conditions, allergies, and any important medical notes from this document. Summarize it concisely."
                response = model.generate_content([uploaded_file, prompt])
                extracted_notes = response.text
                
                # Update the database
                if extracted_notes:
                    profile = get_user_profile(firebase_uid)
                    if profile:
                        existing_notes = profile.get("medical_notes") or ""
                        new_notes = existing_notes + f"\n\n[Extracted from {file.filename}]:\n{extracted_notes}"
                        upsert_user_profile(
                            firebase_uid=firebase_uid,
                            name=profile["name"],
                            phone=profile["phone"],
                            age=profile["age"],
                            conditions=profile.get("medical_conditions") or [],
                            notes=new_notes.strip()
                        )
            except Exception as gemini_err:
                logger.error(f"Gemini parsing failed: {gemini_err}")
                
        return {
            "status": "success", 
            "filename": unique_filename, 
            "path": file_path,
            "parsed": bool(extracted_notes)
        }
    except Exception as e:
        logger.error(f"Error uploading document: {e}")
        raise HTTPException(status_code=500, detail=str(e))

