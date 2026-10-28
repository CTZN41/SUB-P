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
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", upload_dir=str(tmp_path / "uploads"), work_dir=str(tmp_path / "work"))
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
