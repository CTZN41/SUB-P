from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisJob, AnalysisStatus, CalibrationSource, CourtCalibration, JobStatus, PlayType, Player, Shot, ShotResult, ShotStatus, ShotSource, ShotType


def make_client(tmp_path):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work"))
    settings.ensure_storage_directories()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.include_router(router)
    return TestClient(app), factory, settings


def add_record(factory, settings, title="기록", *, status=AnalysisStatus.COMPLETED, video=True, job_status=None):
    with factory() as session:
        analysis = Analysis(title=title, play_type=PlayType.SOLO, original_name="sample.mp4", stored_path="uploads/sample/original.mp4", file_size=4, duration_ms=3000, status=status)
        session.add(analysis)
        session.flush()
        player = Player(analysis_id=analysis.id, display_name="선수 A")
        session.add(player)
        session.flush()
        session.add(Shot(analysis_id=analysis.id, player_id=player.id, candidate_time_ms=1000, sequence_start_ms=0, sequence_end_ms=2000, result=ShotResult.MADE, shot_type=ShotType.SET_SHOT, status=ShotStatus.AUTO, source=ShotSource.AUTO))
        if job_status:
            session.add(AnalysisJob(analysis_id=analysis.id, status=job_status, is_simulation=False))
        session.commit()
        record_id = analysis.id
    folder = settings.upload_path / record_id
    if video:
        folder.mkdir(parents=True)
        (folder / "original.mp4").write_bytes(b"video")
    return record_id


def test_list_persists_and_marks_missing_video(tmp_path):
    client, factory, settings = make_client(tmp_path)
    complete_id = add_record(factory, settings, "정상")
    missing_id = add_record(factory, settings, "원본 없음", video=False)
    with client:
        response = client.get("/api/analyses")
        assert response.status_code == 200
        items = {item["id"]: item for item in response.json()["analyses"]}
        assert items[complete_id]["next_path"] == f"/results/{complete_id}"
        assert items[missing_id]["state_label"] == "원본 영상 없음"
        assert items[missing_id]["video_available"] is False
    # A new API client reads the same database after the first one closes.
    new_client, _, _ = make_client(tmp_path)
    with new_client:
        assert len(new_client.get("/api/analyses").json()["analyses"]) == 2


def test_resume_skips_solo_player_setup_but_keeps_multiplayer_setup(tmp_path):
    client, factory, settings = make_client(tmp_path)
    with factory() as session:
        ids = {}
        for play_type in (PlayType.SOLO, PlayType.ONE_ON_ONE):
            analysis = Analysis(title=play_type.value, play_type=play_type, original_name="sample.mp4", stored_path="uploads/sample/original.mp4", file_size=4, duration_ms=3000, status=AnalysisStatus.SETUP_REQUIRED)
            session.add(analysis)
            session.flush()
            session.add(CourtCalibration(analysis_id=analysis.id, frame_time_ms=0, image_points=[[0, 0], [1, 0], [1, 1], [0, 1]], court_points=[[0, 0], [50, 0], [50, 47], [0, 47]], hoop_point=[.5, .2], transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]], source=CalibrationSource.USER))
            player = Player(analysis_id=analysis.id, display_name="선수 A")
            session.add(player)
            ids[play_type] = (analysis.id, player)
        session.commit()
        solo_id = ids[PlayType.SOLO][0]
        multi_id, multi_player = ids[PlayType.ONE_ON_ONE]
        for analysis_id in (solo_id, multi_id):
            folder = settings.upload_path / analysis_id
            folder.mkdir(parents=True)
            (folder / "original.mp4").write_bytes(b"video")
        with client:
            items = {item["id"]: item for item in client.get("/api/analyses").json()["analyses"]}
            assert items[solo_id]["next_path"] == f"/progress/{solo_id}"
            assert items[multi_id]["next_path"] == f"/players/{multi_id}"
            multi_player.initial_box = [.1, .2, .2, .4]
            session.commit()
            items = {item["id"]: item for item in client.get("/api/analyses").json()["analyses"]}
            assert items[multi_id]["next_path"] == f"/progress/{multi_id}"


def test_delete_removes_related_data_and_files(tmp_path):
    client, factory, settings = make_client(tmp_path)
    record_id = add_record(factory, settings)
    work = settings.work_path / record_id
    work.mkdir()
    (work / "phase7-candidates.json").write_text("{}")
    with client:
        assert client.delete(f"/api/analyses/{record_id}").status_code == 204
        assert client.get(f"/api/analyses/{record_id}").status_code == 404
    with factory() as session:
        assert session.get(Analysis, record_id) is None
        assert session.scalars(select(Player).where(Player.analysis_id == record_id)).all() == []
        assert session.scalars(select(Shot).where(Shot.analysis_id == record_id)).all() == []
    assert not (settings.upload_path / record_id).exists()
    assert not work.exists()


def test_active_job_refuses_delete(tmp_path):
    client, factory, settings = make_client(tmp_path)
    record_id = add_record(factory, settings, job_status=JobStatus.RUNNING)
    with client:
        assert client.delete(f"/api/analyses/{record_id}").status_code == 409
        assert client.get(f"/api/analyses/{record_id}").status_code == 200
    assert (settings.upload_path / record_id / "original.mp4").exists()


def test_file_removal_failure_keeps_retryable_record(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    record_id = add_record(factory, settings)
    def fail(_path):
        raise PermissionError("test")
    monkeypatch.setattr("app.api.analyses.shutil.rmtree", fail)
    with client:
        response = client.delete(f"/api/analyses/{record_id}")
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "PARTIAL_DELETE"
        assert client.get(f"/api/analyses/{record_id}").status_code == 200


def test_second_folder_failure_shows_broken_record_and_can_retry(tmp_path, monkeypatch):
    client, factory, settings = make_client(tmp_path)
    record_id = add_record(factory, settings)
    work = settings.work_path / record_id
    work.mkdir()
    original_rmtree = __import__("shutil").rmtree
    def fail_work(path):
        if path == work:
            raise PermissionError("test")
        original_rmtree(path)
    monkeypatch.setattr("app.api.analyses.shutil.rmtree", fail_work)
    with client:
        response = client.delete(f"/api/analyses/{record_id}")
        assert response.status_code == 500
        item = client.get("/api/analyses").json()["analyses"][0]
        assert item["state_label"] == "원본 영상 없음"
        assert item["video_available"] is False
        monkeypatch.setattr("app.api.analyses.shutil.rmtree", original_rmtree)
        assert client.delete(f"/api/analyses/{record_id}").status_code == 204
