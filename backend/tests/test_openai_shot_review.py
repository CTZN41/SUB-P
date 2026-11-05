from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.court_calibration import calculate_transform
from app.services.openai_shot_review import (
    CameraContextDecision,
    OutcomeReviewDecision,
    PositionReviewDecision,
    ShotReviewDecision,
    _build_shared_camera_contexts,
    _camera_context_is_unclear,
    _calibrated_court_left_direction,
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


def test_confirmed_court_points_anchor_lateral_direction() -> None:
    transform = calculate_transform(
        [[750, 700], [1270, 700], [1550, 960], [490, 960]],
        [[19, 0], [31, 0], [31, 19], [19, 19]],
    )
    assert _calibrated_court_left_direction(transform) == "left"


@pytest.mark.parametrize('mirror', [False, True])
def test_confirmed_axes_replace_conflicting_camera_side_without_moving_shot(tmp_path, monkeypatch, mirror):
    from app.services.openai_shot_review import _calibrated_camera_axes, _camera_context_prompt
    points = [[451, 695], [923, 618], [1446, 635], [944, 756]]
    if mirror:
        points = [[1920-x, y] for x, y in points]
    transform = calculate_transform(points, [[17, 0], [33, 0], [33, 19], [17, 19]])
    axes = _calibrated_camera_axes(transform, [1920, 1080])
    assert axes['court_left_direction_on_screen'] == ('right' if mirror else 'left')
    assert axes['toward_half_court_direction_on_screen'] == ('left' if mirror else 'right')
    prompts = []
    for view in ('left_sideline', 'right_sideline'):
        guessed = camera_context().model_copy(update={'camera_view': view})
        responses = FakeResponses([guessed])
        monkeypatch.setattr('app.services.openai_shot_review.camera_context_frame_inputs', lambda *a: [])
        contexts, records, _ = _build_shared_camera_contexts(
            SimpleNamespace(responses=responses), tmp_path/'video.mp4',
            [{'time_ms':150933, 'scene_id':0, 'frame_size_px':[1920,1080]}],
            Settings(_env_file=None, openai_api_key='test-key'),
            _calibrated_court_left_direction(transform), transform)
        assert contexts[0].camera_view == 'unclear'
        assert records[0]['model_decisions']['primary']['camera_view'] == view
        assert records[0]['calibrated_axes'] == axes
        prompts.append(_camera_context_prompt(contexts[0]))
    assert prompts[0] == prompts[1]
    assert 'left free-throw (17,19)' in prompts[0]
    assert 'right free-throw (33,19)' in prompts[0]
    assert 'left_sideline' not in prompts[0] and 'right_sideline' not in prompts[0]


@pytest.mark.parametrize('transform,size', [
    ([[0,0,0]]*3,[1920,1080]),
    ([[1,0,0],[0,1,0],[0,0,1]],[0,1080]),
    ([[1,0,0],[0,1,0],[0,0,1]],[float('nan'),1080]),
    (None,[1920,1080]),
    ([[1,0,0],[0,1,0],[0,0,1]],None),
])
def test_invalid_calibration_cannot_supply_camera_axes(transform,size):
    from app.services.openai_shot_review import _calibrated_camera_axes
    assert _calibrated_camera_axes(transform,size) is None


def test_calibrated_axes_follow_rotated_video():
    from app.services.openai_shot_review import _calibrated_camera_axes
    points = [[1080-y,x] for x,y in [[451,695],[923,618],[1446,635],[944,756]]]
    transform = calculate_transform(points, [[17,0],[33,0],[33,19],[17,19]])
    axes = _calibrated_camera_axes(transform,[1080,1920])
    assert axes['court_left_direction_on_screen'] == 'up'
    assert axes['toward_half_court_direction_on_screen'] == 'down'


def test_calibrated_axes_do_not_raise_low_camera_confidence(tmp_path,monkeypatch):
    guessed = camera_context().model_copy(update={'confidence':.5})
    monkeypatch.setattr('app.services.openai_shot_review.camera_context_frame_inputs',lambda *a: [])
    contexts,records,_ = _build_shared_camera_contexts(
        SimpleNamespace(responses=FakeResponses([guessed])),tmp_path/'video.mp4',
        [{'time_ms':1000,'frame_size_px':[100,100]}],
        Settings(_env_file=None,openai_api_key='test-key'),'left',
        [[1,0,0],[0,1,0],[0,0,1]])
    assert contexts[0] is None
    assert records[0]['decision']['confidence'] == .5


def test_inconsistent_zone_coordinates_get_one_terra_position_retry(tmp_path, monkeypatch) -> None:
    first = decision(.9, False)
    first.court_zone = "mid_center"
    first.court_x = 24.5
    first.court_y = 17.0  # This is inside the paint, despite the named zone.
    corrected = PositionReviewDecision(
        court_zone="mid_center", court_x=24.5, court_y=21.5,
        confidence=.9, reason="Shoes are beyond the free-throw line",
        camera_view="left_sideline", overhead_lateral_relation="aligned_with_lane",
        distance_band="midrange", projection_evidence=["visible free-throw line"],
    )
    responses = FakeResponses([first, corrected])
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    result = review_candidates_with_openai(
        tmp_path / "video.mp4", {"candidates": [{"time_ms": 1000}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=SimpleNamespace(responses=responses),
    )
    review = result["candidates"][0]["ai_review"]
    assert (review["court_zone"], review["court_x"], review["court_y"]) == (
        "mid_center", 24.5, 21.5,
    )
    assert [attempt["task"] for attempt in review["attempts"]] == [
        "shot_review", "position_consistency",
    ]


def test_position_retry_can_correct_wrong_zone_name_for_same_coordinates(tmp_path, monkeypatch) -> None:
    first = decision(.9, False)
    first.court_zone = "mid_wing_right"
    first.court_x = 35
    first.court_y = 12  # The named wing disagrees with these baseline coordinates.
    corrected = position_decision(
        .70, zone="mid_baseline_right", court_x=35, court_y=12,
    )
    responses = FakeResponses([first, corrected])
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000, "foot_point_px": [35, 12]}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=SimpleNamespace(responses=responses),
        calibration_transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    )
    review = result["candidates"][0]["ai_review"]
    assert (review["court_zone"], review["court_x"], review["court_y"]) == (
        "mid_baseline_right", 35, 12,
    )
    assert review["review_required"] is False
    assert [attempt["task"] for attempt in review["attempts"]] == [
        "shot_review", "position_consistency",
    ]


@pytest.mark.parametrize("retry_confidence, expected_zone", [
    (.72, "mid_wing_right"),
    (.65, "mid_wing_right"),
    (.64, "needs_review"),
])
def test_calibrated_foot_conflict_gets_position_retry(
    tmp_path, monkeypatch, retry_confidence, expected_zone,
) -> None:
    first = decision(.9, False)
    first.court_zone = "mid_wing_left"
    first.court_x = 13
    first.court_y = 18
    corrected = position_decision(
        retry_confidence, zone="mid_wing_right", court_x=35, court_y=21,
    )
    responses = FakeResponses([first, corrected])
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 1000, "foot_point_px": [35, 21]}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=SimpleNamespace(responses=responses),
        calibration_transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    )
    review = result["candidates"][0]["ai_review"]
    assert review["calibrated_foot_projection"] == [35.0, 21.0]
    assert [attempt["task"] for attempt in review["attempts"]] == [
        "shot_review", "calibration_lateral_retry",
    ]
    assert "(35.0, 21.0)" in responses.calls[0]["input"][0]["content"][0]["text"]
    assert review["court_zone"] == expected_zone
    assert review["court_x"] == (35 if retry_confidence >= .65 else None)
    assert review["outcome"] == "made"
    assert review["shot_type"] == "set_shot"


def test_layup_uses_earlier_plant_and_rechecks_hoop_location(tmp_path, monkeypatch) -> None:
    first = decision(.9, False, shot_type="layup")
    first.court_zone, first.court_x, first.court_y = "restricted", 25, 5
    corrected = position_decision(.85, zone="paint_right", court_x=31, court_y=9)
    responses = FakeResponses([first, corrected])
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    candidate = {
        "time_ms": 4500, "foot_point_px": [26, 5],
        "foot_track": [
            {"time_ms": 4000, "foot_point_px": [31, 9], "ankle_points_px": [[31, 9]], "person_box_px": [20, -90, 42, 10]},
            {"time_ms": 4100, "foot_point_px": [31, 9], "ankle_points_px": [[31, 9]], "person_box_px": [20, -90, 42, 10]},
            {"time_ms": 4400, "foot_point_px": [27, 6]},
        ],
    }
    result = review_candidates_with_openai(
        tmp_path / "video.mp4", {"candidates": [candidate]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=SimpleNamespace(responses=responses),
        calibration_transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    )
    review = result["candidates"][0]["ai_review"]
    assert (review["court_zone"], review["court_x"], review["court_y"]) == ("paint_right", 31, 9)
    assert review["calibrated_plant_projection"] == [31.0, 9.0]
    assert review["plant_sample_time_ms"] == 4100
    assert [attempt["task"] for attempt in review["attempts"]] == ["shot_review", "layup_plant_retry"]
    assert "(31.0, 9.0)" in responses.calls[0]["input"][0]["content"][0]["text"]


def test_layup_unresolved_plant_conflict_does_not_save_wrong_zone(tmp_path, monkeypatch) -> None:
    first = decision(.9, False, shot_type="layup")
    first.court_zone, first.court_x, first.court_y = "restricted", 25, 5
    retry = position_decision(.85, zone="restricted", court_x=25, court_y=5)
    monkeypatch.setattr(
        "app.services.openai_shot_review.candidate_frame_inputs",
        lambda *args, **kwargs: [{"type": "input_image", "image_url": "data:image/jpeg;base64,test", "detail": "high"}],
    )
    result = review_candidates_with_openai(
        tmp_path / "video.mp4",
        {"candidates": [{"time_ms": 4500, "foot_point_px": [26, 5],
                         "foot_track": [
                             {"time_ms": 4000, "foot_point_px": [31, 9], "ankle_points_px": [[31, 9]], "person_box_px": [20, -90, 42, 10]},
                             {"time_ms": 4100, "foot_point_px": [31, 9], "ankle_points_px": [[31, 9]], "person_box_px": [20, -90, 42, 10]},
                         ]}]},
        Settings(_env_file=None, openai_api_key="test-key"),
        client=SimpleNamespace(responses=FakeResponses([first, retry])),
        calibration_transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    )
    review = result["candidates"][0]["ai_review"]
    assert review["court_zone"] == "needs_review"
    assert review["court_x"] is None
    assert review["review_required"] is True


@pytest.fixture(autouse=True)
def use_shared_camera_context(monkeypatch):
    shared = camera_context()
    monkeypatch.setattr(
        "app.services.openai_shot_review._build_shared_camera_contexts",
        lambda client, video_path, candidates, settings, calibration_left_direction=None, calibration_transform=None: (
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


@pytest.mark.parametrize('position_confidence, review_required', [(.64, True), (.65, False), (.68, False)])
def test_position_confidence_uses_relaxed_acceptance_without_sol(tmp_path, monkeypatch, position_confidence, review_required) -> None:
    primary = decision(.9, False, outcome="made")
    primary.court_zone = "three_wing_left"
    primary.court_x = 16
    primary.court_y = 29
    primary.position_confidence = position_confidence
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
    assert review["review_required"] is review_required


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
