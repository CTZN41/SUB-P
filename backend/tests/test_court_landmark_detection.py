from pathlib import Path

import numpy as np

from app.services.court_landmark_detection import (
    _order_lane,
    detect_court_landmarks,
    detect_court_landmarks_temporally,
)


def test_named_lane_points_are_ordered_from_hoop() -> None:
    points = np.asarray([[180, 220], [820, 250], [760, 500], [120, 430]], dtype=np.float32)
    ordered = _order_lane(points, [500, 80])
    assert ordered == [[180.0, 220.0], [820.0, 250.0], [760.0, 500.0], [120.0, 430.0]]


def test_missing_models_never_invent_landmarks(tmp_path: Path) -> None:
    result = detect_court_landmarks(np.zeros((720, 1280, 3), dtype=np.uint8), tmp_path / "missing.pt")
    assert result["usable"] is False
    assert result["image_points"] == []
    assert result["hoop_point"] is None


def test_missing_temporal_model_never_invents_landmarks(tmp_path: Path) -> None:
    frames = [np.zeros((720, 1280, 3), dtype=np.uint8)] * 5
    result = detect_court_landmarks_temporally(frames, tmp_path / "missing.pt")
    assert result["usable"] is False
    assert result["image_points"] == []
    assert result["method"] == "temporal-yolo-pose-v1"
