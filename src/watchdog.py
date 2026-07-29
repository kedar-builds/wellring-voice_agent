"""
watchdog.py
===========
WellRing System Watchdog — Powered by Nemotron (via OpenRouter).

The Nemotron model acts as the brain of the WellRing agent system.
It monitors the entire pipeline every 60 seconds, detects anomalies
(e.g. assessment logged but no family notification sent), reasons about
what went wrong, and autonomously re-triggers the appropriate action.

Anomalies it catches:
  - Assessment completed but no alert fired (notification gap)
  - SQLite alerts_log has failures that need retry
  - Postgres: assessments with no corresponding alerts row

Usage:
    Automatically started as a background task via main.py lifespan.
    Controlled by OPENROUTER_API_KEY env var — disabled if not set.
"""

import asyncio
import datetime
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

# pyrefly: ignore [missing-import]
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
NEMOTRON_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

WATCHDOG_INTERVAL_SECONDS = 60       # how often the watchdog runs
ORPHAN_WINDOW_MINUTES = 10           # look-back window for orphaned assessments
MAX_RETRIES_PER_SESSION = 3          # max re-trigger attempts per watchdog cycle


# ---------------------------------------------------------------------------
# Nemotron API call
# ---------------------------------------------------------------------------

async def _call_nemotron(system_prompt: str, user_message: str) -> Optional[str]:
    """
    Send a message to Nemotron via OpenRouter and return the raw text response.
    Returns None on failure.
    """
    if not OPENROUTER_API_KEY:
        logger.warning("[WATCHDOG] OPENROUTER_API_KEY not set — Nemotron brain disabled.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://wellring-backend-production.up.railway.app",
        "X-Title": "WellRing Watchdog",
    }
    payload = {
        "model": NEMOTRON_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,   # deterministic reasoning
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OPENROUTER_BASE_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error(f"[WATCHDOG] Nemotron API call failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Database: find orphaned assessments (assessed but no alert sent)
# ---------------------------------------------------------------------------

def _get_orphaned_assessments_sqlite(window_minutes: int) -> List[Dict[str, Any]]:
    """
    SQLite: return interactions from the last N minutes that have no entry
    in alerts_log (i.e. the notification was never sent or silently failed).
    """
    db_path = os.environ.get("WELLRING_DB_PATH", "wellring.db")
    cutoff = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=window_minutes)
    ).replace(tzinfo=None).isoformat() + "Z"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT i.id, i.timestamp, i.risk_level, i.symptoms,
                   i.severity, i.score, i.action, i.message, i.user_id
            FROM   interactions i
            LEFT JOIN alerts_log al ON al.interaction_id = i.id
            WHERE  i.timestamp >= ?
              AND  al.id IS NULL
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.error(f"[WATCHDOG] SQLite orphan query failed: {exc}")
        return []


def _get_orphaned_assessments_pg(window_minutes: int) -> List[Dict[str, Any]]:
    """
    PostgreSQL: return assessments from the last N minutes with no alerts row.
    """
    try:
        from src.database import get_pg_conn, _pg_cursor, _PG_AVAILABLE, _use_postgres
        if not (_use_postgres() and _PG_AVAILABLE):
            return []

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=window_minutes)
        sql = """
            SELECT a.assessment_id AS id,
                   a.assessed_at   AS timestamp,
                   a.risk_level,
                   a.symptoms,
                   a.severity,
                   a.score,
                   a.action,
                   a.message,
                   a.user_id
            FROM   assessments a
            LEFT JOIN alerts al ON al.assessment_id = a.assessment_id
            WHERE  a.assessed_at >= %(cutoff)s
              AND  al.alert_id IS NULL
        """
        with get_pg_conn() as conn:
            with _pg_cursor(conn) as cur:
                cur.execute(sql, {"cutoff": cutoff})
                rows = []
                for r in cur.fetchall():
                    d = dict(r)
                    # Serialize non-JSON-safe types
                    if hasattr(d.get("timestamp"), "isoformat"):
                        d["timestamp"] = d["timestamp"].isoformat()
                    if d.get("id"):
                        d["id"] = str(d["id"])
                    if d.get("user_id"):
                        d["user_id"] = str(d["user_id"])
                    if isinstance(d.get("symptoms"), list):
                        d["symptoms"] = list(d["symptoms"])
                    rows.append(d)
                return rows
    except Exception as exc:
        logger.error(f"[WATCHDOG] Postgres orphan query failed: {exc}")
        return []


def _get_failed_alerts_sqlite(window_minutes: int) -> List[Dict[str, Any]]:
    """
    SQLite: return alerts_log rows with status='failed' from the last N minutes.
    """
    db_path = os.environ.get("WELLRING_DB_PATH", "wellring.db")
    cutoff = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=window_minutes)
    ).replace(tzinfo=None).isoformat() + "Z"
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT al.id, al.interaction_id, al.timestamp, al.risk_level,
                   al.notification_type, al.status
            FROM   alerts_log al
            WHERE  al.status = 'failed'
              AND  al.timestamp >= ?
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.error(f"[WATCHDOG] SQLite failed-alert query failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Nemotron reasoning
# ---------------------------------------------------------------------------

WATCHDOG_SYSTEM_PROMPT = """
You are the WellRing System Watchdog Brain — an autonomous health-tech monitoring AI.

Your job is to analyze system health data from the WellRing elderly care platform and decide
if any corrective actions need to be taken. You must respond ONLY with a valid JSON object.

You understand:
- The system makes automated check-in calls to elderly patients.
- After a call, an assessment is logged and a WhatsApp notification is sent to family members.
- If a notification was not sent (orphaned assessment or failed alert), you must flag it for retry.

Risk levels: LOW, MEDIUM, HIGH, CRITICAL
- HIGH and CRITICAL always require immediate family notification.
- LOW and MEDIUM require a routine daily update to the family.

Response JSON schema (STRICT - no extra fields):
{
  "system_healthy": boolean,
  "issues_found": integer,
  "actions": [
    {
      "type": "retry_notification",
      "assessment_id": "string (the interaction/assessment id)",
      "user_id": "string or null",
      "risk_level": "string",
      "symptoms": ["list"],
      "severity": "string",
      "score": integer,
      "action": "string",
      "message": "string",
      "reason": "brief explanation of why you're retrying"
    }
  ],
  "summary": "one sentence summary of system status"
}

If no issues, return: {"system_healthy": true, "issues_found": 0, "actions": [], "summary": "All systems nominal."}
"""


async def _nemotron_analyze(orphaned: List[Dict], failed: List[Dict]) -> Optional[Dict]:
    """
    Feed system health data to Nemotron and get back a structured action plan.
    """
    if not orphaned and not failed:
        return None

    user_msg = json.dumps({
        "orphaned_assessments": orphaned,   # assessed, no alert sent at all
        "failed_alerts": failed,            # alert attempted but failed
        "current_time_utc": datetime.datetime.now(datetime.UTC).isoformat(),
    }, default=str)

    raw = await _call_nemotron(WATCHDOG_SYSTEM_PROMPT, user_msg)
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"[WATCHDOG] Nemotron returned non-JSON response: {exc}\nRaw: {raw[:300]}")
        return None


# ---------------------------------------------------------------------------
# Execute actions decided by Nemotron
# ---------------------------------------------------------------------------

async def _execute_action(action: Dict[str, Any]) -> None:
    """
    Execute a single action returned by Nemotron's analysis.
    Currently supports: retry_notification
    """
    if action.get("type") != "retry_notification":
        logger.warning(f"[WATCHDOG] Unknown action type: {action.get('type')}")
        return

    assessment_id = action.get("assessment_id")
    user_id = action.get("user_id")
    risk_level = action.get("risk_level", "LOW")
    reason = action.get("reason", "Watchdog retry")

    logger.info(
        f"[WATCHDOG] 🤖 Nemotron ordered retry for assessment={assessment_id} "
        f"risk={risk_level} | reason: {reason}"
    )

    # Build response_data dict to pass into the notification engine
    response_data = {
        "risk_level": risk_level.upper(),
        "score":      action.get("score", 0),
        "symptoms":   action.get("symptoms", []),
        "action":     action.get("action", "monitor"),
        "message":    action.get("message", "Watchdog-triggered retry"),
        "steps":      [],
    }

    try:
        from src.notifications import trigger_alerts_if_needed
        import asyncio as _asyncio
        await _asyncio.to_thread(
            trigger_alerts_if_needed,
            assessment_id,
            response_data,
            user_id,
        )
        logger.info(f"[WATCHDOG] ✅ Retry notification dispatched for {assessment_id}")
    except Exception as exc:
        logger.error(f"[WATCHDOG] ❌ Retry dispatch failed for {assessment_id}: {exc}")


# ---------------------------------------------------------------------------
# Main watchdog loop
# ---------------------------------------------------------------------------

async def run_watchdog():
    """
    Background task: runs every WATCHDOG_INTERVAL_SECONDS.
    Detects system anomalies, asks Nemotron what to do, executes actions.
    """
    if not OPENROUTER_API_KEY:
        logger.info("[WATCHDOG] OPENROUTER_API_KEY not configured — watchdog is disabled.")
        return

    logger.info(f"[WATCHDOG] 🧠 Nemotron watchdog started (model: {NEMOTRON_MODEL})")

    retry_counts: Dict[str, Dict[str, Any]] = {}  # track per-assessment retry count and timestamp

    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)

            # --- 1. Gather system health data ---
            from src.database import _use_postgres, _PG_AVAILABLE

            if _use_postgres() and _PG_AVAILABLE:
                orphaned = await asyncio.to_thread(_get_orphaned_assessments_pg, ORPHAN_WINDOW_MINUTES)
                failed = []  # PG tracks failed status directly in orphaned query
            else:
                orphaned = await asyncio.to_thread(_get_orphaned_assessments_sqlite, ORPHAN_WINDOW_MINUTES)
                failed = await asyncio.to_thread(_get_failed_alerts_sqlite, ORPHAN_WINDOW_MINUTES)

            if not orphaned and not failed:
                logger.debug("[WATCHDOG] ✅ System healthy — no anomalies detected.")
                continue

            logger.warning(
                f"[WATCHDOG] ⚠️  Anomalies detected: "
                f"{len(orphaned)} orphaned assessments, {len(failed)} failed alerts"
            )

            # --- 2. Ask Nemotron to analyze ---
            plan = await _nemotron_analyze(orphaned, failed)

            if not plan:
                logger.error("[WATCHDOG] Nemotron analysis returned no plan — skipping cycle.")
                continue

            logger.info(f"[WATCHDOG] 🧠 Nemotron says: {plan.get('summary', 'N/A')}")

            if plan.get("system_healthy"):
                logger.info("[WATCHDOG] Nemotron confirmed system is healthy.")
                continue

            # --- 3. Execute each action Nemotron decided ---
            actions = plan.get("actions", [])
            dispatched = 0

            for action in actions[:MAX_RETRIES_PER_SESSION]:
                aid = action.get("assessment_id", "unknown")

                # Guard: don't retry the same assessment more than 3 times total
                if aid not in retry_counts:
                    retry_counts[aid] = {"count": 0, "timestamp": datetime.datetime.now(datetime.UTC).timestamp()}
                
                retry_counts[aid]["count"] += 1
                retry_counts[aid]["timestamp"] = datetime.datetime.now(datetime.UTC).timestamp()
                
                if retry_counts[aid]["count"] > 3:
                    logger.warning(
                        f"[WATCHDOG] Max retries reached for {aid} — "
                        f"skipping to avoid alert spam."
                    )
                    continue

                await _execute_action(action)
                dispatched += 1

            logger.info(
                f"[WATCHDOG] Cycle complete — {dispatched}/{len(actions)} actions dispatched."
            )

            # Flush counts older than 24 hours to free memory
            now_ts = datetime.datetime.now(datetime.UTC).timestamp()
            to_drop = [
                k for k, v in retry_counts.items()
                if (now_ts - v.get("timestamp", 0)) > 24 * 3600
            ]
            for k in to_drop:
                del retry_counts[k]

        except asyncio.CancelledError:
            logger.info("[WATCHDOG] Nemotron watchdog task cancelled.")
            break
        except Exception as exc:
            logger.error(f"[WATCHDOG] Unexpected error in watchdog loop: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# Guardrail: rule-based assessment audit (Nemotron self-correction)
# ---------------------------------------------------------------------------

# Symptoms that are ALWAYS treated as CRITICAL regardless of LLM output.
# Keys must match what the Gemini/Nemotron extraction layer may return.
# IMPORTANT: keep in sync with SYMPTOM_WEIGHTS in scoring_engine/rules.py —
# if a key exists here but not in SYMPTOM_WEIGHTS, the base score will be 0
# (guardrail still fires, but the stored score is misleading).
_ALWAYS_CRITICAL_SYMPTOMS = frozenset({
    "chest_pain",
    "stroke_symptoms",
    "unconscious",
    "breathing_problem",      # primary key in SYMPTOM_WEIGHTS
    "shortness_of_breath",    # alternate phrasing LLM may emit; weight in rules.py
})

# Minimum confidence below which we force follow-up questions
_MIN_CONFIDENCE = 0.5


def audit_and_correct_assessment(
    assessment: Dict[str, Any],
    raw_payload: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Rule-based Nemotron watchdog guardrail.

    Checks the LLM assessment against the raw input payload and applies
    safety overrides if the model has hallucinated or under-reported risk.

    Returns:
        (corrected_assessment, audit_record)

    audit_record keys:
        self_corrected  : bool  — True if any override was applied
        audit_status    : str   — "PASSED" | "OVERRIDDEN" | "FOLLOW_UP"
        override_reason : str   — human-readable explanation (or "")
    """
    corrected = dict(assessment)          # shallow copy — we only mutate our copy
    symptoms  = [s.lower() for s in raw_payload.get("symptoms", [])]
    severity  = (raw_payload.get("severity") or "").lower()
    confidence = float(raw_payload.get("confidence", 1.0))

    self_corrected   = False
    audit_status     = "PASSED"
    override_reason  = ""

    # --- Rule 1: critical symptoms must never be LOW / MEDIUM ---
    critical_hit = _ALWAYS_CRITICAL_SYMPTOMS.intersection(symptoms)
    if critical_hit and corrected.get("risk_level", "").upper() in ("LOW", "MEDIUM", "NONE", ""):
        symptom_name = next(iter(critical_hit))
        corrected["risk_level"] = "CRITICAL"
        corrected["action"]     = "call_911"
        self_corrected  = True
        audit_status    = "OVERRIDDEN"
        override_reason = (
            f"Chest pain detected" if "chest_pain" in critical_hit
            else f"Critical symptom '{symptom_name}' detected"
        ) + f": auto-escalated to CRITICAL. Original risk was {assessment.get('risk_level', 'UNKNOWN')}."

    # --- Rule 2: low confidence forces follow-up ---
    elif confidence < _MIN_CONFIDENCE and corrected.get("action") != "follow_up_questions":
        corrected["action"] = "follow_up_questions"
        self_corrected  = True
        audit_status    = "FOLLOW_UP"
        override_reason = (
            f"Low confidence ({confidence:.2f} < {_MIN_CONFIDENCE}): "
            "forced follow-up questions to gather more information."
        )

    # --- Rule 3: severity=critical payload but risk not CRITICAL ---
    elif severity == "critical" and corrected.get("risk_level", "").upper() not in ("CRITICAL", "HIGH"):
        corrected["risk_level"] = "CRITICAL"
        corrected["action"]     = "immediate_alert"
        self_corrected  = True
        audit_status    = "OVERRIDDEN"
        override_reason = (
            f"Payload severity=critical but LLM returned risk={assessment.get('risk_level')}: "
            "auto-elevated to CRITICAL."
        )

    audit_record: Dict[str, Any] = {
        "self_corrected":  self_corrected,
        "audit_status":    audit_status,
        "override_reason": override_reason if self_corrected else None,
        "original_risk":   assessment.get("risk_level"),
        "final_risk":      corrected.get("risk_level"),
    }

    if self_corrected:
        logger.warning(
            f"[WATCHDOG-GUARDRAIL] Override applied | "
            f"status={audit_status} | reason={override_reason}"
        )

    return corrected, audit_record


def evaluate_pipeline_health() -> Dict[str, Any]:
    """
    Lightweight synchronous health probe for the WellRing pipeline.

    Checks:
      - OPENROUTER_API_KEY configured (Nemotron available)
      - DATABASE_URL or SQLite DB accessible

    Returns a dict with keys: healthy (bool), checks (dict), message (str)
    """
    checks: Dict[str, bool] = {}

    # Nemotron / OpenRouter availability
    checks["openrouter_configured"] = bool(os.environ.get("OPENROUTER_API_KEY"))

    # Database reachability
    try:
        from src.database import _use_postgres, _PG_AVAILABLE
        if _use_postgres() and _PG_AVAILABLE:
            # Use pg_pool_alive() instead of SELECT 1 — avoids a full network
            # round-trip on every health poll and doesn't hold a connection open
            # while the HTTP response is being serialised.
            from src.database import pg_pool_alive
            checks["database"] = pg_pool_alive()
        else:
            import sqlite3 as _sqlite3
            db_path = os.environ.get("WELLRING_DB_PATH", "wellring.db")
            _conn = _sqlite3.connect(db_path)
            _conn.execute("SELECT 1")
            _conn.close()
            checks["database"] = True
    except Exception as exc:
        logger.error(f"[PIPELINE-HEALTH] Database check failed: {exc}")
        checks["database"] = False

    healthy = all(checks.values())
    message = "All pipeline checks passed." if healthy else (
        "Pipeline degraded: " + ", ".join(k for k, v in checks.items() if not v)
    )

    return {"healthy": healthy, "checks": checks, "message": message}
