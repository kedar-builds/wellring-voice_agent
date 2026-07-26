"""
test_notifications.py
=====================
Tests for alert logging and error message recording in database.py & notifications.py
"""

import unittest
from unittest.mock import patch, MagicMock
import uuid
import datetime
from src.database import log_alert
from src.notifications import send_whatsapp_alert, _twilio_send


class TestNotifications(unittest.TestCase):

    @patch("src.database._use_postgres", return_value=True)
    @patch("src.database._PG_AVAILABLE", True)
    @patch("src.database.get_pg_conn")
    def test_log_alert_pg_with_error_message(self, mock_get_pg_conn, mock_use_pg):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_get_pg_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        test_id = str(uuid.uuid4())
        log_alert(
            interaction_id=test_id,
            timestamp=datetime.datetime.now().isoformat(),
            risk_level="HIGH",
            notification_type="whatsapp",
            status="failed",
            recipient_phone="+1234567890",
            recipient_name="Test Caregiver",
            error_message="Twilio Error 21211: Invalid To Phone Number"
        )

        mock_cursor.execute.assert_called_once()
        args, kwargs = mock_cursor.execute.call_args
        params = kwargs.get("assessment_id") or args[1]
        self.assertEqual(params["error_message"], "Twilio Error 21211: Invalid To Phone Number")
        self.assertEqual(params["status"], "failed")

    @patch("src.notifications._twilio_send", return_value=(False, "Twilio Auth Error"))
    @patch("src.notifications.log_alert")
    def test_send_whatsapp_alert_captures_error(self, mock_log_alert, mock_twilio_send):
        with patch("src.notifications.USE_TWILIO", True):
            test_id = str(uuid.uuid4())
            response_data = {
                "risk_level": "HIGH",
                "score": 80,
                "symptoms": ["chest_pain"],
                "action": "emergency",
                "steps": ["Call 911"]
            }

            result = send_whatsapp_alert(
                interaction_id=test_id,
                response_data=response_data,
                to_phone="+1234567890",
                patient_name="John Doe",
                caregiver_name="Jane Doe"
            )

            self.assertFalse(result)
            mock_log_alert.assert_called_once()
            kwargs = mock_log_alert.call_args.kwargs
            self.assertEqual(kwargs["status"], "failed")
            self.assertEqual(kwargs["error_message"], "Twilio Auth Error")


if __name__ == "__main__":
    unittest.main()
