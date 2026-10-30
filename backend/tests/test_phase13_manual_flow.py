from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, PlayType


def test_three_on_three_manual_shooters_and_player_statistics(tmp_path):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work"))
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        analysis = Analysis(title="3대3 수동 시험", play_type=PlayType.THREE_ON_THREE, original_name="game.mp4", stored_path="uploads/game/original.mp4", file_size=1, duration_ms=20000, status=AnalysisStatus.SETUP_REQUIRED)
        session.add(analysis)
        session.commit()
        analysis_id = analysis.id
    folder = settings.upload_path / analysis_id
    folder.mkdir(parents=True)
    (folder / "original.mp4").write_bytes(b"test")
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(router)
    with TestClient(app) as client:
        players = [client.post(f"/api/analyses/{analysis_id}/players", json={}).json() for _ in range(6)]
        assert [player["display_name"] for player in players] == [f"선수 {letter}" for letter in "ABCDEF"]
        assert client.post(f"/api/analyses/{analysis_id}/players", json={}).status_code == 409
        first = {"player_id": players[0]["id"], "candidate_time_ms": 4000, "sequence_start_ms": 2000, "sequence_end_ms": 6000, "court_x": 25, "court_y": 18, "result": "made", "shot_type": "set_shot"}
        second = {**first, "player_id": players[1]["id"], "candidate_time_ms": 10000, "sequence_start_ms": 8000, "sequence_end_ms": 12000, "result": "missed"}
        shot_a = client.post(f"/api/analyses/{analysis_id}/shots", json=first)
        shot_b = client.post(f"/api/analyses/{analysis_id}/shots", json=second)
        assert shot_a.status_code == shot_b.status_code == 201
        all_shots = client.get(f"/api/analyses/{analysis_id}/shots").json()
        assert all_shots["statistics"]["overall"]["attempts"] == 2
        assert all_shots["statistics"]["by_player"][players[0]["id"]]["made"] == 1
        assert client.get(f"/api/analyses/{analysis_id}/shots", params={"player_id": players[1]["id"]}).json()["statistics"]["overall"]["attempts"] == 1
        changed = client.patch(f"/api/analyses/{analysis_id}/shots/{shot_b.json()['id']}", json={"player_id": players[0]["id"]})
        assert changed.status_code == 200
        updated = client.get(f"/api/analyses/{analysis_id}/shots").json()
        assert updated["statistics"]["by_player"][players[0]["id"]]["attempts"] == 2
        assert players[1]["id"] not in updated["statistics"]["by_player"]
        history = client.get("/api/analyses").json()["analyses"][0]
        assert history["state_label"] == "수동 기록"
