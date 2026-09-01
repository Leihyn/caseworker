from fastapi.testclient import TestClient

from app.api import api

client = TestClient(api)


def _reset() -> None:
    assert client.post("/api/cases/demo").status_code == 200


def test_get_case() -> None:
    _reset()
    response = client.get("/api/cases/CW-1042")
    assert response.status_code == 200
    assert response.json()["id"] == "CW-1042"


def test_unknown_case_is_404() -> None:
    assert client.get("/api/cases/CW-9999").status_code == 404


def test_execute_without_approval_is_403() -> None:
    _reset()
    response = client.post("/api/cases/CW-1042/actions/execute")
    assert response.status_code == 403
    assert "approval" in response.json()["detail"].lower()


def test_full_demo_path_over_http() -> None:
    _reset()
    assert client.post("/api/cases/CW-1042/actions/approve", json={"decided_by": "Maya Okeke"}).status_code == 200
    executed = client.post("/api/cases/CW-1042/actions/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "awaiting_response"

    denied = client.post("/api/cases/CW-1042/events/demo-denial")
    assert denied.status_code == 200
    assert denied.json()["status"] == "escalation_ready"

    assert client.post("/api/cases/CW-1042/actions/approve").status_code == 200
    assert client.post("/api/cases/CW-1042/actions/execute").status_code == 200
    resolved = client.post("/api/cases/CW-1042/events/demo-resolution")
    assert resolved.status_code == 200
    assert resolved.json()["resolution"]["amount"] == 184.20


def test_wake_event_idempotent_over_http() -> None:
    _reset()
    client.post("/api/cases/CW-1042/actions/approve")
    client.post("/api/cases/CW-1042/actions/execute")
    first = client.post("/api/cases/CW-1042/events/demo-denial", json={"event_id": "evt-7"}).json()
    second = client.post("/api/cases/CW-1042/events/demo-denial", json={"event_id": "evt-7"}).json()
    assert first["revision"] == second["revision"]
