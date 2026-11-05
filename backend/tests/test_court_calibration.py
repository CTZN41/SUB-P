from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.analyses import router
from app.core.config import Settings
from app.db.base import Base
from app.models import Analysis, AnalysisStatus, CalibrationSource, CourtCalibration, PlayType, Player
from app.services.court_calibration import calculate_transform, transform_point


def make_client(tmp_path: Path):
    settings = Settings(_env_file=None, app_env="test", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work"))
    settings.ensure_storage_directories()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    analysis = Analysis(title="코트 시험", play_type=PlayType.SOLO, original_name="test.mp4", stored_path="", file_size=1, duration_ms=1000, width=320, height=240, fps=30, status=AnalysisStatus.SETUP_REQUIRED, ai_notice_confirmed=True, warnings_accepted=True)
    with factory() as session:
        session.add(analysis); session.commit(); session.refresh(analysis); analysis_id = analysis.id
    folder = settings.upload_path / analysis_id; folder.mkdir(parents=True)
    writer = cv2.VideoWriter(str(folder / "original.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
    for _ in range(10): writer.write(np.full((240, 320, 3), 120, dtype=np.uint8))
    writer.release()
    app = FastAPI(); app.state.settings = settings; app.state.session_factory = factory; app.include_router(router)
    return TestClient(app), analysis_id, engine


def valid_payload():
    return {"frame_time_ms": 0, "image_points": [[20, 220], [300, 220], [240, 30], [80, 30]], "court_points": [[0, 0], [50, 0], [50, 47], [0, 47]], "hoop_point": [160, 45]}


def test_transform_is_stable() -> None:
    transform = calculate_transform([[0, 0], [100, 0], [100, 100], [0, 100]], [[0, 0], [50, 0], [50, 47], [0, 47]])
    assert transform_point(transform, [50, 50]) == [25.0, 23.5]


def test_multiple_views_preserve_other_view_and_optional_arc(tmp_path):
    client, aid, engine = make_client(tmp_path)
    with client:
        first = valid_payload()
        first['three_point_line'] = [[30,220],[40,120],[160,40],[280,120],[290,220]]
        assert client.put(f'/api/analyses/{aid}/calibration', json=first).status_code == 200
        second = valid_payload(); second['frame_time_ms'] = 100
        assert client.put(f'/api/analyses/{aid}/calibration', json=second).status_code == 200
        views = client.get(f'/api/analyses/{aid}/calibrations').json()
        assert len(views) == 2 and views[0]['three_point_line'] == first['three_point_line']
        # The paint-only editor omits the legacy field; no historical data loss.
        paint_update = valid_payload()
        assert client.put(f'/api/analyses/{aid}/calibration',json=paint_update).status_code == 200
        assert client.get(f'/api/analyses/{aid}/calibration').json()['three_point_line'] == first['three_point_line']
        assert client.put(f'/api/analyses/{aid}/calibration', json=second).status_code == 200
        assert len(client.get(f'/api/analyses/{aid}/calibrations').json()) == 2
        invalid = dict(second, three_point_line=[[10,10]] * 5)
        assert client.put(f'/api/analyses/{aid}/calibration', json=invalid).status_code == 400
        assert len(client.get(f'/api/analyses/{aid}/calibrations').json()) == 2
    engine.dispose()


def test_frame_and_calibration_are_saved(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    with client:
        frame = client.get(f"/api/analyses/{analysis_id}/calibration/frame")
        assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
        saved = client.put(f"/api/analyses/{analysis_id}/calibration", json=valid_payload())
        assert saved.status_code == 200
        fetched = client.get(f"/api/analyses/{analysis_id}/calibration")
        assert fetched.status_code == 200
        assert fetched.json()["image_points"] == valid_payload()["image_points"]
    engine.dispose()


def test_partial_straight_three_point_trace_can_be_saved(tmp_path):
    client, aid, engine = make_client(tmp_path)
    payload = valid_payload()
    payload['three_point_line'] = [[50,150],[100,150],[150,150],[200,150],[250,150]]
    with client:
        response = client.put(f'/api/analyses/{aid}/calibration',json=payload)
        assert response.status_code == 200
        assert response.json()['three_point_line'] == payload['three_point_line']
        crossed = dict(payload, three_point_line=[[40,40],[250,180],[40,180],[250,40],[280,200]])
        assert client.put(f'/api/analyses/{aid}/calibration',json=crossed).status_code == 400
        assert client.get(f'/api/analyses/{aid}/calibration').json()['three_point_line'] == payload['three_point_line']
    engine.dispose()


def test_invalid_calibration_is_rejected(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    payload = valid_payload(); payload["image_points"] = [[10, 10], [20, 10], [30, 10]]
    with client:
        response = client.put(f"/api/analyses/{analysis_id}/calibration", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "CALIBRATION_INVALID"
    engine.dispose()


def test_known_old_lane_coordinates_cannot_be_saved_or_reanalyzed(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    payload = valid_payload()
    payload["court_points"] = [[19, 0], [19, 19], [31, 19], [31, 0]]
    with client:
        rejected = client.put(f"/api/analyses/{analysis_id}/calibration", json=payload)
        assert rejected.status_code == 400
        assert "19~31" in rejected.json()["detail"]["message"]
        # Older saved records may still exist; only their new analysis run is blocked.
        factory = sessionmaker(bind=engine)
        with factory() as session:
            session.add(CourtCalibration(
                analysis_id=analysis_id, frame_time_ms=0,
                image_points=payload["image_points"], court_points=payload["court_points"],
                hoop_point=payload["hoop_point"],
                transform=calculate_transform(payload["image_points"], payload["court_points"]),
                source=CalibrationSource.USER,
            ))
            session.add(Player(analysis_id=analysis_id, display_name="선수 A"))
            session.commit()
        blocked = client.post(f"/api/analyses/{analysis_id}/jobs/start-real")
        assert blocked.status_code == 409
        assert "19~31" in blocked.json()["detail"]["message"]
    engine.dispose()


def test_auto_suggestion_endpoint_is_removed(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    with client:
        response = client.get(f"/api/analyses/{analysis_id}/calibration/suggestion")
        assert response.status_code == 404
    engine.dispose()


def test_quality_preview_reports_weak_points_without_saving_a_calibration(tmp_path):
    client, analysis_id, engine = make_client(tmp_path)
    with client:
        result = client.post(f'/api/analyses/{analysis_id}/calibration/quality', json=valid_payload())
        assert result.status_code == 200
        quality = result.json()
        assert quality['usable_count'] == 0 and quality['required_count'] == 3
        assert [p['reason'] for p in quality['landmarks']] == ['image_edge', 'image_edge', 'low_texture', 'low_texture']
        assert client.get(f'/api/analyses/{analysis_id}/calibration').status_code == 404
        payload = valid_payload(); payload['frame_time_ms'] = 1001
        assert client.post(f'/api/analyses/{analysis_id}/calibration/quality', json=payload).status_code == 400
        payload = valid_payload(); payload['image_points'][0] = [-1, 20]
        assert client.post(f'/api/analyses/{analysis_id}/calibration/quality', json=payload).status_code == 400
        assert client.post('/api/analyses/missing/calibration/quality', json=valid_payload()).status_code == 404
    engine.dispose()
