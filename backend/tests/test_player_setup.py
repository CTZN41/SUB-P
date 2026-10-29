from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, PlayType, Player


def make_client(tmp_path: Path, play_type: PlayType):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work"))
    settings.ensure_storage_directories(); engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}); Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    analysis = Analysis(title="선수 시험", play_type=play_type, original_name="test.mp4", stored_path="", file_size=1, duration_ms=10000, width=320, height=240, fps=30, status=AnalysisStatus.SETUP_REQUIRED, ai_notice_confirmed=True, warnings_accepted=True)
    with factory() as session: session.add(analysis); session.commit(); session.refresh(analysis); analysis_id = analysis.id
    app = FastAPI(); app.state.settings = settings; app.state.session_factory = factory; app.include_router(router)
    return TestClient(app), analysis_id, engine


def test_temporary_names_are_unique_and_limit_is_enforced(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path, PlayType.THREE_ON_THREE)
    with client:
        for _ in range(6): assert client.post(f"/api/analyses/{analysis_id}/players", json={}).status_code == 201
        listed = client.get(f"/api/analyses/{analysis_id}/players").json()
        assert [item["display_name"] for item in listed["players"]] == [f"선수 {letter}" for letter in "ABCDEF"]
        assert client.post(f"/api/analyses/{analysis_id}/players", json={}).status_code == 409
    engine.dispose()


def test_player_assignment_is_saved_and_overlap_is_rejected(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path, PlayType.ONE_ON_ONE)
    with client:
        first = client.post(f"/api/analyses/{analysis_id}/players", json={}).json()
        second = client.post(f"/api/analyses/{analysis_id}/players", json={}).json()
        assigned = client.patch(f"/api/analyses/{analysis_id}/players/{first['id']}", json={"display_name": "철수", "jersey_number": "7", "initial_time_ms": 1000, "initial_box": [.1, .1, .1, .2]})
        assert assigned.status_code == 200 and assigned.json()["display_name"] == "철수"
        overlap = client.patch(f"/api/analyses/{analysis_id}/players/{second['id']}", json={"initial_time_ms": 1000, "initial_box": [.15, .15, .1, .2]})
        assert overlap.status_code == 409
        separate = client.patch(f"/api/analyses/{analysis_id}/players/{second['id']}", json={"initial_time_ms": 1000, "initial_box": [.7, .1, .1, .2]})
        assert separate.status_code == 200
    engine.dispose()


def test_solo_default_player_cannot_be_deleted(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path, PlayType.SOLO)
    factory = sessionmaker(bind=engine)
    with factory() as session: player = Player(analysis_id=analysis_id, display_name="선수 A"); session.add(player); session.commit(); player_id = player.id
    with client:
        assert client.delete(f"/api/analyses/{analysis_id}/players/{player_id}").status_code == 409
        assert client.get(f"/api/analyses/{analysis_id}/players").json()["warnings"] == []
    engine.dispose()
