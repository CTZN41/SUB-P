import numpy as np

from app.services.court_landmark_consensus import court_landmark_consensus


BASE = np.float32([[220, 300], [420, 300], [450, 470], [190, 470], [320, 130]])


def prediction(offset: float, confidence: float = .9):
    return {"points": (BASE + offset).tolist(), "confidence": confidence}


def test_consensus_keeps_stable_cluster_and_ignores_outlier() -> None:
    result = court_landmark_consensus(
        [prediction(-2), prediction(0), prediction(2), prediction(140)], width=640, height=540
    )
    assert result["usable"] is True
    assert result["support"] == 3
    assert result["points"] == BASE.tolist()


def test_consensus_rejects_predictions_that_move_between_frames() -> None:
    result = court_landmark_consensus(
        [prediction(0), prediction(70), prediction(140), prediction(210)], width=900, height=700
    )
    assert result["usable"] is False
    assert result["points"] == []


def test_consensus_rejects_hoop_below_floor_landmarks() -> None:
    invalid = BASE.copy()
    invalid[4] = [320, 500]
    result = court_landmark_consensus(
        [{"points": invalid.tolist(), "confidence": .95}] * 4, width=640, height=540
    )
    assert result["usable"] is False
    assert result["support"] == 0
