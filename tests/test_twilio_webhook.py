"""
test_twilio_webhook.py
======================
Tests for the inbound Twilio WhatsApp webhook (POST /twilio-webhook).

Covers:
    - Happy path: form-encoded inbound message → 200 TwiML reply
      (signature validation skipped when TWILIO_AUTH_TOKEN is unset)
    - Signature enforcement once TWILIO_AUTH_TOKEN is set:
      missing / invalid signature → 403, valid signature → 200
    - Non-form bodies → 400
"""


def _twilio_form_data(**overrides):
    data = {
        "MessageSid": "SM1234567890abcdef",
        "From": "whatsapp:+919876543210",
        "To": "whatsapp:+14155238886",
        "Body": "hello",
        "NumMedia": "0",
    }
    data.update(overrides)
    return data


def test_twilio_webhook_returns_twiml_reply(client, monkeypatch):
    # No TWILIO_AUTH_TOKEN configured → dev mode, validation skipped.
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    r = client.post("/twilio-webhook", data=_twilio_form_data(Body="hii"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<Response>" in r.text
    assert "<Message>" in r.text
    assert "WellRing" in r.text


def test_twilio_webhook_rejects_missing_signature_when_token_set(client, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-auth-token")
    r = client.post("/twilio-webhook", data=_twilio_form_data())
    assert r.status_code == 403


def test_twilio_webhook_rejects_invalid_signature_when_token_set(client, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-auth-token")
    r = client.post(
        "/twilio-webhook",
        data=_twilio_form_data(),
        headers={"X-Twilio-Signature": "not-a-real-signature"},
    )
    assert r.status_code == 403


def test_twilio_webhook_accepts_valid_signature(client, monkeypatch):
    from twilio.request_validator import RequestValidator

    token = "test-auth-token"
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)

    data = _twilio_form_data(Body="all good")
    signature = RequestValidator(token).compute_signature(
        "http://testserver/twilio-webhook", data
    )
    r = client.post(
        "/twilio-webhook",
        data=data,
        headers={"X-Twilio-Signature": signature},
    )
    assert r.status_code == 200
    assert "<Message>" in r.text


def test_twilio_webhook_rejects_non_form_body(client, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    r = client.post("/twilio-webhook", json={"not": "form"})
    assert r.status_code == 400
