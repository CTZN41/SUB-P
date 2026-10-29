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
from app.models import Analysis, AnalysisStatus, PlayType
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
    assert transform_point(transform, [50, 50]) == [25.0, 23.5]


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


def test_invalid_calibration_is_rejected(tmp_path) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    payload = valid_payload(); payload["image_points"] = [[10, 10], [20, 10], [30, 10]]
    with client:
        response = client.put(f"/api/analyses/{analysis_id}/calibration", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "CALIBRATION_INVALID"
    engine.dispose()


def test_auto_suggestion_endpoint_returns_named_points(tmp_path, monkeypatch) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    suggestion = {
        "usable": True,
        "method": "test",
        "confidence": .9,
        "image_points": [[50, 80], [270, 80], [250, 180], [70, 180]],
        "court_points": [[17, 0], [33, 0], [33, 19], [17, 19]],
        "hoop_point": [160, 45],
        "landmark_names": ["베이스라인 왼쪽", "베이스라인 오른쪽", "자유투선 오른쪽", "자유투선 왼쪽"],
        "warnings": [],
    }
    monkeypatch.setattr("app.api.analyses.detect_court_landmarks", lambda frame, model: suggestion)
    with client:
        response = client.get(f"/api/analyses/{analysis_id}/calibration/suggestion")
        assert response.status_code == 200
        assert response.json() == suggestion
    engine.dispose()


def test_auto_suggestion_uses_temporal_fallback_when_rule_result_is_unusable(tmp_path, monkeypatch) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    unavailable = {
        "usable": False, "method": "rule", "confidence": 0.0, "image_points": [],
        "court_points": [], "hoop_point": None,
        "landmark_names": ["베이스라인 왼쪽", "베이스라인 오른쪽", "자유투선 오른쪽", "자유투선 왼쪽"],
        "warnings": ["규칙 실패"],
    }
    temporal = {
        "usable": True, "method": "temporal-yolo-pose-v1", "confidence": .8,
        "image_points": [[50, 80], [270, 80], [250, 180], [70, 180]],
        "court_points": [[17, 0], [33, 0], [33, 19], [17, 19]], "hoop_point": [160, 45],
        "landmark_names": ["베이스라인 왼쪽", "베이스라인 오른쪽", "자유투선 오른쪽", "자유투선 왼쪽"],
        "warnings": ["확인 필요"],
    }
    monkeypatch.setattr("app.api.analyses.detect_court_landmarks", lambda frame, model: unavailable)
    monkeypatch.setattr(
        "app.api.analyses.detect_court_landmarks_temporally", lambda frames, model: temporal
    )
    with client:
        response = client.get(f"/api/analyses/{analysis_id}/calibration/suggestion")
        assert response.status_code == 200
        assert response.json() == temporal
    engine.dispose()


def test_auto_suggestion_tries_target_model_after_general_model_rejects(tmp_path, monkeypatch) -> None:
    client, analysis_id, engine = make_client(tmp_path)
    unavailable = {
        "usable": False, "method": "rule", "confidence": 0.0, "image_points": [],
        "court_points": [], "hoop_point": None,
        "landmark_names": ["베이스라인 왼쪽", "베이스라인 오른쪽", "자유투선 오른쪽", "자유투선 왼쪽"],
        "warnings": ["실패"],
    }
    calls = []

    def temporal_result(frames, model):
        calls.append(model.name)
        if "v5-general" in model.name:
            return unavailable
        return {
            **unavailable, "usable": True, "method": "temporal-yolo-pose-v1", "confidence": .5,
            "image_points": [[50, 80], [270, 80], [250, 180], [70, 180]],
            "court_points": [[17, 0], [33, 0], [33, 19], [17, 19]], "hoop_point": [160, 45],
        }

    monkeypatch.setattr("app.api.analyses.detect_court_landmarks", lambda frame, model: unavailable)
    monkeypatch.setattr("app.api.analyses.detect_court_landmarks_temporally", temporal_result)
    with client:
        response = client.get(f"/api/analyses/{analysis_id}/calibration/suggestion")
        assert response.status_code == 200
        assert response.json()["usable"] is True
        assert calls == [
            "court-keypoints-yolo26n-pose-v5-general.pt",
            "court-keypoints-yolo26n-pose-v6-target-low-lr.pt",
        ]
    engine.dispose()
