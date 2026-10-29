from types import SimpleNamespace

from app.core.config import Settings
from app.services.openai_shot_review import ShotReviewDecision, review_candidates_with_openai


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
        visible_evidence=["release"],
        sequence_start_offset_ms=-2000,
        sequence_end_offset_ms=2000,
        review_required=review_required,
        reason="test",
    )


def test_missing_key_skips_review(tmp_path) -> None:
    settings = Settings(_env_file=None, openai_api_key=None)
    result = review_candidates_with_openai(tmp_path / "video.mp4", {"candidates": []}, settings)
    assert result["ai_review"]["status"] == "not_configured"


def test_ambiguous_terra_result_escalates_once_to_sol(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([decision(.5, True), decision(.9, False)])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    settings = Settings(_env_file=None, openai_api_key="test-key")
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000}]},
        settings,
        client=client,
    )
    assert [call["model"] for call in responses.calls] == ["gpt-6.1-terra", "gpt-6.1-sol"]
    assert all("astra" not in call["model"] for call in responses.calls)
    assert result["candidates"][0]["ai_review"]["model"] == "gpt-6.1-sol"
    assert result["candidates"][0]["ai_review"]["outcome"] == "unclear"
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
        lambda *args: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
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
        decision(.9, True, outcome="unclear", shot_type="dribble_pull_up"),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
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


def test_confident_terra_result_does_not_escalate(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([decision(.9, False)])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
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


def test_confident_ai_rejection_escalates_and_keeps_human_review(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([
        decision(.95, False, is_shot=False),
        decision(.95, False, is_shot=False),
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    settings = Settings(_env_file=None, openai_api_key="test-key")
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000, "foot_point_px": [10, 20]}]},
        settings,
        client=client,
    )
    review = result["candidates"][0]["ai_review"]
    assert [call["model"] for call in responses.calls] == ["gpt-6.1-terra", "gpt-6.1-sol"]
    assert review["is_shot"] is False
    assert review["local_ai_conflict"] is True
    assert review["review_required"] is True


def test_invalid_openai_response_preserves_local_candidate(tmp_path, monkeypatch) -> None:
    responses = FakeResponses([None])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test"}],
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
