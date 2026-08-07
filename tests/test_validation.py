"""
test_validation.py
==================
Tests for input validation on POST /assess.

Covers:
    - Invalid severity string → 422
    - Severity case sensitivity (FastAPI should normalise) → 200
    - Confidence out of range (> 1.0, < 0.0) → 422
    - Missing required field (intent, severity) → 422
    - Completely empty body → 422
    - Empty symptoms list (valid — score comes from severity bonus only)
    - Unknown symptom keys (silently ignored, not a crash)
    - Symptoms with mixed valid + unknown keys (unknown filtered out)
"""


# ---------------------------------------------------------------------------
# Severity validation
# ---------------------------------------------------------------------------

def test_invalid_severity_string_returns_422(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "extreme",
        "confidence": 0.9,
    })
    assert r.status_code == 422


def test_severity_uppercase_is_normalised(client):
    """FastAPI validator normalises severity to lowercase — should be 200."""
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "LOW",
        "confidence": 0.9,
    })
    assert r.status_code == 200
    assert r.json()["severity"] == "low"


def test_severity_mixed_case_is_normalised(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "Critical",
        "confidence": 0.9,
    })
    assert r.status_code == 200
    assert r.json()["severity"] == "critical"


# ---------------------------------------------------------------------------
# Confidence validation
# ---------------------------------------------------------------------------

def test_confidence_above_1_returns_422(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "low",
        "confidence": 1.5,
    })
    assert r.status_code == 422


def test_confidence_below_0_returns_422(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "low",
        "confidence": -0.1,
    })
    assert r.status_code == 422


def test_confidence_exactly_0_is_valid(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "severity": "low",
        "confidence": 0.0,
    })
    assert r.status_code == 200
    assert r.json()["score"] == 0


def test_confidence_exactly_1_is_valid(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["medicine_missed"],
        "severity": "low",
        "confidence": 1.0,
    })
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

def test_missing_intent_returns_422(client):
    r = client.post("/assess", json={
        "symptoms": ["dizziness"],
        "severity": "low",
        "confidence": 0.9,
    })
    assert r.status_code == 422


def test_missing_severity_returns_422(client):
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness"],
        "confidence": 0.9,
    })
    assert r.status_code == 422


def test_empty_body_returns_422(client):
    r = client.post("/assess", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Symptom edge cases
# ---------------------------------------------------------------------------

def test_empty_symptoms_list_is_valid(client):
    """
    Empty symptoms is allowed — score is purely from severity bonus.
    severity=critical bonus = 40, confidence=1.0 → score=40.
    """
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": [],
        "severity": "critical",
        "confidence": 1.0,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["score"] == 40
    assert data["symptoms"] == []


def test_unknown_symptoms_are_silently_ignored(client):
    """Unknown symptom keys should not raise an error."""
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["purple_moon", "dancing_stars"],
        "severity": "low",
        "confidence": 1.0,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["symptoms"] == []  # all filtered out
    assert data["score"] == 0      # low bonus = 0, no valid symptoms


def test_mixed_valid_and_unknown_symptoms(client):
    """Valid symptoms are scored; unknown ones are silently dropped."""
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["dizziness", "made_up_symptom"],
        "severity": "low",
        "confidence": 1.0,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["symptoms"] == ["dizziness"]
    # Score >= 20 (dizziness base weight), history may make it higher
    assert data["score"] >= 20


def test_robust_parsing_for_stringified_params(client):
    """Verify that stringified fields and lists from templates are correctly sanitized."""
    r = client.post("/assess", json={
        "intent": "%(intent)s",
        "symptoms": "['fever', 'breathing_problem']",
        "severity": " HIGH ",
        "confidence": "0.95",
        "user_id": "%(user_id)s"
    })
    assert r.status_code == 200
    data = r.json()
    assert "fever" in data["symptoms"]
    assert "breathing_problem" in data["symptoms"]
    assert data["severity"] == "high"
    assert data["confidence"] == 0.95


# ---------------------------------------------------------------------------
# sanitize_assess_payload: placeholder inside a list
# ---------------------------------------------------------------------------

def test_sanitize_drops_placeholder_inside_symptoms_list(client):
    """A list like ["%(symptoms)s", "dizziness"] must drop the placeholder item."""
    r = client.post("/assess", json={
        "intent": "health_issue",
        "symptoms": ["%(symptoms)s", "dizziness"],
        "severity": "low",
        "confidence": 1.0,
    })
    assert r.status_code == 200
    data = r.json()
    assert "dizziness" in data["symptoms"]
    assert not any(str(s).startswith("%") for s in data["symptoms"])


# ---------------------------------------------------------------------------
# Reminder input validation (POST /reminders)
# ---------------------------------------------------------------------------

def test_reminder_rejects_unknown_type(client):
    r = client.post("/reminders", json={
        "type": "teleport", "title": "X", "time": "10:00",
        "frequency": "daily", "phone": "+919004261186",
    })
    assert r.status_code == 422


def test_reminder_rejects_unknown_frequency(client):
    r = client.post("/reminders", json={
        "type": "medicine", "title": "Pill", "time": "10:00",
        "frequency": "hourly", "phone": "+919004261186",
    })
    assert r.status_code == 422


def test_reminder_rejects_bad_time(client):
    r = client.post("/reminders", json={
        "type": "medicine", "title": "Pill", "time": "25:99",
        "frequency": "daily", "phone": "+919004261186",
    })
    assert r.status_code == 422


def test_reminder_rejects_short_phone(client):
    r = client.post("/reminders", json={
        "type": "medicine", "title": "Pill", "time": "10:00",
        "frequency": "daily", "phone": "123",
    })
    assert r.status_code == 422


def test_reminder_accepts_valid_payload(client):
    r = client.post("/reminders", json={
        "type": "medicine", "title": "Amlodipine", "time": "09:00",
        "frequency": "daily", "phone": "+919004261186", "notes": "with food",
    })
    assert r.status_code == 201


def test_reminder_accepts_iso_time_and_call_type(client):
    r = client.post("/reminders", json={
        "type": "call", "title": "Weekly check-in", "time": "2026-08-10T10:00",
        "frequency": "once", "phone": "+919004261186",
    })
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# /config-check requires auth
# ---------------------------------------------------------------------------

def test_config_check_requires_api_key(client):
    r = client.get("/config-check", headers={"X-API-Key": "definitely-wrong"})
    assert r.status_code == 401


def test_config_check_ok_with_valid_key(client):
    r = client.get("/config-check")
    assert r.status_code == 200
    body = r.json()
    assert "BOLNA_AGENT_ID" in body
    # Keys must be masked or marked unconfigured — never a raw secret value.
    for key in ("BOLNA_AGENT_ID", "BOLNA_API_KEY", "GEMINI_API_KEY", "TWILIO_ACCOUNT_SID"):
        val = body[key]
        assert val in ("not_configured", "***") or ("..." in val)


