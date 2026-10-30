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
    candidate_overlaps_range,
    Phase7Canceled,
    merge_candidate_evidence,
    merge_candidates,
    pose_motion_candidates,
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


def test_candidate_crossing_segment_start_is_kept_without_old_context_candidates() -> None:
    crossing = {"time_ms": 99_700, "next_time_ms": 100_200}
    old = {"time_ms": 97_000, "next_time_ms": 97_500}
    inside = {"time_ms": 101_000, "next_time_ms": 101_300}
    assert candidate_overlaps_range(crossing, 100_000, 110_000) is True
    assert candidate_overlaps_range(inside, 100_000, 110_000) is True
    assert candidate_overlaps_range(old, 100_000, 110_000) is False


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


def test_candidate_merge_keeps_rapid_shots_more_than_one_second_apart() -> None:
    first = {"time_ms": 1000, "confidence": .8}
    second = {"time_ms": 2100, "confidence": .7}
    assert merge_candidates([first, second]) == [first, second]


def test_late_rim_evidence_merges_into_same_release() -> None:
    release = {
        "time_ms": 4400, "next_time_ms": 5200, "confidence": .7,
        "foot_point_px": [830, 960], "candidate_source": "trajectory",
        "scene_id": 0, "review_reasons": [],
    }
    rim = {
        "time_ms": 5867, "next_time_ms": 6800, "confidence": .4,
        "foot_point_px": [802, 1076], "candidate_source": "rim_approach_backtrack",
        "scene_id": 0, "review_reasons": ["rim evidence"],
    }
    candidates = merge_candidate_evidence([release, rim], 1920, 1080)
    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 4400
    assert candidates[0]["evidence_end_ms"] == 6800
    assert candidates[0]["candidate_sources"] == ["rim_approach_backtrack", "trajectory"]


def test_overlapping_trajectory_evidence_keeps_later_release() -> None:
    early = {
        "time_ms": 8133, "next_time_ms": 9333, "confidence": .6,
        "foot_point_px": [1675, 841], "candidate_source": "trajectory",
        "scene_id": 0, "review_reasons": [],
    }
    release = {
        "time_ms": 9333, "next_time_ms": 9467, "confidence": .45,
        "foot_point_px": [1362, 844], "candidate_source": "trajectory",
        "scene_id": 0, "review_reasons": [],
    }
    candidates = merge_candidate_evidence([early, release], 1920, 1080)
    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 9333
    assert candidates[0]["confidence"] == .45


def test_weak_ball_only_trace_after_pose_release_is_not_second_shot() -> None:
    release = {
        "time_ms": 9333, "next_time_ms": 9467, "confidence": .7571,
        "foot_point_px": [1361, 845], "candidate_source": "trajectory",
        "scene_id": 0, "review_reasons": [],
    }
    pose = {**release, "candidate_source": "pose_shot_motion"}
    trace = {
        "time_ms": 10333, "next_time_ms": 10467, "confidence": .1465,
        "foot_point_px": [1110, 837], "candidate_source": "trajectory",
        "scene_id": 0, "review_reasons": [],
    }
    candidates = merge_candidate_evidence([release, pose, trace], 1920, 1080)
    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 9333
    assert "같은 슛으로 병합" in candidates[0]["review_reasons"][-1]


def test_second_pose_backed_release_is_preserved() -> None:
    first = {
        "time_ms": 9333, "next_time_ms": 9467, "confidence": .75,
        "foot_point_px": [1361, 845], "candidate_source": "trajectory",
        "scene_id": 0, "review_reasons": [],
    }
    second = {
        **first, "time_ms": 10333, "next_time_ms": 10467,
        "confidence": .65, "foot_point_px": [1110, 837],
    }
    candidates = merge_candidate_evidence([
        first, {**first, "candidate_source": "pose_shot_motion"},
        second, {**second, "candidate_source": "pose_shot_motion"},
    ], 1920, 1080)
    assert len(candidates) == 2


def test_late_release_pose_keeps_its_time_foot_and_confidence() -> None:
    def item(time, next_time, source, confidence, foot):
        return {
            "time_ms": time, "next_time_ms": next_time,
            "frame_index": round(time * .024), "candidate_source": source,
            "confidence": confidence, "foot_point_px": foot,
            "ball_point_px": [300, 100], "scene_id": 0,
            "review_reasons": [],
        }

    candidates = merge_candidate_evidence([
        item(2127, 2502, "pose_shot_motion", .2296, [608, 720]),
        item(2753, 3754, "trajectory", .672, [686, 628]),
        item(3754, 4630, "rim_approach_backtrack", .6966, [561, 514]),
        item(4004, 4004, "pose_shot_motion", .8219, [536, 465]),
    ], 1280, 720)

    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 4004
    assert candidates[0]["foot_point_px"] == [536, 465]
    assert candidates[0]["confidence"] == .8219
    assert candidates[0]["release_basis"] == "pose_after_ball_flight"


def test_existing_release_keeps_trajectory_when_pose_is_before_flight_end() -> None:
    def item(time, next_time, source, confidence, foot):
        return {
            "time_ms": time, "next_time_ms": next_time,
            "frame_index": round(time * .03), "candidate_source": source,
            "confidence": confidence, "foot_point_px": foot,
            "ball_point_px": [300, 100], "scene_id": 0,
            "review_reasons": [],
        }

    candidates = merge_candidate_evidence([
        item(3733, 5200, "pose_shot_motion", .6546, [793, 883]),
        item(4400, 5200, "trajectory", .5102, [830, 961]),
        item(4933, 5200, "pose_shot_motion", .6546, [809, 946]),
        item(5867, 6800, "rim_approach_backtrack", .3855, [802, 1076]),
    ], 1920, 1080)

    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 4400
    assert candidates[0]["foot_point_px"] == [830, 961]
    assert candidates[0]["confidence"] == .5102
    assert candidates[0]["release_basis"] == "trajectory_start"


def test_trajectory_rejects_impossible_ball_jump() -> None:
    person = {"xyxy": [500, 500, 700, 1080], "confidence": .9}
    start = {"xyxy": [590, 640, 610, 660], "confidence": .8}
    jump = {"xyxy": [810, -10, 830, 10], "confidence": .7}
    records = [
        {"time_ms": 1000, "frame_index": 30, "person": person,
         "balls": [start], "tracked_hoop": [600, 100], "hoop_valid": True,
         "scene_id": 0, "rim_missing": False},
        {"time_ms": 1125, "frame_index": 34, "person": person,
         "balls": [jump], "tracked_hoop": [600, 100], "hoop_valid": True,
         "scene_id": 0, "rim_missing": False},
    ]
    assert trajectory_candidates(records, [600, 100], 1080) == []


def test_trajectory_rejects_ball_returning_toward_camera() -> None:
    person = {"xyxy": [100, 120, 200, 360], "confidence": .9}
    start = {"xyxy": [140, 180, 160, 200], "confidence": .8}
    ordinary_flight = {"xyxy": [135, 80, 155, 100], "confidence": .7}
    approaching_camera = {"xyxy": [125, 70, 165, 110], "confidence": .7}

    def records(end_ball):
        return [
            {"time_ms": 1000, "frame_index": 30, "person": person,
             "balls": [start], "tracked_hoop": [280, 60], "hoop_valid": True,
             "scene_id": 0, "rim_missing": False},
            {"time_ms": 1250, "frame_index": 38, "person": person,
             "balls": [end_ball], "tracked_hoop": [280, 60], "hoop_valid": True,
             "scene_id": 0, "rim_missing": False},
        ]

    assert len(trajectory_candidates(records(ordinary_flight), [280, 60], 480)) == 1
    assert trajectory_candidates(records(approaching_camera), [280, 60], 480) == []


def test_pose_motion_requires_preparation_and_later_ball_flight() -> None:
    person = {"xyxy": [100, 100, 200, 400], "confidence": .9}
    lower_pose = {
        "xy": [[0, 0]] * 17,
        "confidence": [0.0] * 17,
    }
    raised_pose = {
        "xy": [[0, 0]] * 17,
        "confidence": [0.0] * 17,
    }
    for pose in (lower_pose, raised_pose):
        for index in (5, 7, 9):
            pose["confidence"][index] = .9
    lower_pose["xy"][5], lower_pose["xy"][7], lower_pose["xy"][9] = [140, 180], [145, 220], [150, 250]
    raised_pose["xy"][5], raised_pose["xy"][7], raised_pose["xy"][9] = [140, 180], [150, 160], [160, 130]
    records = [
        {"time_ms": 0, "frame_index": 0, "person": person, "pose": lower_pose,
         "balls": [{"xyxy": [145, 235, 165, 255], "confidence": .8}],
         "hoop_valid": True, "tracked_hoop": [280, 60], "scene_id": 0},
        {"time_ms": 500, "frame_index": 15, "person": person, "pose": raised_pose,
         "balls": [], "hoop_valid": True, "tracked_hoop": [280, 60], "scene_id": 0},
        {"time_ms": 750, "frame_index": 22, "person": person, "pose": raised_pose,
         "balls": [{"xyxy": [205, 75, 225, 95], "confidence": .7}],
         "hoop_valid": True, "tracked_hoop": [280, 60], "scene_id": 0},
    ]
    candidates = pose_motion_candidates(records, [280, 60], 480)
    assert len(candidates) == 1
    assert candidates[0]["candidate_source"] == "pose_shot_motion"


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


def test_trajectory_candidate_connects_one_second_ball_detection_gap() -> None:
    person = {"xyxy": [100, 120, 180, 360], "confidence": .9}
    start_ball = {"xyxy": [135, 125, 155, 145], "confidence": .8}
    released_ball = {"xyxy": [190, 50, 210, 70], "confidence": .7}
    records = [{
        "time_ms": 1000,
        "frame_index": 30,
        "person": person,
        "balls": [start_ball],
        "hoop_valid": True,
        "rim_missing": False,
        "tracked_hoop": [240, 50],
    }]
    records.extend({
        "time_ms": time_ms,
        "frame_index": 30 + offset,
        "person": person,
        "balls": [],
        "hoop_valid": True,
        "rim_missing": True,
        "tracked_hoop": [240, 50],
    } for offset, time_ms in enumerate(range(1125, 2000, 125), start=1))
    records.append({
        "time_ms": 2000,
        "frame_index": 60,
        "person": person,
        "balls": [released_ball],
        "hoop_valid": True,
        "rim_missing": False,
        "tracked_hoop": [240, 50],
    })

    candidates = trajectory_candidates(records, [240, 50], 480)
    assert len(candidates) == 1
    assert candidates[0]["time_ms"] == 1000


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
    def analyze_with_current_model(*args, **kwargs):
        assert args[1].name == "basketball-yolo26n-pilot-v4.pt"
        return result
    monkeypatch.setattr("app.services.analysis_jobs.analyze_video", analyze_with_current_model)
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
