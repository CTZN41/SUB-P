from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.openai_shot_review import (
    CameraContextDecision,
    OutcomeReviewDecision,
    PositionReviewDecision,
    ShotReviewDecision,
    _build_shared_camera_contexts,
    _camera_context_is_unclear,
    review_candidates_with_openai,
)


def camera_context() -> CameraContextDecision:
    return CameraContextDecision(
        camera_view="left_sideline",
        confidence=.9,
        court_left_direction_on_screen="lower_left",
        toward_half_court_direction_on_screen="left",
        orientation_summary="fixed court mapping",
        visible_landmarks=["hoop", "paint", "three-point arc"],
        reason="stable fixed camera",
    )


def test_camera_orientation_keeps_75_percent_threshold() -> None:
    context = camera_context()
    assert _camera_context_is_unclear(context.model_copy(update={"confidence": .72}))
    assert not _camera_context_is_unclear(context.model_copy(update={"confidence": .75}))


@pytest.fixture(autouse=True)
def use_shared_camera_context(monkeypatch):
    shared = camera_context()
    monkeypatch.setattr(
        "app.services.openai_shot_review._build_shared_camera_contexts",
        lambda client, video_path, candidates, settings: (
            {int(candidate.get("scene_id", 0)): shared for candidate in candidates},
            [{"scene_id": 0, "status": "resolved", "decision": shared.model_dump()}],
            0,
        ),
    )


class FakeResponses:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        decision = next(self.decisions)
        return SimpleNamespace(
            output_parsed=decision,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
        )


def decision(
    confidence: float,
    review_required: bool,
    *,
    is_shot: bool = True,
    outcome: str | None = None,
    shot_type: str = "set_shot",
) -> ShotReviewDecision:
    return ShotReviewDecision(
        is_shot=is_shot,
        confidence=confidence,
        is_shot_confidence=confidence,
        outcome=outcome or ("unclear" if review_required or not is_shot else "made"),
        outcome_confidence=confidence,
        shot_type=shot_type,
        shot_type_confidence=confidence,
        court_zone="three_center",
        court_x=25,
        court_y=30,
        position_confidence=.9,
        position_reason="visible court lines",
        visible_evidence=["release"],
        sequence_start_offset_ms=-2000,
        sequence_end_offset_ms=2000,
        review_required=review_required,
        reason="test",
    )


def outcome_decision(
    confidence: float,
    review_required: bool,
    *,
    outcome: str = "made",
) -> OutcomeReviewDecision:
    return OutcomeReviewDecision(
        outcome=outcome,
        confidence=confidence,
        visible_evidence=["rim_passage"] if outcome == "made" else ["rim_deflection"],
        review_required=review_required,
        reason="outcome test",
    )


def position_decision(
    confidence: float,
    *,
    zone: str = "three_center",
    court_x: float | None = 25,
    court_y: float | None = 30,
) -> PositionReviewDecision:
    return PositionReviewDecision(
        court_zone=zone,
        court_x=court_x,
        court_y=court_y,
        confidence=confidence,
        reason="position test",
        camera_view="left_sideline",
        overhead_lateral_relation="aligned_with_lane",
        distance_band="three_point",
        projection_evidence=["lane edges", "arc apex"],
    )


def test_missing_key_skips_review(tmp_path) -> None:
    settings = Settings(_env_file=None, openai_api_key=None)
    result = review_candidates_with_openai(tmp_path / "video.mp4", {"candidates": []}, settings)
    assert result["ai_review"]["status"] == "not_configured"


def test_camera_context_is_called_once_and_shared_by_scene(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([camera_context()])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.camera_context_frame_inputs",
        lambda *args, **kwargs: [
            {"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"},
        ],
    )

    contexts, records, escalated = _build_shared_camera_contexts(
        client,
        tmp_path / "video.mp4",
        [{"time_ms": 1000, "scene_id": 4}, {"time_ms": 3000, "scene_id": 4}],
        Settings(_env_file=None, openai_api_key="test-key"),
    )

    assert len(responses.calls) == 1
    assert contexts[4] is not None
    assert contexts[4].camera_view == "left_sideline"
    assert records[0]["status"] == "resolved"
    assert escalated == 0


def test_unclear_terra_camera_context_stays_unresolved_without_sol(tmp_path, monkeypatch) -> None:
    terra = camera_context()
    terra.camera_view = "unclear"
    terra.confidence = .5
    terra.court_left_direction_on_screen = "unclear"
    responses = FakeResponses([terra])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.camera_context_frame_inputs",
        lambda *args, **kwargs: [
            {"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"},
        ],
    )

    contexts, records, escalated = _build_shared_camera_contexts(
        client,
        tmp_path / "video.mp4",
        [{"time_ms": 1000, "scene_id": 2}],
        Settings(_env_file=None, openai_api_key="test-key"),
    )

    assert [call["model"] for call in responses.calls] == ["gpt-6.1-terra"]
    assert contexts[2] is None
    assert records[0]["status"] == "needs_review"
    assert escalated == 0


def test_ambiguous_outcome_escalates_once_to_sol_outcome_review(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([
        decision(.5, True),
        outcome_decision(.5, True, outcome="unclear"),
        outcome_decision(.9, False, outcome="missed"),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_outcome_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    settings = Settings(_env_file=None, openai_api_key="test-key")
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        settings,
        client=client,
    )
    assert [call["model"] for call in responses.calls] == [
        "gpt-6.1-terra",
        "gpt-6.1-terra",
        "gpt-6.1-sol",
    ]
    assert all("astra" not in call["model"] for call in responses.calls)
    assert result["candidates"][0]["ai_review"]["model"] == "gpt-6.1-sol"
    assert [attempt["task"] for attempt in result["candidates"][0]["ai_review"]["attempts"]] == [
        "shot_review",
        "outcome_only",
        "outcome_only",
    ]
    assert result["candidates"][0]["ai_review"]["outcome"] == "missed"
    assert result["candidates"][0]["ai_review"]["review_required"] is True
    assert result["ai_review"]["escalated"] == 1


def test_matching_terra_and_sol_results_are_auto_consensus(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([
        decision(.6, True, outcome="made"),
        decision(.9, False, outcome="made"),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=client,
    )
    review = result["candidates"][0]["ai_review"]
    assert review["outcome"] == "made"
    assert review["shot_type"] == "set_shot"
    assert review["review_required"] is False
    assert review["consensus"] == {
        "shot_agrees": True,
        "shot_type_agrees": True,
        "outcome_agrees": True,
    }


def test_unclear_outcome_preserves_agreed_shot_type(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([
        decision(.8, True, outcome="unclear", shot_type="dribble_pull_up"),
        outcome_decision(.8, True, outcome="unclear"),
        outcome_decision(.9, True, outcome="unclear"),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_outcome_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=client,
    )
    review = result["candidates"][0]["ai_review"]
    assert review["is_shot"] is True
    assert review["shot_type"] == "dribble_pull_up"
    assert review["outcome"] == "unclear"
    assert review["review_required"] is True


def test_outcome_review_runs_even_when_shot_type_is_low_confidence(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([
        decision(.5, True, outcome="unclear", shot_type="needs_review"),
        outcome_decision(.95, False, outcome="made"),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_outcome_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )

    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=client,
    )

    review = result["candidates"][0]["ai_review"]
    assert [attempt["task"] for attempt in review["attempts"]] == ["shot_review", "outcome_only"]
    assert review["outcome"] == "made"
    assert review["shot_type"] == "needs_review"
    assert review["review_required"] is True


def test_unclear_terra_position_is_not_sent_to_sol(tmp_path, monkeypatch) -> None:
    primary = decision(.9, True, outcome="made")
    primary.court_zone = "needs_review"
    primary.court_x = None
    primary.court_y = None
    primary.position_confidence = .4
    responses = FakeResponses([primary])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )

    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=client,
    )

    review = result["candidates"][0]["ai_review"]
    assert [attempt["task"] for attempt in review["attempts"]] == ["shot_review"]
    assert review["model"] == "gpt-6.1-terra"
    assert review["court_zone"] == "needs_review"
    assert review["court_x"] is None and review["court_y"] is None
    assert review["review_required"] is True
    assert result["ai_review"]["escalated"] == 0


def test_low_confidence_terra_position_is_kept_for_review_without_sol(tmp_path, monkeypatch) -> None:
    primary = decision(.9, False, outcome="made")
    primary.court_zone = "three_wing_left"
    primary.court_x = 16
    primary.court_y = 29
    primary.position_confidence = .68
    responses = FakeResponses([primary])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )

    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=client,
    )

    review = result["candidates"][0]["ai_review"]
    assert [call["model"] for call in responses.calls] == ["gpt-6.1-terra"]
    assert review["court_zone"] == "three_wing_left"
    assert (review["court_x"], review["court_y"]) == (16, 29)
    assert review["review_required"] is True


def test_fixed_scene_reuses_one_camera_view_for_all_positions(tmp_path, monkeypatch) -> None:
    left = decision(.9, False, outcome="made")
    left.camera_view = "left_sideline"
    right = decision(.9, False, outcome="made")
    right.camera_view = "right_sideline"
    responses = FakeResponses([left, right])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )

    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [
            {"time_ms": 1000, "scene_id": 0},
            {"time_ms": 3000, "scene_id": 0},
        ]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=client,
    )

    assert result["ai_review"]["camera_conflicts"] == 0
    for candidate in result["candidates"]:
        review = candidate["ai_review"]
        assert review["court_zone"] == "three_center"
        assert review["camera_view"] == "left_sideline"


def test_confident_terra_result_does_not_escalate(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([decision(.9, False)])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    settings = Settings(_env_file=None, openai_api_key="test-key")
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        settings,
        client=client,
    )
    assert [call["model"] for call in responses.calls] == ["gpt-6.1-terra"]
    assert result["ai_review"]["escalated"] == 0


def test_local_shot_is_not_rejected_by_detail_review(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([
        decision(.95, False, is_shot=False, outcome="made"),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    settings = Settings(_env_file=None, openai_api_key="test-key")
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000, "foot_point_px": [10, 20]}]},
        settings,
        client=client,
    )
    review = result["candidates"][0]["ai_review"]
    assert [call["model"] for call in responses.calls] == ["gpt-6.1-terra"]
    assert review["is_shot"] is True
    assert review["local_ai_conflict"] is False


def test_invalid_openai_response_preserves_local_candidate(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([None])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test"}],
    )
    settings = Settings(_env_file=None, openai_api_key="test-key")
    candidate = {"time_ms": 1000}

    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [candidate]},
        settings,
        client=client,
    )

    assert result["candidates"] == [candidate]
    assert candidate["ai_review"]["status"] == "failed"
    assert result["ai_review"]["status"] == "partial"
