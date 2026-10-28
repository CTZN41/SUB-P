from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisJob, AnalysisStatus, CalibrationSource, CourtCalibration, JobStatus, Player, PlayType, Shot
from app.services.analysis_jobs import recover_stale_jobs, utc_now


def make_ready_client(tmp_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work")); settings.ensure_storage_directories()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}); Base.metadata.create_all(engine); factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    analysis = Analysis(title="작업 시험", play_type=PlayType.SOLO, original_name="test.mp4", stored_path="", file_size=1, duration_ms=10000, width=320, height=240, fps=30, status=AnalysisStatus.SETUP_REQUIRED, ai_notice_confirmed=True, warnings_accepted=True)
    with factory() as session:
        session.add(analysis); session.flush(); session.add(Player(analysis_id=analysis.id, display_name="선수 A")); session.add(CourtCalibration(analysis_id=analysis.id, frame_time_ms=0, image_points=[[0,0],[1,0],[1,1],[0,1]], court_points=[[0,0],[50,0],[50,47],[0,47]], hoop_point=[.5,.2], transform=[[1,0,0],[0,1,0],[0,0,1]], source=CalibrationSource.USER)); session.commit(); analysis_id = analysis.id
    folder = settings.upload_path / analysis_id; folder.mkdir(parents=True); (folder / "original.mp4").write_bytes(b"video")
    app = FastAPI(); app.state.settings = settings; app.state.session_factory = factory; app.include_router(router)
    return TestClient(app), analysis_id, engine, factory


def test_start_is_idempotent_and_cancel_keeps_source(tmp_path) -> None:
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    with client:
        first = client.post(f"/api/analyses/{analysis_id}/jobs/start"); second = client.post(f"/api/analyses/{analysis_id}/jobs/start")
        assert first.status_code == 200 and second.json()["id"] == first.json()["id"]
        canceled = client.post(f"/api/analyses/{analysis_id}/jobs/{first.json()['id']}/cancel")
        assert canceled.json()["status"] == "canceled"
        retried = client.post(f"/api/analyses/{analysis_id}/jobs/start")
        assert retried.json()["id"] != first.json()["id"]
    with Session(engine) as session:
        assert len(list(session.scalars(select(AnalysisJob)))) == 2
        assert session.scalar(select(Shot)) is None
    assert (tmp_path / "uploads" / analysis_id / "original.mp4").is_file(); engine.dispose()


def test_missing_calibration_blocks_start(tmp_path) -> None:
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    with factory() as session: session.query(CourtCalibration).delete(); session.commit()
    with client:
        response = client.post(f"/api/analyses/{analysis_id}/jobs/start")
        assert response.status_code == 409 and "코트 위치 확인" in response.json()["detail"]["message"]
    engine.dispose()


def test_stale_running_job_is_recovered_as_failed(tmp_path) -> None:
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    old = utc_now() - timedelta(minutes=20)
    with factory() as session:
        job = AnalysisJob(analysis_id=analysis_id, status=JobStatus.RUNNING, stage="선수·공 추적", started_at=old, heartbeat_at=old, is_simulation=True); session.add(job); session.commit()
        assert recover_stale_jobs(session) == 1
        session.refresh(job); analysis = session.get(Analysis, analysis_id)
        assert job.status is JobStatus.FAILED and analysis.status is AnalysisStatus.FAILED
    engine.dispose()


def test_completed_simulation_creates_no_fake_shots(tmp_path) -> None:
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    with client:
        started = client.post(f"/api/analyses/{analysis_id}/jobs/start").json()
        with factory() as session:
            job = session.get(AnalysisJob, started["id"])
            job.started_at = utc_now() - timedelta(seconds=14)
            session.commit()
        completed = client.get(f"/api/analyses/{analysis_id}/jobs/current")
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"
        assert completed.json()["is_simulation"] is True
    with Session(engine) as session:
        assert session.scalar(select(Shot)) is None
        assert session.get(Analysis, analysis_id).status is AnalysisStatus.SETUP_REQUIRED
    engine.dispose()
