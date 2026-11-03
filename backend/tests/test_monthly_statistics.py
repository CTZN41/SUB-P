from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.config import Settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, PlayType, Player, Shot, ShotResult, ShotSource, ShotStatus, ShotType


def test_monthly_results_use_recording_date_and_exclude_non_shot(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'monthly.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        recorded = Analysis(title="Recorded", play_type=PlayType.SOLO, recorded_date=date(2026, 9, 30), original_name="a.mp4", stored_path="a", file_size=1, duration_ms=1000, status=AnalysisStatus.COMPLETED, created_at=datetime(2026, 10, 1, 1, tzinfo=timezone.utc))
        registered = Analysis(title="Registered", play_type=PlayType.SOLO, original_name="b.mp4", stored_path="b", file_size=1, duration_ms=1000, status=AnalysisStatus.COMPLETED, created_at=datetime(2026, 9, 30, 16, tzinfo=timezone.utc))
        session.add_all([recorded, registered]); session.flush()
        first = Player(analysis_id=recorded.id, display_name="A")
        second = Player(analysis_id=registered.id, display_name="B")
        session.add_all([first, second]); session.flush()
        session.add_all([
            Shot(analysis_id=recorded.id, player_id=first.id, candidate_time_ms=100, sequence_start_ms=0, sequence_end_ms=500, result=ShotResult.MADE, shot_type=ShotType.SET_SHOT, status=ShotStatus.AUTO, source=ShotSource.AUTO, zone_code="three_center"),
            Shot(analysis_id=registered.id, player_id=second.id, candidate_time_ms=100, sequence_start_ms=0, sequence_end_ms=500, result=ShotResult.MISSED, shot_type=ShotType.SET_SHOT, status=ShotStatus.AUTO, source=ShotSource.AUTO, zone_code="three_center"),
            Shot(analysis_id=registered.id, player_id=second.id, candidate_time_ms=600, sequence_start_ms=500, sequence_end_ms=900, result=ShotResult.NEEDS_REVIEW, shot_type=ShotType.NEEDS_REVIEW, status=ShotStatus.NEEDS_REVIEW, source=ShotSource.AUTO, confidence_data={"shot_gate": {"verdict": "not_shot"}}),
        ])
        session.commit()
    app = FastAPI(); app.state.settings = Settings(_env_file=None, app_env='test'); app.state.session_factory = factory; app.include_router(router)
    with TestClient(app) as client:
        october = client.get("/api/analyses/monthly").json()
        september = client.get("/api/analyses/monthly?month=2026-09").json()
        assert client.get("/api/analyses/monthly?month=2026-13").status_code == 422
    assert october["available_months"] == ["2026-10", "2026-09"]
    assert october["month"] == "2026-10"
    assert october["analysis_count"] == 1
    assert october["statistics"]["overall"]["attempts"] == 1
    assert october["statistics"]["overall"]["missed"] == 1
    assert october["statistics"]["by_zone"]["three_center"]["attempts"] == 1
    assert len(october["shots"]) == 1
    assert october["shots"][0]["result"] == "missed"
    assert october["shots"][0]["analysis_title"] == "Registered"
    assert september["analysis_count"] == 1
    assert september["statistics"]["overall"]["made"] == 1
    engine.dispose()
