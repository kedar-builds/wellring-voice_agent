import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from src.main import app

client = TestClient(app)

@pytest.fixture
def mock_dependencies():
    with patch("src.main.get_user_by_phone") as mock_get_user, \
         patch("src.main.get_family_contacts") as mock_get_contacts, \
         patch("src.main.send_unanswered_call_alert") as mock_send_alert, \
         patch("src.main.analyze_transcript_for_health_issues", new_callable=AsyncMock) as mock_analyze, \
         patch("src.main.analyze_emotion_from_audio", new_callable=AsyncMock) as mock_emotion, \
         patch("src.main.process_assessment_data", new_callable=AsyncMock) as mock_process:
        
        # Setup basic mock returns
        mock_get_user.return_value = {"user_id": 1, "name": "John Doe", "phone": "+918421971145"}
        mock_get_contacts.return_value = [{"name": "Jane Doe", "phone": "+919876543210"}]
        mock_analyze.return_value = {"symptoms": ["fever"], "severity": "medium", "intent": "health_check"}
        mock_emotion.return_value = "Neutral"
        
        yield {
            "get_user": mock_get_user,
            "get_contacts": mock_get_contacts,
            "send_alert": mock_send_alert,
            "analyze": mock_analyze,
            "emotion": mock_emotion,
            "process": mock_process
        }

def test_webhook_missed_call(mock_dependencies):
    payload = {
        "status": "no-answer",
        "recipient_phone_number": "+918421971145"
    }
    response = client.post("/bolna-webhook", json=payload)
    assert response.status_code == 200
    
    # Assert that an alert was sent for unanswered call
    mock_dependencies["send_alert"].assert_called_once()
    mock_dependencies["process"].assert_not_called()

def test_webhook_completed_no_data(mock_dependencies):
    payload = {
        "status": "completed",
        "recipient_phone_number": "+918421971145"
    }
    response = client.post("/bolna-webhook", json=payload)
    assert response.status_code == 200
    
    # Assert that missing data defaults to an unanswered call alert
    mock_dependencies["send_alert"].assert_called_once()
    mock_dependencies["process"].assert_not_called()

def test_webhook_completed_with_extraction_data(mock_dependencies):
    payload = {
        "status": "completed",
        "recipient_phone_number": "+918421971145",
        "extraction_data": {
            "symptoms": ["headache"],
            "severity": "low",
            "intent": "health_check"
        }
    }
    response = client.post("/bolna-webhook", json=payload)
    assert response.status_code == 200
    
    # Alert should NOT be sent
    mock_dependencies["send_alert"].assert_not_called()
    
    # Process assessment data SHOULD be called
    mock_dependencies["process"].assert_called_once()
    kwargs = mock_dependencies["process"].call_args.kwargs
    assert kwargs["symptoms"] == ["headache"]
    assert kwargs["severity"] == "low"

def test_webhook_completed_with_transcript(mock_dependencies):
    payload = {
        "status": "completed",
        "recipient_phone_number": "+918421971145",
        "messages": [
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "I have a fever"}
        ]
    }
    response = client.post("/bolna-webhook", json=payload)
    assert response.status_code == 200
    
    # Alert should NOT be sent
    mock_dependencies["send_alert"].assert_not_called()
    
    # Analyze transcript SHOULD be called
    mock_dependencies["analyze"].assert_called_once()
    
    # Process assessment data SHOULD be called
    mock_dependencies["process"].assert_called_once()
    kwargs = mock_dependencies["process"].call_args.kwargs
    assert kwargs["symptoms"] == ["fever"]  # From mock return
    assert kwargs["severity"] == "medium"
    assert "fever" in kwargs["transcript"] # Just checking it passed a string

def test_webhook_missing_phone(mock_dependencies):
    # Test handling when phone is missing from payload
    payload = {
        "status": "completed",
        # no phone
    }
    response = client.post("/bolna-webhook", json=payload)
    assert response.status_code == 200
