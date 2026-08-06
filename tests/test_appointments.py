"""
test_appointments.py
====================
Tests for the /appointments CRUD endpoints (frontend dashboard feature):

1. GET /appointments         — list (empty initially)
2. POST /appointments        — book, with the field names the React frontend sends
3. GET after POST            — new appointment present in the list
4. DELETE /appointments/{id} — cancel
5. DELETE missing id         — 404
6. POST without title        — 422
"""


def test_appointments_crud(client):
    # 1. Initially empty
    r = client.get("/appointments")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # 2. Book an appointment (mirrors frontend payload field names)
    r = client.post(
        "/appointments",
        json={
            "title": "Cardiologist Check-up",
            "type": "Doctor",
            "provider": "Dr. Sarah Jenkins",
            "time": "09:30 AM",
            "date": "2026-08-10",
            "location": "City General Hospital",
            "phone": "+919876543210",
        },
    )
    assert r.status_code == 201
    appt_id = r.json()["id"]
    assert appt_id

    # 3. Listed with the expected fields
    r = client.get("/appointments")
    assert r.status_code == 200
    data = r.json()
    assert any(
        a["id"] == appt_id
        and a["title"] == "Cardiologist Check-up"
        and a["provider"] == "Dr. Sarah Jenkins"
        and a["status"] == "upcoming"
        for a in data
    )

    # 4. Cancel it
    r = client.delete(f"/appointments/{appt_id}")
    assert r.status_code == 200
    assert r.json()["message"] == "Appointment cancelled successfully"

    # 5. Gone from the list
    r = client.get("/appointments")
    assert not any(a["id"] == appt_id for a in r.json())

    # 6. Cancelling an unknown id → 404
    r = client.delete(f"/appointments/{appt_id}")
    assert r.status_code == 404


def test_appointments_validation(client):
    # Missing required title → 422
    r = client.post("/appointments", json={"time": "09:30 AM"})
    assert r.status_code == 422

    # Minimal valid payload
    r = client.post("/appointments", json={"title": "Weekly Therapy"})
    assert r.status_code == 201

    # Unknown status is coerced to 'upcoming' rather than rejected by the DB CHECK
    r = client.post(
        "/appointments",
        json={"title": "Physio", "status": "booked"},
    )
    assert r.status_code == 201
    appt_id = r.json()["id"]
    r = client.get("/appointments")
    appt = next(a for a in r.json() if a["id"] == appt_id)
    assert appt["status"] == "upcoming"


def test_appointments_require_api_key():
    """Endpoints must reject requests without a valid X-API-Key."""
    from fastapi.testclient import TestClient
    from src.main import app

    with TestClient(app) as c:
        assert c.get("/appointments").status_code == 401
        assert c.post("/appointments", json={"title": "X"}).status_code == 401
        assert c.delete("/appointments/some-id").status_code == 401
