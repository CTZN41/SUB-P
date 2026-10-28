from app.analysis.release_candidates import find_candidates


def detection(class_id: int, confidence: float, xyxy: list[float]) -> dict[str, object]:
    x1, y1, x2, y2 = xyxy
    return {
        "class_id": class_id,
        "confidence": confidence,
        "xyxy": xyxy,
        "center": [(x1 + x2) / 2, (y1 + y2) / 2],
    }


def test_release_candidate_when_ball_leaves_upper_body_upward() -> None:
    records = [
        {
            "seconds": 1.0,
            "detections": [
                detection(0, 0.9, [500, 300, 800, 1000]),
                detection(32, 0.8, [640, 330, 700, 390]),
            ],
        },
        {
            "seconds": 1.16,
            "detections": [
                detection(0, 0.9, [500, 300, 800, 1000]),
                detection(32, 0.9, [670, 180, 730, 240]),
            ],
        },
    ]

    candidates = find_candidates(records, max_lookahead=0.25, min_rise_pixels=60, cooldown=2)

    assert len(candidates) == 1
    assert candidates[0]["seconds"] == 1.0


def test_dribble_near_lower_body_is_not_release() -> None:
    records = [
        {
            "seconds": 1.0,
            "detections": [
                detection(0, 0.9, [500, 300, 800, 1000]),
                detection(32, 0.8, [640, 800, 700, 860]),
            ],
        },
        {
            "seconds": 1.16,
            "detections": [
                detection(0, 0.9, [500, 300, 800, 1000]),
                detection(32, 0.9, [650, 650, 710, 710]),
            ],
        },
    ]

    candidates = find_candidates(records, max_lookahead=0.25, min_rise_pixels=60, cooldown=2)

    assert candidates == []
