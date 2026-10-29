from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Analysis,
    AnalysisStatus,
    Player,
    PlayType,
    Shot,
    ShotResult,
    ShotSource,
    ShotStatus,
    ShotType,
)


def make_client(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        upload_dir=str(tmp_path / "uploads"),
        work_dir=str(tmp_path / "work"),
    )
    settings.ensure_storage_directories()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        analysis = Analysis(
            title="Phase 9 시험",
            play_type=PlayType.SOLO,
            original_name="test.mp4",
            stored_path="",
            file_size=5,
            duration_ms=10_000,
            fps=60,
            status=AnalysisStatus.REVIEW_REQUIRED,
            ai_notice_confirmed=True,
            warnings_accepted=True,
        )
        session.add(analysis)
        session.flush()
        player = Player(analysis_id=analysis.id, display_name="선수 A")
        session.add(player)
        session.flush()
        shot = Shot(
            analysis_id=analysis.id,
            player_id=player.id,
            candidate_time_ms=3000,
            sequence_start_ms=1000,
            sequence_end_ms=5000,
            court_x=None,
            court_y=None,
            zone_code=None,
            result=ShotResult.NEEDS_REVIEW,
            shot_type=ShotType.NEEDS_REVIEW,
            status=ShotStatus.NEEDS_REVIEW,
            source=ShotSource.AUTO,
            review_reason="OpenAI 응답을 확인하지 못했습니다.",
        )
        session.add(shot)
        session.commit()
        ids = analysis.id, player.id, shot.id
    video_folder = settings.upload_path / ids[0]
    video_folder.mkdir(parents=True)
    (video_folder / "original.mp4").write_bytes(b"phase9-video")
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(router)
    return TestClient(app), engine, ids


def test_review_update_add_delete_and_statistics(tmp_path) -> None:
    client, engine, (analysis_id, player_id, shot_id) = make_client(tmp_path)
    with client:
        listed = client.get(f"/api/analyses/{analysis_id}/shots")
        assert listed.status_code == 200
        assert listed.json()["shots"][0]["status"] == "needs_review"
        assert listed.json()["statistics"]["overall"]["success_rate"] is None

        confirmed = client.patch(
            f"/api/analyses/{analysis_id}/shots/{shot_id}",
            json={
                "player_id": player_id,
                "candidate_time_ms": 3000,
                "sequence_start_ms": 1000,
                "sequence_end_ms": 5000,
                "court_x": 25,
                "court_y": 6,
                "result": "made",
                "shot_type": "layup",
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "user_confirmed"
        assert confirmed.json()["zone_code"] == "restricted"

        created = client.post(
            f"/api/analyses/{analysis_id}/shots",
            json={
                "player_id": player_id,
                "candidate_time_ms": 8000,
                "sequence_start_ms": 6000,
                "sequence_end_ms": 10_000,
                "court_x": 3,
                "court_y": 8,
                "result": "missed",
                "shot_type": "set_shot",
            },
        )
        assert created.status_code == 201
        assert created.json()["source"] == "user"

        complete = client.get(f"/api/analyses/{analysis_id}/shots").json()
        assert complete["analysis_status"] == "completed"
        assert complete["statistics"]["overall"] == {
            "attempts": 2,
            "made": 1,
            "missed": 1,
            "success_rate": 50.0,
            "attempt_share": None,
            "difference_from_overall": None,
        }

        deleted = client.delete(f"/api/analyses/{analysis_id}/shots/{created.json()['id']}")
        assert deleted.status_code == 204
        assert client.get(f"/api/analyses/{analysis_id}/shots").json()["statistics"]["overall"]["attempts"] == 1
    engine.dispose()


def test_invalid_time_position_player_and_unconfirmed_values_are_rejected(tmp_path) -> None:
    client, engine, (analysis_id, player_id, shot_id) = make_client(tmp_path)
    base = {
        "player_id": player_id,
        "candidate_time_ms": 3000,
        "sequence_start_ms": 5000,
        "sequence_end_ms": 1000,
        "court_x": 25,
        "court_y": 6,
        "result": "made",
        "shot_type": "layup",
    }
    with client:
        assert client.patch(f"/api/analyses/{analysis_id}/shots/{shot_id}", json=base).status_code == 400
        invalid_player = {**base, "sequence_start_ms": 1000, "sequence_end_ms": 5000, "player_id": "missing"}
        assert client.patch(f"/api/analyses/{analysis_id}/shots/{shot_id}", json=invalid_player).status_code == 400
        still_review = {**base, "sequence_start_ms": 1000, "sequence_end_ms": 5000, "result": "needs_review"}
        assert client.patch(f"/api/analyses/{analysis_id}/shots/{shot_id}", json=still_review).status_code == 400
    engine.dispose()


def test_original_video_is_available_for_sequence_playback(tmp_path) -> None:
    client, engine, (analysis_id, _, _) = make_client(tmp_path)
    with client:
        response = client.get(f"/api/analyses/{analysis_id}/video")
        assert response.status_code == 200
        assert response.content == b"phase9-video"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-type"].startswith("video/mp4")
    engine.dispose()
