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
from src.notifications import (
    send_whatsapp_alert,
    _is_twilio_quota_error,
    _handle_twilio_quota_error,
    twilio_quota_exhausted,
    _notify_dev_via_webhook,
)


class TestTwilioQuotaDetection(unittest.TestCase):
    """Tests for the Twilio daily-limit detection & dev-alert path."""

    def test_is_twilio_quota_error_detects_63038(self):
        err = "HTTP 400: Account ACxxx exceeded the 50 daily messages limit. Error 63038"
        self.assertTrue(_is_twilio_quota_error(err))

    def test_is_twilio_quota_error_detects_phrasing(self):
        self.assertTrue(_is_twilio_quota_error("Error 63038: daily messages limit"))
        self.assertTrue(_is_twilio_quota_error("Account quota reached"))

    def test_is_twilio_quota_error_false_for_other_failures(self):
        # 63016 (failed send) and 63017 (blocked) are NOT quota errors — they
        # must not suppress watchdog retries or fire quota alarms.
        self.assertFalse(_is_twilio_quota_error("63016: Failed to send WhatsApp message"))
        self.assertFalse(_is_twilio_quota_error("63017: Message blocked"))
        self.assertFalse(_is_twilio_quota_error("Twilio Auth Error"))
        self.assertFalse(_is_twilio_quota_error("Error 21211: Invalid To Phone Number"))
        self.assertFalse(_is_twilio_quota_error(""))
        self.assertFalse(_is_twilio_quota_error(None))

    @patch("src.notifications._notify_dev_via_webhook")
    def test_quota_error_marks_exhausted_and_notifies_dev(self, mock_notify):
        with patch("src.notifications._quota_exhausted_ts", 0.0), \
             patch("src.notifications._last_dev_alert_ts", 0.0):
            _handle_twilio_quota_error("+919999999999", "Error 63038: exceeded the 50 daily messages limit")
            self.assertTrue(twilio_quota_exhausted())
            mock_notify.assert_called_once()

    @patch("src.notifications._notify_dev_via_webhook")
    def test_quota_dev_alert_deduped_within_cooldown(self, mock_notify):
        """A burst of failed sends must not spam the dev webhook."""
        with patch("src.notifications._quota_exhausted_ts", 0.0), \
             patch("src.notifications._last_dev_alert_ts", 0.0):
            _handle_twilio_quota_error("+919999999999", "Error 63038: daily messages limit")
            # immediately call again — still inside the cooldown window
            _handle_twilio_quota_error("+919999999999", "Error 63038: daily messages limit")
            mock_notify.assert_called_once()

    @patch("src.notifications._handle_twilio_quota_error")
    @patch("twilio.rest.Client")
    def test_twilio_send_routes_quota_error_to_handler(self, mock_client, mock_handle):
        """
        End-to-end: a 63038 error raised by the real Twilio client must be
        routed through _handle_twilio_quota_error (critical log + dev alert)
        instead of being treated as a generic send failure.
        """
        from src.notifications import _twilio_send

        mock_client.return_value.messages.create.side_effect = Exception(
            "HTTP 400: Account ACxxx exceeded the 50 daily messages limit. Error 63038"
        )
        with patch("src.notifications.TWILIO_ACCOUNT_SID", "AC123"), \
             patch("src.notifications.TWILIO_AUTH_TOKEN", "tok"), \
             patch("src.notifications.USE_WHATSAPP", True):
            sent, err = _twilio_send("+919999999999", "hello")

        self.assertFalse(sent)
        self.assertIn("63038", err)
        mock_handle.assert_called_once()
        to_phone, err_msg = mock_handle.call_args.args
        self.assertEqual(to_phone, "+919999999999")

    @patch("src.notifications.logger.error")
    @patch("twilio.rest.Client")
    def test_twilio_send_generic_error_not_quota(self, mock_client, mock_logger):
        """Non-quota Twilio errors stay on the generic path (no handler call)."""
        from src.notifications import _twilio_send

        mock_client.return_value.messages.create.side_effect = Exception(
            "Error 21211: Invalid To Phone Number"
        )
        with patch("src.notifications.TWILIO_ACCOUNT_SID", "AC123"), \
             patch("src.notifications.TWILIO_AUTH_TOKEN", "tok"), \
             patch("src.notifications.USE_WHATSAPP", True):
            sent, err = _twilio_send("+919999999999", "hello")

        self.assertFalse(sent)
        mock_logger.assert_called_once()

    @patch("httpx.post")
    def test_notify_dev_via_webhook_posts(self, mock_post):
        # _notify_dev_via_webhook imports httpx locally, so patch the module-level
        # httpx.post — the local import resolves to the real module from sys.modules.
        mock_post.return_value.status_code = 200
        with patch("src.notifications.DEV_ALERT_WEBHOOK_URL", "https://hooks.slack.com/xyz"):
            _notify_dev_via_webhook("Error 63038: quota exceeded")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertIn("Twilio message quota exceeded", kwargs["json"]["text"])

    @patch("src.notifications.logger.error")
    def test_notify_dev_via_webhook_unconfigured_logs_guidance(self, mock_logger):
        with patch("src.notifications.DEV_ALERT_WEBHOOK_URL", ""):
            _notify_dev_via_webhook("Error 63038: quota exceeded")
        mock_logger.assert_called_once()
        self.assertIn("DEV_ALERT_WEBHOOK_URL", mock_logger.call_args.args[0])


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
