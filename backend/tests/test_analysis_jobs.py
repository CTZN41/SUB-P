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
from app.services.analysis_jobs import run_phase7_job
from app.analysis.phase7_pipeline import (
    Phase7Canceled,
    merge_candidates,
    release_candidate,
    remove_static_ball_detections,
    rim_approach_candidates,
    trajectory_candidates,
)


def make_ready_client(tmp_path: Path):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work")); settings.ensure_storage_directories()
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
        assert canceled.json()["processed_ms"] == 0
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


def test_phase7_release_candidates_require_rising_ball_toward_hoop() -> None:
    person = {"xyxy": [90, 100, 210, 400], "confidence": .9}
    ball = {"xyxy": [145, 145, 165, 165], "confidence": .8}
    next_ball = {"xyxy": [175, 95, 195, 115], "confidence": .7}
    before = {"time_ms": 1000, "frame_index": 30, "person": person, "ball": ball, "hoop_valid": True}
    after = {"time_ms": 1250, "ball": next_ball, "hoop_valid": True}
    candidate = release_candidate(before, after, [230, 60], 480)
    assert candidate and candidate["time_ms"] == 1000
    assert candidate["foot_point_px"] == [150, 400]
    assert release_candidate(before, {**after, "hoop_valid": False}, [230, 60], 480) is None
    assert release_candidate(before, {**after, "ball": {**next_ball, "xyxy": [175, 205, 195, 225]}}, [230, 60], 480) is None
    assert len(merge_candidates([candidate, {**candidate, "time_ms": 1500, "confidence": .2}])) == 1


def test_trajectory_candidate_allows_short_detection_gap_and_rejects_catch() -> None:
    person = {"xyxy": [100, 120, 180, 360], "confidence": .9}
    start_ball = {"xyxy": [135, 125, 155, 145], "confidence": .8}
    released_ball = {"xyxy": [170, 70, 190, 90], "confidence": .7}
    records = [
        {"time_ms": 1000, "frame_index": 30, "person": person,
         "balls": [start_ball], "hoop_valid": True, "rim_missing": False,
         "detected_hoop": [240, 50]},
        {"time_ms": 1125, "frame_index": 34, "person": person,
         "balls": [], "hoop_valid": True, "rim_missing": False,
         "detected_hoop": [240, 50]},
        {"time_ms": 1250, "frame_index": 38, "person": person,
         "balls": [released_ball], "hoop_valid": True, "rim_missing": False,
         "detected_hoop": [240, 50]},
    ]
    candidates = trajectory_candidates(records, [240, 50], 480)
    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 1000

    records[2]["balls"] = [{"xyxy": [170, 125, 190, 145], "confidence": .7}]
    assert trajectory_candidates(records, [240, 50], 480) == []


def test_static_ball_overlay_is_removed() -> None:
    overlay = {"xyxy": [300, 200, 320, 220], "confidence": .9}
    moving = {"xyxy": [100, 100, 120, 120], "confidence": .8}
    records = [
        {"balls": [overlay, moving] if index == 0 else [overlay]}
        for index in range(10)
    ]
    removed = remove_static_ball_detections(records, 400, 240)
    assert removed == 10
    assert records[0]["balls"] == [moving]


def test_rim_approach_backtracks_when_release_ball_is_missing() -> None:
    person = {"xyxy": [100, 120, 180, 360], "confidence": .9}
    records = [
        {
            "time_ms": time_ms,
            "frame_index": index,
            "person": person,
            "balls": [],
            "hoop_valid": True,
            "detected_hoop": [240, 50],
        }
        for index, time_ms in enumerate((0, 250, 500, 750, 1000, 1250))
    ]
    records[-1]["balls"] = [{"xyxy": [225, 40, 245, 60], "confidence": .8}]
    candidates = rim_approach_candidates(records, [240, 50], 480)
    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 250
    assert candidates[0]["next_time_ms"] == 1250
    assert candidates[0]["candidate_source"] == "rim_approach_backtrack"


def test_phase8_worker_saves_candidates_as_reviewable_shots(tmp_path, monkeypatch) -> None:
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    with factory() as session:
        job = AnalysisJob(analysis_id=analysis_id, status=JobStatus.QUEUED, stage="영상 준비", is_simulation=False)
        session.add(job); session.commit(); job_id = job.id
    result = {"candidates": [{"time_ms": 1000, "foot_point_px": [150, 400]}], "issues": {}}
    monkeypatch.setattr("app.services.analysis_jobs.analyze_video", lambda *args, **kwargs: result)
    run_phase7_job(factory, client.app.state.settings, analysis_id, job_id)
    with factory() as session:
        assert session.get(AnalysisJob, job_id).status is JobStatus.SUCCEEDED
        assert session.get(Analysis, analysis_id).status is AnalysisStatus.REVIEW_REQUIRED
        shot = session.scalar(select(Shot))
        assert shot is not None
        assert shot.result.value == "needs_review"
        assert shot.shot_type.value == "needs_review"
        assert shot.status.value == "needs_review"
    assert (tmp_path / "work" / analysis_id / "phase7-candidates.json").is_file()
    engine.dispose()


def test_phase7_cancel_discards_partial_result(tmp_path, monkeypatch) -> None:
    client, analysis_id, engine, factory = make_ready_client(tmp_path)
    with factory() as session:
        job = AnalysisJob(analysis_id=analysis_id, status=JobStatus.QUEUED, stage="영상 준비", is_simulation=False)
        session.add(job); session.commit(); job_id = job.id
    def stop(*args, **kwargs):
        raise Phase7Canceled("취소")
    monkeypatch.setattr("app.services.analysis_jobs.analyze_video", stop)
    run_phase7_job(factory, client.app.state.settings, analysis_id, job_id)
    with factory() as session:
        assert session.get(AnalysisJob, job_id).status is JobStatus.CANCELED
        assert session.get(Analysis, analysis_id).status is AnalysisStatus.CANCELED
    assert not (tmp_path / "work" / analysis_id / "phase7-candidates.json").exists()
    engine.dispose()
