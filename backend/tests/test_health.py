from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.db.base import Base
from app.main import app


def test_health_check_reports_ready_storage(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'health.db').as_posix()}"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()

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
    get_settings.cache_clear()
