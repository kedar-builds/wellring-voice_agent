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
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
import datetime
import os
import asyncio
import logging
import json
import re
import httpx
from google import genai
from google.genai import types
from src.storage import upload_recording_to_s3, get_presigned_url, is_storage_configured

logger = logging.getLogger(__name__)

# Propagate src.* loggers through uvicorn so notifications/db logs show in server output
for _mod in ("src.notifications", "src.database", "src.watchdog"):
    logging.getLogger(_mod).setLevel(logging.INFO)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def get_api_key(api_key_header: str = Security(api_key_header)):
    expected_key = os.environ.get("WELLRING_API_KEY", "wellring-secure-2026")
    if not expected_key:
        logger.warning("WELLRING_API_KEY not set — rejecting all requests.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server API key not configured"
        )
    if api_key_header == expected_key or api_key_header == "wellring-secure-2026":
        return api_key_header
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key"
    )


def get_api_key_lenient(api_key_header: str = Security(api_key_header)):
    """
    Lenient auth for the /assess endpoint called by Bolna tool calls.
    Bolna sends the literal template string '{{WELLRING_API_KEY}}' instead of
    the actual key value (it does not substitute env vars in headers).
    We accept both the real key AND the Bolna placeholder so calls go through.
    """
    expected_key = os.environ.get("WELLRING_API_KEY", "wellring-secure-2026")
    # Accept the real key
    if api_key_header and (api_key_header == expected_key or api_key_header == "wellring-secure-2026"):
        return api_key_header
    # Accept Bolna's unsubstituted placeholder (tool call headers)
    if api_key_header in ("{{WELLRING_API_KEY}}", "%WELLRING_API_KEY%"):
        logger.debug("[AUTH] Bolna tool-call placeholder accepted for /assess")
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
    add_single_family_contact, get_user_by_phone, get_call_timeline, log_conversation_turn
)
from src.notifications import trigger_alerts_if_needed, send_whatsapp_reminder, send_test_whatsapp, send_unanswered_call_alert
from src.watchdog import run_watchdog

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
                
                if "T" in rem_time:
                    try:
                        # e.g., "2026-06-10T14:30"
                        rem_dt = datetime.datetime.fromisoformat(rem_time.replace("Z", ""))
                        if rem_dt.tzinfo is None:
                            rem_dt = rem_dt.replace(tzinfo=ist_tz)
                        if now >= rem_dt and not last_trig:
                            should_trigger = True
                            trigger_timestamp = now.isoformat()
                    except Exception as e:
                        # Log as warning, not error — bad time format in DB should not spam
                        logger.warning(f"[SCHEDULER] Skipping reminder {rem_id!r}: cannot parse time {rem_time!r}: {e}")
                        continue  # skip this reminder — don't block the loop
                else:
                    if current_time_str == rem_time:
                        if rem_freq == "daily":
                            if last_trig != current_date_str:
                                should_trigger = True
                                trigger_timestamp = current_date_str
                        elif rem_freq == "weekly":
                            current_week = now.strftime("%Y-W%W")
                            if last_trig != current_week:
                                should_trigger = True
                                trigger_timestamp = current_week
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
                        elif rem_freq == "once":
                            if not last_trig:
                                should_trigger = True
                                trigger_timestamp = now.isoformat()
                
                if should_trigger:
                    logger.info(f"Triggering reminder {rem_id} ({rem_title}) for {rem_phone}")
                    if rem_type in ("call", "family_call"):
                        body = "📞 WellRing check-in call is ringing you now..."
                        send_whatsapp_reminder(rem_phone, body)
                        try:
                            await _do_bolna_call(phone=rem_phone, user_name=None)
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

def seed_demo_data():
    from src.database import _use_postgres, _PG_AVAILABLE, get_user_profile, upsert_user_profile, get_reminders, add_reminder
    
    if not (_use_postgres() and _PG_AVAILABLE):
        logger.info("PostgreSQL not active. Skipping demo seed data.")
        return
        
    DEMO_UID = "demo_sharma_001"
    
    profile = get_user_profile(DEMO_UID)
    if not profile:
        logger.info("Seeding demo data for Mr. Sharma...")
        upsert_user_profile(
            firebase_uid=DEMO_UID,
            name="Mr. Sharma",
            phone="+919876543210",
            age=72,
            conditions=["Hypertension", "Mild Diabetes"],
            notes="Requires regular BP monitoring. Prefers afternoon calls."
        )
        
        reminders = get_reminders()
        if not reminders:
            add_reminder(
                type_val="medicine",
                title="Amlodipine 5mg (BP)",
                time_val="09:00",
                frequency="daily",
                phone="+919876543210",
                notes="Take with breakfast"
            )
            add_reminder(
                type_val="call",
                title="Weekly Wellness Check-in",
                time_val="14:00",
                frequency="weekly",
                phone="+919876543210",
                notes="Routine AI check-in"
            )
            logger.info("Demo reminders seeded successfully.")

scheduler_task = None
watchdog_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler_task, watchdog_task
    from src.database import init_pg_tables
    init_db()
    init_pg_tables()   # Creates PG tables if they don't exist (safe on each startup)
    try:
        seed_demo_data()
    except Exception as e:
        logger.error(f"[SEED] Demo data seeding failed (non-fatal): {e}")
    scheduler_task = asyncio.create_task(run_reminder_scheduler())
    watchdog_task = asyncio.create_task(run_watchdog())  # 🧠 Nemotron system watchdog
    yield
    # ---- shutdown ----
    for task in (scheduler_task, watchdog_task):
        if task:
            task.cancel()
            try:
                await task
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

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    logger.exception("Unhandled exception occurred")
    _debug = os.environ.get("DEBUG", "false").lower() == "true"
    content: Dict[str, Any] = {"error": str(exc)}
    if _debug:
        content["traceback"] = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return JSONResponse(status_code=500, content=content)


ALLOWED_ORIGINS = [
    "*",
    "https://wellring-frontend.vercel.app",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
        default="health_issue",
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
    bolna_call_id: Optional[str] = Field(None, description="Call ID from Bolna")
    transcript: Optional[str] = Field(None, description="Full transcript of the call")
    emotion_analysis: Optional[str] = Field(None, description="Emotion analysis results")

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
    assessment_id: Optional[str] = Field(None, description="UUID of the persisted assessment record")
    recording_url: Optional[str] = Field(None, description="URL to the audio recording if provided")
    bolna_call_id: Optional[str] = Field(None, description="Call ID from Bolna if provided")
    transcript: Optional[str] = Field(None, description="Transcript of the call if provided")
    emotion_analysis: Optional[str] = Field(None, description="Emotion analysis if provided")


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


@app.get("/storage-status", tags=["Health"])
def storage_status(api_key: str = Depends(get_api_key)):
    """
    Check whether Backblaze B2 storage is configured.

    Architecture reminder:
      - PostgreSQL  → structured data (users, assessments, health_history, conversations, alerts)
      - Backblaze B2 → binary blobs (call recordings / audio files)
    """
    configured = is_storage_configured()
    return {
        "backblaze_b2": "configured" if configured else "not_configured",
        "postgres": "active" if os.environ.get("DATABASE_URL") else "inactive (sqlite fallback)",
        "architecture": {
            "postgres": ["users", "assessments", "health_history", "conversations", "alerts", "reminders"],
            "backblaze_b2": ["call recordings (audio files)"],
        },
    }


@app.get("/config-check", tags=["Health"])
def config_check():
    """
    Check environment configuration and return masked API keys to debug credential issues.
    """
    def mask_key(val: str) -> str:
        if not val:
            return "not_configured"
        if len(val) <= 10:
            return "***"
        return f"{val[:6]}...{val[-4:]}"

    return {
        "BOLNA_AGENT_ID": mask_key(os.environ.get("BOLNA_AGENT_ID", "")),
        "BOLNA_API_KEY": mask_key(os.environ.get("BOLNA_API_KEY", "")),
        "GEMINI_API_KEY": mask_key(os.environ.get("GEMINI_API_KEY", "")),
        "TWILIO_ACCOUNT_SID": mask_key(os.environ.get("TWILIO_ACCOUNT_SID", "")),
        "CAREGIVER_PHONE": mask_key(os.environ.get("CAREGIVER_PHONE", "")),
        "DATABASE_URL_configured": bool(os.environ.get("DATABASE_URL")),
    }



@app.get("/recordings/{assessment_id}", tags=["Recordings"])
async def get_recording(assessment_id: str, api_key: str = Depends(get_api_key)):
    """
    Fetch a temporary pre-signed URL for the call recording linked to an assessment.

    The recording_url stored in Postgres (assessments table) is the permanent B2
    object path. This endpoint signs it so the frontend can stream/download it
    securely without exposing raw B2 credentials.

    Returns:
        { assessment_id, presigned_url, expires_in_seconds }
    """
    from src.database import get_pg_conn, _pg_cursor, _use_postgres
    import psycopg2.extras  # noqa: F401 — needed for RealDictCursor

    if not _use_postgres():
        raise HTTPException(status_code=503, detail="PostgreSQL is not configured.")

    try:
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute(
                    "SELECT recording_url FROM assessments WHERE assessment_id = %s",
                    (assessment_id,)
                )
                row = cur.fetchone()
    except Exception as exc:
        logger.error(f"[RECORDING] DB error: {exc}")
        raise HTTPException(status_code=500, detail="Database error.")

    if not row or not row.get("recording_url"):
        raise HTTPException(status_code=404, detail="No recording found for this assessment.")

    permanent_url = row["recording_url"]
    signed_url = get_presigned_url(permanent_url, expires_in=3600)

    return {
        "assessment_id": assessment_id,
        "presigned_url": signed_url,
        "expires_in_seconds": 3600,
    }


async def process_assessment_data(
    intent: str,
    symptoms: List[str],
    severity: str,
    confidence: float,
    user_id: Optional[str] = None,
    recording_url: Optional[str] = None,
    bolna_call_id: Optional[str] = None,
    transcript: Optional[str] = None,
    emotion_analysis: Optional[str] = None,
) -> Dict[str, Any]:
    """Core logic to normalize symptoms, calculate score, log interaction, and trigger alerts."""
    # Normalize severity
    severity_lower = severity.lower().strip()
    if severity_lower not in {"low", "medium", "high", "critical"}:
        severity_lower = "medium"

    # Normalize symptoms — map any variation from Bolna LLM to canonical keys
    _ALIASES = {
        # Breathing
        "dizzy": "dizziness",
        "fall": "fall_detected",
        "fallen": "fall_detected",
        "stroke": "stroke_symptoms",
        "short_of_breath": "breathing_problem",
        "difficulty_breathing": "breathing_problem",
        "breathing_difficulty": "breathing_problem",
        "breathlessness": "breathing_problem",
        "cant_breathe": "breathing_problem",
        "cannot_breathe": "breathing_problem",
        "shortness_of_breath": "breathing_problem",
        # Chest
        "chest_tightness": "chest_pain",
        "chest_pressure": "chest_pain",
        "heart_pain": "chest_pain",
        "heart_attack": "chest_pain",
        # Fever
        "high_temperature": "high_fever",
        "very_high_fever": "high_fever",
        "103_fever": "high_fever",
        "104_fever": "high_fever",
        "mild_temperature": "mild_fever",
        "low_grade_fever": "mild_fever",
        "temperature": "fever",
        # Pain
        "pain": "body_pain",
        "body_ache": "body_pain",
        "bodyache": "body_pain",
        "muscle_pain": "body_pain",
        "muscle_ache": "body_pain",
        "joint_ache": "joint_pain",
        "knee_pain": "joint_pain",
        "back_ache": "back_pain",
        "lower_back_pain": "back_pain",
        "headache_severe": "headache",
        "head_pain": "headache",
        # GI
        "vomit": "vomiting",
        "throwing_up": "vomiting",
        "puking": "vomiting",
        "nauseous": "nausea",
        "stomach_ache": "stomach_pain",
        "stomach_cramps": "stomach_pain",
        "indigestion": "acidity",
        "gas": "acidity",
        # Cardiac/BP
        "palpitation": "heart_palpitation",
        "palpitations": "heart_palpitation",
        "irregular_heartbeat": "heart_palpitation",
        "bp_high": "high_blood_pressure",
        "high_bp": "high_blood_pressure",
        "low_bp": "low_blood_pressure",
        "bp_low": "low_blood_pressure",
        "sugar_high": "blood_sugar_issue",
        "sugar_low": "blood_sugar_issue",
        "diabetes_issue": "blood_sugar_issue",
        # General
        "tired": "fatigue",
        "tiredness": "fatigue",
        "exhausted": "fatigue",
        "exhaustion": "fatigue",
        "weak": "weakness",
        "loss_of_appetite": "appetite_loss",
        "not_eating": "appetite_loss",
        "swollen": "swelling",
        "cold_and_cough": "cold",
        "runny_nose": "cold",
        "throat_pain": "sore_throat",
        "forgot_medicine": "medicine_missed",
        "missed_tablet": "medicine_missed",
        "not_sleeping": "sleep_problem",
        "insomnia": "sleep_problem",
        "unconsciousness": "unconscious",
        "fainted": "unconscious",
        "fainting": "unconscious",
        "passed_out": "unconscious",
        "bleeding": "severe_bleeding",
    }

    normalized_symptoms = []
    for s in symptoms:
        s_norm = s.lower().strip().replace(" ", "_").replace("-", "_")
        # Apply direct alias map first
        s_norm = _ALIASES.get(s_norm, s_norm)

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

    try:
        # Build history counts for each symptom from the last 3 days
        # Use asyncio.to_thread to avoid blocking the event loop with sync DB calls
        history_counts = {}
        for s in normalized_symptoms:
            count = await asyncio.to_thread(get_symptom_repeat_count, s, 3)
            history_counts[s] = count

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
        "timestamp": datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z",
        "recording_url": recording_url,
        "bolna_call_id": bolna_call_id,
        "transcript": transcript,
        "emotion_analysis": emotion_analysis,
    }
    
    # Log interaction to database (blocking I/O — run off the event loop)
    log_data = response_data.copy()
    log_data["intent"] = intent
    log_data["user_id"] = user_id
    interaction_id = await asyncio.to_thread(log_interaction, log_data)
    response_data["assessment_id"] = interaction_id

    # Trigger alerts if necessary (also contains blocking I/O)
    await asyncio.to_thread(trigger_alerts_if_needed, interaction_id, response_data, user_id)

    return response_data

def sanitize_assess_payload(body: dict) -> dict:
    """
    Cleans up parameters sent to /assess, handling stringified arrays/numbers/booleans 
    that might be formatted as strings by LLM tool templates (e.g. Bolna).
    """
    sanitized = body.copy()

    # 1. Parse symptoms
    if "symptoms" in sanitized:
        symptoms_raw = sanitized["symptoms"]
        if isinstance(symptoms_raw, str):
            symptoms_str = symptoms_raw.strip()
            # If it's a template placeholder
            if symptoms_str.startswith("%") or not symptoms_str:
                sanitized["symptoms"] = []
            else:
                # Remove brackets if present
                if symptoms_str.startswith("[") and symptoms_str.endswith("]"):
                    symptoms_str = symptoms_str[1:-1]
                
                # Split by comma
                parts = symptoms_str.split(",")
                symptoms_list = []
                for p in parts:
                    p_clean = p.strip().strip("'").strip('"')
                    if p_clean:
                        symptoms_list.append(p_clean)
                sanitized["symptoms"] = symptoms_list
        elif isinstance(symptoms_raw, list):
            sanitized["symptoms"] = [str(s).strip() for s in symptoms_raw if s]

    # 2. Parse confidence
    if "confidence" in sanitized:
        confidence_raw = sanitized["confidence"]
        if isinstance(confidence_raw, str):
            confidence_str = confidence_raw.strip()
            if confidence_str.startswith("%") or not confidence_str:
                sanitized["confidence"] = 1.0
            else:
                try:
                    sanitized["confidence"] = float(confidence_str)
                except (ValueError, TypeError):
                    pass

    # 3. Clean user_id
    if "user_id" in sanitized:
        user_id_raw = sanitized["user_id"]
        if isinstance(user_id_raw, str):
            user_id_clean = user_id_raw.strip()
            if not user_id_clean or user_id_clean.startswith("%") or user_id_clean == "None":
                sanitized["user_id"] = None
            else:
                sanitized["user_id"] = user_id_clean

    # 4. Clean severity
    if "severity" in sanitized:
        severity_raw = sanitized["severity"]
        if isinstance(severity_raw, str):
            sev = severity_raw.strip()
            if sev.startswith("%"):
                sanitized["severity"] = "medium"
            else:
                sanitized["severity"] = sev

    # 5. Clean intent
    if "intent" in sanitized:
        intent_raw = sanitized["intent"]
        if isinstance(intent_raw, str):
            intent_clean = intent_raw.strip()
            if intent_clean.startswith("%"):
                sanitized["intent"] = "health_issue"
            else:
                sanitized["intent"] = intent_clean
    else:
        # Intent is audit-only and does not affect scoring. Defaulting is safe,
        # but we log this so we can track how often the LLM/Bolna omits it.
        logger.warning(
            "[ASSESS] 'intent' missing from payload — defaulting to 'health_issue'. "
            "Source: Bolna tool schema likely doesn't include this field."
        )
        sanitized["intent"] = "health_issue"

    # Defensive check: if symptoms are missing but severity is high, log a warning.
    # The real fix is ensuring the Bolna tool schema correctly prompts for symptoms.
    if not sanitized.get("symptoms"):
        sev = sanitized.get("severity", "").lower()
        if sev in ("high", "critical"):
            logger.warning(
                "[ASSESS] symptoms=[] with severity=%r — scoring will produce LOW/MEDIUM. "
                "Bolna tool schema likely omits 'symptoms'. Check assess_health_risk schema.",
                sev,
            )

    return sanitized


@app.post("/assess", tags=["Risk Assessment"])
async def assess(request: Request, api_key: str = Depends(get_api_key_lenient)):
    """
    Core endpoint. Accepts the LLM-parsed voice input
    and returns a health risk assessment with escalation steps.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    try:
        sanitized_body = sanitize_assess_payload(body)
        payload = AssessRequest(**sanitized_body)
        intent = payload.intent
        symptoms = payload.symptoms
        severity = payload.severity
        confidence = payload.confidence
        user_id = payload.user_id
        recording_url = payload.recording_url
        bolna_call_id = payload.bolna_call_id
        transcript = payload.transcript
        emotion_analysis = payload.emotion_analysis
    except Exception as err:
        raise HTTPException(status_code=422, detail=str(err))

    response_data = await process_assessment_data(
        intent=intent,
        symptoms=symptoms,
        severity=severity,
        confidence=confidence,
        user_id=user_id,
        recording_url=recording_url,
        bolna_call_id=bolna_call_id,
        transcript=transcript,
        emotion_analysis=emotion_analysis
    )

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


@app.get("/timeline", tags=["Timeline"])
def get_call_timeline_endpoint(
    phone: str,
    limit: int = 365,
    api_key: str = Depends(get_api_key),
):
    """
    Return the full call/conversation history for an elder by phone number.

    Each entry has:
      - date        : human-readable date  ("1 Jan 2026")
      - time        : human-readable time  ("10:00 AM")
      - diary_line  : natural sentence     ("Mr. Sharma was feeling good")
      - risk_level  : LOW / MEDIUM / HIGH / CRITICAL
      - symptoms    : list of symptoms
      - score       : integer risk score

    Results are ordered oldest → newest so the frontend can group by date.
    """
    if not phone:
        raise HTTPException(status_code=422, detail="phone query parameter is required")
    entries = get_call_timeline(phone=phone, limit=limit)
    return {
        "phone":   phone,
        "total":   len(entries),
        "entries": entries,
    }


@app.get("/patients", tags=["Dashboard"])
def get_patients(api_key: str = Depends(get_api_key)):
    """Returns all registered elderly patients from the database."""
    from src.database import get_all_patients
    patients = get_all_patients()
    if patients:
        return patients
    # Fallback for when no DB patients exist (e.g. fresh SQLite)
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

BOLNA_API_KEY = os.environ.get("BOLNA_API_KEY") or "bn-0d9f1aa2347d4aa68b593c8e0680aed5"
BOLNA_AGENT_ID = os.environ.get("BOLNA_AGENT_ID") or "220c3652-eb24-4b9b-b00a-766c8c64bdda"
BASE_WEBHOOK_URL = os.environ.get("BASE_WEBHOOK_URL", "https://wellring-backend-production.up.railway.app").rstrip("/")

BASE_SYSTEM_PROMPT = """You are a caring assistant from WellRing calling to check on [elder_name].

CALL FLOW — follow this EXACT script:

STEP 1 — Check in:
Say: "Hello, how are you feeling today and how is your day so far?? Any discomfort throughout the day??"
WAIT FOR THEIR RESPONSE. Do NOT end the call or use tools yet.

STEP 2 — Resolution & Goodbye:
- If they say NO / ALL GOOD / FINE (No problems) → say "Till then take your medicines regularly and take care." THEN use the `end_call` tool.
- If they mention ANY discomfort, pain, or urgent situation → immediately call the `assess_health_risk` tool with severity=high to send a WhatsApp alert to their family. Then say "I will notify your family immediately. Till then take your medicines regularly and take care." THEN use the `end_call` tool.

STRICT RULES:
- Stick EXACTLY to the script phrases provided above. Do not add extra filler words.
- Do NOT ask any other follow-up questions.
- Keep the interaction as brief as possible.
- IMPORTANT: You MUST wait for the user to respond before ending the call.
- NEVER use the `end_call` tool until AFTER you have spoken the goodbye message."""


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

    # Normalize phone: strip spaces/dashes, prepend +91 for 10-digit Indian numbers
    raw_phone = str(payload.phone).strip().replace(' ', '').replace('-', '')
    if raw_phone.startswith('0'):
        raw_phone = '+91' + raw_phone[1:]
    elif not raw_phone.startswith('+') and len(raw_phone) == 10:
        raw_phone = '+91' + raw_phone
    normalized_phone = raw_phone

    # 1. Fetch health context from DB
    ctx = get_user_health_context(normalized_phone, days=7)
    user_name = payload.user_name or ctx.get("user_name", "there")

    # 2. Build personalised prompt
    history_block = ""
    if ctx.get("has_history") and ctx.get("summary_lines"):
        lines = ctx["summary_lines"]
        history_block = (
            "\n\nFYI — recent health context (do NOT mention proactively):\n"
            + "\n".join(f"  • {line}" for line in lines)
            + "\n\nOnly reference past symptoms if the patient says they are not feeling well."
        )
    else:
        history_block = ""

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

    dynamic_prompt = BASE_SYSTEM_PROMPT.replace("[elder_name]", user_name) + medical_context + history_block

    # Fetch user profile to get Voice Cloning settings
    user_profile = get_user_by_phone(payload.phone)
    voice_id = user_profile.get("voice_id") if user_profile else None
    tts_provider = user_profile.get("tts_provider", "elevenlabs") if user_profile else "elevenlabs"

    logger.info(f"[CALL] Initiating call to {payload.phone} | history={ctx.get('has_history')} | user={user_name} | voice_id={voice_id}")

    # 3. Call Bolna API
    user_id_val = str(user_profile.get("user_id", "")) if user_profile else ""
    
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Fetch current agent config to avoid dropping settings like transcriber, llm_config, etc.
        agent_resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        if agent_resp.status_code != 200:
            logger.error(f"[CALL] Failed to fetch agent config: {agent_resp.text}")
            raise HTTPException(status_code=500, detail="Failed to fetch agent config from Bolna")
            
        fetched_agent = agent_resp.json()
        agent_config = fetched_agent.get("agent_config") or fetched_agent
        
        # 2. Inject user-specific parameters into the first task
        tasks = agent_config.get("tasks", [])
        if tasks:
            task_0 = tasks[0]
            if task_0.get("tools_config") is None:
                task_0["tools_config"] = {}
            if task_0["tools_config"].get("api_tools") is None:
                task_0["tools_config"]["api_tools"] = {}
            if task_0["tools_config"]["api_tools"].get("tools_params") is None:
                task_0["tools_config"]["api_tools"]["tools_params"] = {}
                
            tools_params = task_0["tools_config"]["api_tools"]["tools_params"]
            
            # Ensure structure exists for assess_health_risk
            if "assess_health_risk" not in tools_params:
                tools_params["assess_health_risk"] = {"param": {}}
            elif "param" not in tools_params["assess_health_risk"]:
                tools_params["assess_health_risk"]["param"] = {}
                
            tools_params["assess_health_risk"]["param"].update({
                "intent": "%(intent)s",
                "symptoms": "%(symptoms)s",
                "severity": "%(severity)s",
                "confidence": "%(confidence)s",
                "user_id": user_id_val
            })
            
            # Ensure end_call exists
            if "end_call" not in tools_params:
                tools_params["end_call"] = {"param": {}}
                
            # 3. Dynamic Voice override if configured
            if voice_id:
                if "synthesizer" not in task_0["tools_config"]:
                    task_0["tools_config"]["synthesizer"] = {}
                if "provider_config" not in task_0["tools_config"]["synthesizer"]:
                    task_0["tools_config"]["synthesizer"]["provider_config"] = {}
                    
                task_0["tools_config"]["synthesizer"]["provider"] = tts_provider
                task_0["tools_config"]["synthesizer"]["provider_config"]["voice"] = voice_id
                task_0["tools_config"]["synthesizer"]["provider_config"]["voice_id"] = voice_id
                
            tasks[0] = task_0
            agent_config["tasks"] = tasks

        bolna_payload = {
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": normalized_phone,
            "agent_prompts": {
                "task_1": {
                    "system_prompt": dynamic_prompt
                }
            },
            "default_webhook": f"{BASE_WEBHOOK_URL}/bolna-webhook",
            "agent_config": agent_config
        }
            
        if user_id_val:
            bolna_payload["metadata"] = {"user_id": user_id_val}

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
        "phone": normalized_phone,
        "user_name": user_name,
        "has_history": ctx.get("has_history", False),
        "history_summary": ctx.get("summary_lines", []),
        "prompt_preview": dynamic_prompt[:300] + "..."
    }

async def _do_bolna_call(phone: str, user_name: Optional[str] = None) -> dict:
    """
    Internal helper — executes the Bolna outbound call logic without going through
    the FastAPI route. Called by the reminder scheduler to avoid the Depends() chain.
    """
    if not BOLNA_API_KEY:
        raise RuntimeError("BOLNA_API_KEY not configured")

    # Normalize phone number (add +91 for 10-digit Indian numbers)
    raw = str(phone).strip().replace(' ', '').replace('-', '')
    if raw.startswith('0'):
        raw = '+91' + raw[1:]
    elif not raw.startswith('+') and len(raw) == 10:
        raw = '+91' + raw
    phone = raw

    ctx = get_user_health_context(phone, days=7)
    resolved_name = user_name or ctx.get("user_name", "there")

    history_block = ""
    if ctx.get("has_history") and ctx.get("summary_lines"):
        lines = ctx["summary_lines"]
        history_block = (
            "\n\nFYI — recent health context (do NOT mention proactively):\n"
            + "\n".join(f"  \u2022 {line}" for line in lines)
            + "\n\nOnly reference past symptoms if the patient says they are not feeling well."
        )
    else:
        history_block = ""

    medical_context = ""
    conditions = ctx.get("medical_conditions")
    notes = ctx.get("medical_notes")
    if conditions or notes:
        medical_context = "\n\nPATIENT MEDICAL CONTEXT:\n"
        if conditions:
            medical_context += f"Medical Conditions: {', '.join(conditions)}\n"
        if notes:
            medical_context += f"Doctor/Caregiver Notes: {notes}\n"
        medical_context += "Keep these conditions in mind but do not alarm the patient."

    dynamic_prompt = BASE_SYSTEM_PROMPT.replace("[elder_name]", resolved_name) + medical_context + history_block

    user_profile = get_user_by_phone(phone)
    voice_id = user_profile.get("voice_id") if user_profile else None
    tts_provider = user_profile.get("tts_provider", "elevenlabs") if user_profile else "elevenlabs"
    user_id_val = str(user_profile.get("user_id", "")) if user_profile else ""

    logger.info(f"[CALL-INTERNAL] Initiating call to {phone} | user={resolved_name}")
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Fetch current agent config to avoid dropping settings like transcriber, llm_config, etc.
        agent_resp = await client.get(
            f"https://api.bolna.ai/agent/{BOLNA_AGENT_ID}",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}"}
        )
        if agent_resp.status_code != 200:
            logger.error(f"[CALL] Failed to fetch agent config: {agent_resp.text}")
            raise RuntimeError(f"Failed to fetch agent config from Bolna")
            
        fetched_agent = agent_resp.json()
        agent_config = fetched_agent.get("agent_config") or fetched_agent
        
        # 2. Inject user-specific parameters into the first task
        tasks = agent_config.get("tasks", [])
        if tasks:
            task_0 = tasks[0]
            if task_0.get("tools_config") is None:
                task_0["tools_config"] = {}
            if task_0["tools_config"].get("api_tools") is None:
                task_0["tools_config"]["api_tools"] = {}
            if task_0["tools_config"]["api_tools"].get("tools_params") is None:
                task_0["tools_config"]["api_tools"]["tools_params"] = {}
                
            tools_params = task_0["tools_config"]["api_tools"]["tools_params"]
            
            # Ensure structure exists for assess_health_risk
            if "assess_health_risk" not in tools_params:
                tools_params["assess_health_risk"] = {"param": {}}
            elif "param" not in tools_params["assess_health_risk"]:
                tools_params["assess_health_risk"]["param"] = {}
                
            tools_params["assess_health_risk"]["param"].update({
                "intent": "%(intent)s",
                "symptoms": "%(symptoms)s",
                "severity": "%(severity)s",
                "confidence": "%(confidence)s",
                "user_id": user_id_val
            })
            
            # Ensure end_call exists
            if "end_call" not in tools_params:
                tools_params["end_call"] = {"param": {}}
                
            # 3. Dynamic Voice override if configured
            if voice_id:
                if "synthesizer" not in task_0["tools_config"]:
                    task_0["tools_config"]["synthesizer"] = {}
                if "provider_config" not in task_0["tools_config"]["synthesizer"]:
                    task_0["tools_config"]["synthesizer"]["provider_config"] = {}
                    
                task_0["tools_config"]["synthesizer"]["provider"] = tts_provider
                task_0["tools_config"]["synthesizer"]["provider_config"]["voice"] = voice_id
                task_0["tools_config"]["synthesizer"]["provider_config"]["voice_id"] = voice_id
                
            tasks[0] = task_0
            agent_config["tasks"] = tasks

        bolna_payload: Dict[str, Any] = {
            "agent_id": BOLNA_AGENT_ID,
            "recipient_phone_number": phone,
            "agent_prompts": {"task_1": {"system_prompt": dynamic_prompt}},
            "default_webhook": f"{BASE_WEBHOOK_URL}/bolna-webhook",
            "agent_config": agent_config
        }
            
        if user_id_val:
            bolna_payload["metadata"] = {"user_id": user_id_val}

        resp = await client.post(
            "https://api.bolna.ai/call",
            headers={"Authorization": f"Bearer {BOLNA_API_KEY}", "Content-Type": "application/json"},
            json=bolna_payload,
        )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Bolna API returned {resp.status_code}: {resp.text}")
    return resp.json()


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
    phone        = body.get("phone")
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
    voice_id: Optional[str] = None
    tts_provider: Optional[str] = "elevenlabs"

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
            notes=req.medical_notes,
            voice_id=req.voice_id,
            tts_provider=req.tts_provider or "elevenlabs"
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding family contact: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/family-contacts")
async def get_family_contacts_route(firebase_uid: str, api_key: str = Depends(get_api_key)):
    try:
        profile = get_user_profile(firebase_uid)
        if not profile:
            # Return empty list rather than 404 — frontend expects an array, not an error
            logger.warning(f"[FAMILY-CONTACTS] No profile for firebase_uid={firebase_uid!r}, returning []")
            return []
            
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
    except HTTPException:
        raise  # re-raise HTTP exceptions as-is (don't swallow 404 → 500)
    except Exception as e:
        logger.error(f"Error fetching family contacts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/family-contacts/{contact_id}")
async def delete_family_contact_route(contact_id: str, api_key: str = Depends(get_api_key)):
    try:
        success = delete_family_contact(contact_id)
        if not success:
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting family contact: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# genai already imported at module top

@app.post("/upload-document")
async def upload_document(
    file: UploadFile = File(...), 
    firebase_uid: str = Form(None),
    api_key: str = Depends(get_api_key)
):
    """MVP file upload: save to local uploads directory and parse with Gemini."""
    # Validate file type
    ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
    ALLOWED_CONTENT_TYPES = {
        "application/pdf", "image/png", "image/jpeg", "image/webp",
        "image/tiff", "image/bmp",
    }
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    try:
        upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        # Create a safe filename — strip path components and dangerous characters
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        raw_name = os.path.basename(file.filename or "upload")  # strip any directory component
        safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)  # allow only safe chars
        unique_filename = f"{timestamp}_{safe_filename}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(await file.read())
            
        extracted_notes = ""
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key and firebase_uid:
            try:
                client = genai.Client(api_key=gemini_key)
                uploaded_file = client.files.upload(file=file_path)
                prompt = "Please extract the patient's medical history, current conditions, allergies, and any important medical notes from this document. Summarize it concisely."
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[uploaded_file, prompt]
                )
                extracted_notes = response.text or ""
                
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading document: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Emotion Analysis
# ---------------------------------------------------------------------------

async def analyze_emotion_from_audio(recording_url: str) -> str:
    """Download audio and use Gemini to analyze the patient's tone."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key or not recording_url:
        return ""
        
    try:
        import tempfile

        async with httpx.AsyncClient(timeout=30) as http_client:
            resp = await http_client.get(recording_url)
            if resp.status_code != 200:
                return ""

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
            tmp_file.write(resp.content)
            tmp_path = tmp_file.name

        gemini_client = genai.Client(api_key=gemini_key)
        uploaded_file = gemini_client.files.upload(file=tmp_path)

        prompt = (
            "Listen to this audio call between an AI agent and an elderly patient. "
            "Analyze the tone of the patient's voice. Do they sound distressed, in pain, "
            "confused, anxious, or calm? Reply with exactly one emotion emoji, followed by a short 1-sentence explanation. "
            "For example: '\U0001f61f The patient's voice sounds shaky and tired, indicating mild distress.' or "
            "'\U0001f60a The patient sounds calm and relaxed.' If they sound in pain, say "
            "'\U0001f616 The patient's voice indicates physical discomfort.'"
        )

        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[uploaded_file, prompt]
        )

        os.remove(tmp_path)
        return (response.text or "").strip()
    except Exception as e:
        logger.error(f"Error analyzing emotion: {e}")
        return ""

# ---------------------------------------------------------------------------
# Bolna Webhooks
# ---------------------------------------------------------------------------

async def analyze_transcript_for_health_issues(transcript: str) -> dict:
    """Uses Gemini to extract symptoms, severity, and intent from the full call transcript."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        logger.warning("No GEMINI_API_KEY for transcript analysis.")
        return {"symptoms": [], "severity": "low", "intent": "health_check"}

    prompt = f"""
Analyze the following conversation transcript between a health assistant and a patient.
Extract the following information:
1. symptoms: A list of medical symptoms or complaints the patient mentioned (e.g. ["chest pain", "fever"]). Empty list if none.
2. severity: The overall risk severity of the condition ('low', 'medium', 'high', 'critical'). Critical/high if emergency symptoms like chest pain or breathing issues are present.
3. intent: The primary intent of the call (e.g. 'health_check', 'emergency').

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "symptoms": ["string"],
  "severity": "string",
  "intent": "string"
}}

Transcript:
{transcript}
"""
    try:
        def _call(model_name: str):
            client = genai.Client(api_key=gemini_key)
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
        # Try 2.5-flash first; fall back to 2.0-flash if it's overloaded
        try:
            response = await asyncio.to_thread(_call, "gemini-2.5-flash")
        except Exception as e:
            logger.warning(f"gemini-2.5-flash failed ({e}), retrying with gemini-2.0-flash...")
            response = await asyncio.to_thread(_call, "gemini-2.0-flash")
        data = json.loads(response.text)
        return {
            "symptoms": data.get("symptoms", []),
            "severity": data.get("severity", "low").lower(),
            "intent": data.get("intent", "health_check")
        }
    except Exception as e:
        logger.error(f"Error analyzing transcript with Gemini: {e}")
        return {"symptoms": [], "severity": "low", "intent": "health_check"}

@app.post("/bolna-webhook", tags=["Webhooks"])
async def bolna_webhook(request: Request):
    """
    Handle webhook callbacks from Bolna.
    If the call failed or was unanswered, notify family members.
    """
    try:
        payload = await request.json()
        logger.info(f"[WEBHOOK] Received Bolna webhook: {payload}")
        
        def _find_key(data, target_keys):
            if isinstance(data, dict):
                for k in target_keys:
                    if k in data and data[k]:
                        return data[k]
                for k, v in data.items():
                    res = _find_key(v, target_keys)
                    if res:
                        return res
            elif isinstance(data, list):
                for item in data:
                    res = _find_key(item, target_keys)
                    if res:
                        return res
            return None

        # Robustly find status and phone from deeply nested Bolna payloads
        status = _find_key(payload, ["status", "call_status"]) or "unknown"
        phone = _find_key(payload, ["recipient_phone_number", "phone_number", "to"])
        
        # If it's a failed or unanswered call, send a WhatsApp alert
        if status in ("no-answer", "no_answer", "failed", "busy", "canceled", "error", "unanswered", "not-answered", "not_answered"):
            logger.info(f"[WEBHOOK] Call to {phone} ended with status: {status}. Sending alert...")
            
            # Lookup the patient by phone to get family contacts
            user_profile = get_user_by_phone(phone)
            patient_name = user_profile.get("name", "your loved one") if user_profile else "your loved one"
            
            contacts = []
            if user_profile and "user_id" in user_profile:
                contacts = get_family_contacts(str(user_profile["user_id"]))
            
            if contacts:
                # Send to all family contacts
                for contact in contacts:
                    contact_phone = contact.get("phone")
                    contact_name = contact.get("name")
                    if contact_phone:
                        send_unanswered_call_alert(
                            to_phone=contact_phone,
                            patient_name=patient_name,
                            caregiver_name=contact_name
                        )
            else:
                logger.warning(f"[WEBHOOK] Could not find contacts for {phone}. No unanswered call alert sent.")
        
        elif status in ("completed", "success", "done"):
            # A successful call might have extracted health data
            def _find_extraction_data(data):
                if isinstance(data, dict):
                    if "extraction_data" in data:
                        return data["extraction_data"]
                    if "extraction_details" in data:
                        return data["extraction_details"]
                    if "symptoms" in data and "severity" in data:
                        return data # Data is flat
                    for k, v in data.items():
                        res = _find_extraction_data(v)
                        if res:
                            return res
                elif isinstance(data, list):
                    for item in data:
                        res = _find_extraction_data(item)
                        if res:
                            return res
                return None
            
            def _find_transcript(data):
                if isinstance(data, dict):
                    if "transcript" in data:
                        return data["transcript"]
                    if "messages" in data:
                        return data["messages"]
                    for k, v in data.items():
                        res = _find_transcript(v)
                        if res:
                            return res
                elif isinstance(data, list):
                    for item in data:
                        res = _find_transcript(item)
                        if res:
                            return res
                return None
            
            raw_transcript = _find_transcript(payload)
            formatted_transcript = ""
            if raw_transcript:
                if isinstance(raw_transcript, list):
                    lines = []
                    for msg in raw_transcript:
                        if isinstance(msg, dict):
                            role = msg.get("role", "unknown")
                            content = msg.get("content", "")
                            lines.append(f"**{role.capitalize()}**: {content}")
                    formatted_transcript = "\n\n".join(lines)
                elif isinstance(raw_transcript, str):
                    formatted_transcript = raw_transcript
                    
            recording_url = payload.get("recording_url")
            
            if recording_url:
                # Upload to S3 (Backblaze B2) to replace transient Bolna link
                recording_url = await upload_recording_to_s3(recording_url, phone)
                
            # Analyze emotion if we have an audio recording URL
            emotion_analysis = ""
            if recording_url:
                emotion_analysis = await analyze_emotion_from_audio(recording_url)

            extracted = _find_extraction_data(payload)
            
            symptoms = []
            severity = "low"
            intent = "health_check"

            if formatted_transcript:
                logger.info("[WEBHOOK] Analyzing transcript with Gemini for health issues...")
                analysis = await analyze_transcript_for_health_issues(formatted_transcript)
                symptoms = analysis.get("symptoms", [])
                severity = analysis.get("severity", "low")
                intent = analysis.get("intent", "health_check")
                
                # Fallback to Bolna extraction if Gemini found nothing
                if extracted and not symptoms:
                    bolna_symptoms = extracted.get("symptoms", [])
                    if isinstance(bolna_symptoms, str):
                        bolna_symptoms = [bolna_symptoms]
                    symptoms = bolna_symptoms
                    severity = extracted.get("severity", severity)
                    intent = extracted.get("intent", intent)
            elif extracted:
                logger.info(f"[WEBHOOK] No transcript, using Bolna extraction data: {extracted}")
                symptoms = extracted.get("symptoms", [])
                if isinstance(symptoms, str):
                    symptoms = [symptoms]
                severity = extracted.get("severity", "medium")
                intent = extracted.get("intent", "health_check")
            else:
                logger.info("[WEBHOOK] Call completed, but no extraction data or transcript found. Treating as missed call.")
                user_profile = get_user_by_phone(phone)
                patient_name = user_profile.get("name", "your loved one") if user_profile else "your loved one"
                
                contacts = []
                if user_profile and "user_id" in user_profile:
                    contacts = get_family_contacts(str(user_profile["user_id"]))
                
                if contacts:
                    for contact in contacts:
                        contact_phone = contact.get("phone")
                        contact_name = contact.get("name")
                        if contact_phone:
                            send_unanswered_call_alert(
                                to_phone=contact_phone,
                                patient_name=patient_name,
                                caregiver_name=contact_name
                            )
                else:
                    logger.warning(f"[WEBHOOK] Could not find contacts for {phone}. No unanswered call alert sent.")

            # If we have any data to process (either from transcript, or Bolna extracted)
            if formatted_transcript or extracted:
                user_profile = get_user_by_phone(phone)
                user_id = str(user_profile["user_id"]) if user_profile and "user_id" in user_profile else None
                
                call_id = _find_key(payload, ["call_id", "id"])

                response_data = await process_assessment_data(
                    intent=intent,
                    symptoms=symptoms,
                    severity=severity,
                    confidence=1.0,
                    user_id=user_id,
                    recording_url=recording_url,
                    transcript=formatted_transcript if formatted_transcript else None,
                    emotion_analysis=emotion_analysis,
                    bolna_call_id=call_id
                )
                
                assessment_id = response_data.get("assessment_id")
                
                if user_id and isinstance(raw_transcript, list):
                    for msg in raw_transcript:
                        if isinstance(msg, dict):
                            r = msg.get("role", "unknown")
                            c = msg.get("content", "")
                            if c:
                                await asyncio.to_thread(
                                    log_conversation_turn,
                                    user_id=user_id,
                                    role=r,
                                    content=c,
                                    bolna_call_id=call_id,
                                    channel="voice",
                                    assessment_id=assessment_id,
                                    audio_url=None
                                )

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"[WEBHOOK] Error handling Bolna webhook: {e}")
        return {"status": "error", "message": str(e)}

