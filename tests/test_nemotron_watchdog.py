"""
test_nemotron_watchdog.py
=========================
Tests for Nemotron 70B Watchdog self-healing guardrails, audit logging,
and watchdog endpoints.
"""

from unittest.mock import patch

from src.watchdog import (
    audit_and_correct_assessment,
)
from src.database import (
    log_nemotron_audit,
    get_latest_nemotron_audits,
    get_active_watchdog_logs,
)


def test_audit_and_correct_assessment_valid():
    """Test that a valid assessment passes audit without correction."""
    assessment = {
        "score": 10,
        "risk_level": "LOW",
        "action": "monitor",
        "category": "MEDICATION",
        "explanation": "Missed medicine",
        "follow_up_questions": [],
    }
    raw_payload = {
        "intent": "health_issue",
        "symptoms": ["medicine_missed"],
        "severity": "low",
        "confidence": 1.0,
        "user_id": "test_user_valid",
    }
    
    corrected, audit = audit_and_correct_assessment(assessment, raw_payload)
    
    assert corrected["risk_level"] == "LOW"
    assert corrected["action"] == "monitor"
    assert audit["self_corrected"] is False
    assert audit["override_reason"] is None or audit["override_reason"] == ""


def test_audit_and_correct_assessment_hallucinated_chest_pain():
    """Test that Nemotron watchdog catches hallucinated low risk for chest pain."""
    assessment = {
        "score": 15,
        "risk_level": "LOW",
        "action": "monitor",
        "category": "MEDICATION",
        "explanation": "Underreported risk",
        "follow_up_questions": [],
    }
    raw_payload = {
        "intent": "health_issue",
        "symptoms": ["chest_pain"],
        "severity": "critical",
        "confidence": 0.95,
        "user_id": "test_user_hallucination",
    }
    
    corrected, audit = audit_and_correct_assessment(assessment, raw_payload)
    
    assert corrected["risk_level"] == "CRITICAL"
    assert corrected["action"] == "call_911"
    assert audit["self_corrected"] is True
    assert audit["audit_status"] == "OVERRIDDEN"
    assert "Chest pain detected" in audit["override_reason"]


def test_audit_and_correct_assessment_low_confidence():
    """Test that Nemotron watchdog forces follow-up questions on low confidence."""
    assessment = {
        "score": 25,
        "risk_level": "LOW",
        "action": "monitor",
        "category": "GENERAL",
        "explanation": "Low confidence case",
        "follow_up_questions": [],
    }
    raw_payload = {
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "medium",
        "confidence": 0.3,
        "user_id": "test_user_low_conf",
    }
    
    corrected, audit = audit_and_correct_assessment(assessment, raw_payload)
    
    assert corrected["action"] == "follow_up_questions"
    assert audit["self_corrected"] is True
    assert "Low confidence" in audit["override_reason"]


def test_database_nemotron_audit_logging():
    """Test inserting and retrieving Nemotron audit logs in database."""
    user_id = "test_audit_db_user"
    audit_id = log_nemotron_audit(
        user_id=user_id,
        assessment_id="test_assessment_123",
        original_score=20,
        original_risk="LOW",
        final_score=85,
        final_risk="CRITICAL",
        self_corrected=True,
        override_reason="Hallucination detected by Nemotron 70B Watchdog.",
        raw_payload={"symptoms": ["chest_pain"]},
        audit_status="OVERRIDDEN",
    )
    
    assert audit_id is not None
    logs = get_latest_nemotron_audits(limit=5)
    assert len(logs) > 0
    target_log = next((log for log in logs if log.get("user_id") == user_id or log.get("id") == audit_id), None)
    assert target_log is not None
    assert target_log["final_risk"] == "CRITICAL"
    assert target_log["self_corrected"] is True or target_log["self_corrected"] == 1


def test_get_active_watchdog_logs():
    """Test retrieving combined active watchdog logs for dashboard monitor."""
    user_id = "test_watchdog_dashboard_user"
    log_nemotron_audit(
        user_id=user_id,
        assessment_id="test_asm_456",
        original_score=10,
        original_risk="LOW",
        final_score=90,
        final_risk="CRITICAL",
        self_corrected=True,
        override_reason="Watchdog test override",
        raw_payload={"symptoms": ["stroke_symptoms"]},
        audit_status="OVERRIDDEN",
    )
    
    active_logs = get_active_watchdog_logs(limit=10)
    assert len(active_logs) > 0
    assert any(log.get("user_id") == user_id for log in active_logs)


def test_get_watchdog_logs_endpoint(client):
    """Test GET /watchdog/logs API endpoint."""
    response = client.get("/watchdog/logs?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "audits" in data
    assert "count" in data
    assert isinstance(data["audits"], list)


def test_auto_bolna_outbound_call_trigger_on_critical(client):
    """Test that POST /assess auto-triggers alert pipeline on CRITICAL risk."""
    critical_payload = {
        "intent": "health_issue",
        "symptoms": ["chest_pain", "shortness_of_breath"],
        "severity": "critical",
        "confidence": 0.99,
        "user_id": "user_auto_call_test",
    }

    # trigger_alerts_if_needed is imported into src.main and called via
    # asyncio.to_thread on every /assess response.  Patching at the usage
    # site ensures the mock intercepts the call correctly.
    with patch(
        "src.main.trigger_alerts_if_needed",
        return_value=None,
    ) as mock_alerts:
        response = client.post("/assess", json=critical_payload)
        assert response.status_code == 200
        res_data = response.json()
        assert res_data["risk_level"] in ["CRITICAL", "HIGH"]

        # Verify the alert pipeline was invoked for the CRITICAL assessment
        assert mock_alerts.called
