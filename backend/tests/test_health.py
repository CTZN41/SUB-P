from fastapi.testclient import TestClient

from app.main import app


def test_health_check_reports_ready_storage() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["storage"] == {
        "uploads_ready": True,
        "work_ready": True,
        "database_ready": True,
    }
