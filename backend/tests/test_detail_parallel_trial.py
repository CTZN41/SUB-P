"""Trial-only checks: retries remain sequential inside each candidate."""
from copy import deepcopy
from pathlib import Path
from threading import Lock
from time import sleep

from app.core.config import Settings
from app.services import openai_shot_review as review


def decision(**changes):
    values = dict(is_shot=True, confidence=.9, is_shot_confidence=1,
                  outcome="made", outcome_confidence=.9, shot_type="set_shot",
                  shot_type_confidence=.9, court_zone="mid_center", court_x=25,
                  court_y=22, position_confidence=.9, position_reason="visible line",
                  visible_evidence=["rim_passage"], sequence_start_offset_ms=-2000,
                  sequence_end_offset_ms=2000, review_required=False, reason="test")
    values.update(changes)
    return review.ShotReviewDecision(**values)


def test_parallel_retries_preserve_order_usage_and_decisions(monkeypatch):
    context = review.CameraContextDecision(camera_view="top_of_court", confidence=.9,
        court_left_direction_on_screen="left", toward_half_court_direction_on_screen="down",
        orientation_summary="fixed", visible_landmarks=["paint"], reason="visible")
    monkeypatch.setattr(review, "_build_shared_camera_contexts", lambda *a: ({0: context}, [], 0))
    monkeypatch.setattr(review, "candidate_frame_inputs", lambda video, time, *a: time)
    monkeypatch.setattr(review, "candidate_outcome_frame_inputs", lambda video, time, *a: time)
    lock, active, peak = Lock(), [0], [0]
    def primary(client, model, images, **kwargs):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            sleep(.01)
            if images == 1000:
                return decision(outcome="unclear", outcome_confidence=.2), {"input_tokens": 10, "output_tokens": 2}
            return decision(court_y=17), {"input_tokens": 10, "output_tokens": 2}
        finally:
            with lock:
                active[0] -= 1
    monkeypatch.setattr(review, "_review_once", primary)
    monkeypatch.setattr(review, "_review_outcome_once", lambda client, model, images: (
        review.OutcomeReviewDecision(outcome="made" if model == "fallback" else "unclear",
                                     confidence=.9 if model == "fallback" else .2,
                                     visible_evidence=["rim_passage"], review_required=False, reason="test"),
        {"input_tokens": 4, "output_tokens": 1},
    ))
    monkeypatch.setattr(review, "_review_position_once", lambda *a, **kw: (
        review.PositionReviewDecision(court_zone="mid_center", court_x=25, court_y=22,
                                      confidence=.9, reason="free throw line", camera_view="top_of_court",
                                      overhead_lateral_relation="aligned_with_lane", distance_band="midrange",
                                      projection_evidence=["free throw line"]),
        {"input_tokens": 5, "output_tokens": 1},
    ))
    settings = Settings(_env_file=None, openai_api_key="test", openai_vision_model="primary",
                        openai_vision_fallback_model="fallback")
    source = {"candidates": [{"time_ms": 1000}, {"time_ms": 2000}]}
    a = review.review_candidates_with_openai(Path("unused"), deepcopy(source), settings,
                                           client=object(), detail_workers=1)
    peak[0] = 0
    activity = []
    def record_activity(candidate, task):
        with lock:
            activity.append((candidate, task))
    b = review.review_candidates_with_openai(Path("unused"), deepcopy(source), settings,
                                           client=object(), detail_workers=2, review_activity=record_activity)
    assert {'camera_context', 'prepare_images', 'shot_review', 'outcome_review', 'position_review'} <= {task for _, task in activity}
    last_activity = dict(activity)
    assert all(task is None for task in last_activity.values())
    assert peak[0] == 2
    for result in (a, b):
        for candidate in result["candidates"]:
            assert candidate.pop("detail_trial_metrics")["steps"]
    assert a == b
    assert a["ai_review"]["escalated"] == 1
    assert a["ai_review"]["usage"] == {"input_tokens": 33, "output_tokens": 7}
    assert [x["task"] for x in a["candidates"][0]["ai_review"]["attempts"]] == [
        "shot_review", "outcome_only", "outcome_only",
    ]
    assert [x["task"] for x in a["candidates"][1]["ai_review"]["attempts"]] == [
        "shot_review", "position_consistency",
    ]
