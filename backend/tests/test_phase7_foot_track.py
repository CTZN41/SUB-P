from app.analysis.phase7_pipeline import attach_candidate_foot_tracks


def test_pre_release_foot_track_keeps_same_scene_and_near_player() -> None:
    candidate = {
        "time_ms": 5000, "scene_id": 2, "foot_point_px": [1100, 750],
    }
    records = [
        {"time_ms": 4600, "scene_id": 2, "person": {"xyxy": [1170, 300, 1230, 740]}},
        {"time_ms": 4700, "scene_id": 3, "person": {"xyxy": [1170, 300, 1230, 740]}},
        {"time_ms": 4800, "scene_id": 2, "person": {"xyxy": [90, 300, 150, 740]}},
        {"time_ms": 4900, "scene_id": 2, "person": {"xyxy": [1070, 300, 1130, 750]}},
    ]

    attach_candidate_foot_tracks([candidate], records, 1920, 1080)

    assert candidate["foot_track"] == [
        {"time_ms": 4600, "foot_point_px": [1200.0, 740], "foot_point_clipped": False},
        {"time_ms": 4900, "foot_point_px": [1100.0, 750], "foot_point_clipped": False},
    ]
