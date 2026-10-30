"""Use OpenAI vision as a bounded, optional reviewer for local YOLO candidates."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

import cv2
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.services.shot_review_policy import POSITION_AUTO_CONFIRM_CONFIDENCE


FRAME_OFFSETS_MS = (
    -2500, -2000, -1500, -1000, -600, -300,
    0, 300, 700, 1200, 2000, 3500,
)
OUTCOME_EXTENSION_OFFSETS_MS = (4500, 5500)
FOCUS_OFFSETS_MS = (-1000, -300, 300, 700)
OUTCOME_OFFSETS_MS = (800, 1200, 1600, 2000, 2400, 2800, 3500, 4500)
ESCALATION_CONFIDENCE = 0.65
CAMERA_CONTEXT_CONFIDENCE = 0.75
POSITION_ESCALATION_CONFIDENCE = POSITION_AUTO_CONFIRM_CONFIDENCE

CourtZone = Literal[
    "restricted", "paint_left", "paint_center", "paint_right",
    "mid_baseline_left", "mid_wing_left", "mid_center",
    "mid_wing_right", "mid_baseline_right", "three_corner_left",
    "three_wing_left", "three_center", "three_wing_right",
    "three_corner_right", "needs_review",
]
CameraView = Literal[
    "left_sideline", "right_sideline", "baseline", "top_of_court", "unclear"
]
LateralRelation = Literal["left_of_lane", "aligned_with_lane", "right_of_lane", "unclear"]
DistanceBand = Literal["restricted", "paint", "midrange", "three_point", "unclear"]
ScreenDirection = Literal[
    "left", "right", "up", "down", "upper_left", "upper_right",
    "lower_left", "lower_right", "unclear",
]


class CameraContextDecision(BaseModel):
    camera_view: CameraView
    confidence: float = Field(ge=0, le=1)
    court_left_direction_on_screen: ScreenDirection
    toward_half_court_direction_on_screen: ScreenDirection
    orientation_summary: str = Field(max_length=500)
    visible_landmarks: list[str]
    reason: str = Field(max_length=300)


class ShotReviewDecision(BaseModel):
    is_shot: bool
    confidence: float = Field(ge=0, le=1)
    is_shot_confidence: float = Field(ge=0, le=1)
    outcome: Literal["made", "missed", "unclear"]
    outcome_confidence: float = Field(ge=0, le=1)
    shot_type: Literal[
        "catch_and_shoot",
        "set_shot",
        "dribble_pull_up",
        "layup",
        "dunk",
        "fadeaway",
        "step_back",
        "turnaround",
        "hook_shot",
        "other",
        "needs_review",
    ]
    shot_type_confidence: float = Field(ge=0, le=1)
    court_zone: CourtZone = "needs_review"
    court_x: float | None = Field(default=None, ge=0, le=50)
    court_y: float | None = Field(default=None, ge=0, le=47)
    position_confidence: float = Field(default=0, ge=0, le=1)
    position_reason: str = Field(default="", max_length=300)
    camera_view: CameraView = "unclear"
    overhead_lateral_relation: LateralRelation = "unclear"
    distance_band: DistanceBand = "unclear"
    projection_evidence: list[str] = Field(default_factory=list)
    visible_evidence: list[Literal[
        "release",
        "ball_rising",
        "rim_passage",
        "rim_deflection",
        "ball_falling_below_rim",
        "rebound_behavior",
        "scoreboard_change",
        "camera_cut",
        "rim_not_visible",
        "ball_path_not_visible",
    ]]
    sequence_start_offset_ms: int = Field(ge=-20000, le=0)
    sequence_end_offset_ms: int = Field(ge=0, le=20000)
    review_required: bool
    reason: str = Field(max_length=300)


class ShotDetailDecision(BaseModel):
    outcome: Literal["made", "missed", "unclear"]
    outcome_confidence: float = Field(ge=0, le=1)
    shot_type: Literal[
        "catch_and_shoot",
        "set_shot",
        "dribble_pull_up",
        "layup",
        "dunk",
        "fadeaway",
        "step_back",
        "turnaround",
        "hook_shot",
        "other",
        "needs_review",
    ]
    shot_type_confidence: float = Field(ge=0, le=1)
    court_zone: CourtZone
    court_x: float | None = Field(ge=0, le=50)
    court_y: float | None = Field(ge=0, le=47)
    position_confidence: float = Field(ge=0, le=1)
    position_reason: str = Field(max_length=300)
    camera_view: CameraView
    overhead_lateral_relation: LateralRelation
    distance_band: DistanceBand
    projection_evidence: list[str]
    visible_evidence: list[Literal[
        "release",
        "ball_rising",
        "rim_passage",
        "rim_deflection",
        "ball_falling_below_rim",
        "rebound_behavior",
        "scoreboard_change",
        "camera_cut",
        "rim_not_visible",
        "ball_path_not_visible",
    ]]
    sequence_start_offset_ms: int = Field(ge=-20000, le=0)
    sequence_end_offset_ms: int = Field(ge=0, le=20000)
    review_required: bool
    reason: str = Field(max_length=300)


class OutcomeReviewDecision(BaseModel):
    outcome: Literal["made", "missed", "unclear"]
    confidence: float = Field(ge=0, le=1)
    visible_evidence: list[Literal[
        "rim_passage",
        "rim_deflection",
        "ball_falling_below_rim",
        "rebound_behavior",
        "scoreboard_change",
        "camera_cut",
        "rim_not_visible",
        "ball_path_not_visible",
    ]]
    review_required: bool
    reason: str = Field(max_length=300)


class PositionReviewDecision(BaseModel):
    court_zone: CourtZone
    court_x: float | None = Field(ge=0, le=50)
    court_y: float | None = Field(ge=0, le=47)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=300)
    camera_view: CameraView
    overhead_lateral_relation: LateralRelation
    distance_band: DistanceBand
    projection_evidence: list[str]


class OpenAIShotReviewError(RuntimeError):
    pass


def _encode_frame(frame) -> str:
    height, width = frame.shape[:2]
    if width > 1280:
        scale = 1280 / width
        frame = cv2.resize(frame, (1280, round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not ok:
        raise OpenAIShotReviewError("후보 장면을 이미지로 변환하지 못했습니다.")
    return "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")


def _focus_crop(frame, focus_point: list[float] | None):
    if not focus_point or len(focus_point) != 2:
        return None
    height, width = frame.shape[:2]
    focus_x = min(width - 1, max(0, int(focus_point[0])))
    focus_y = min(height - 1, max(0, int(focus_point[1])))
    half_width = max(int(width * .22), int(height * .22))
    x1, x2 = max(0, focus_x - half_width), min(width, focus_x + half_width)
    y1, y2 = max(0, focus_y - int(height * .58)), min(height, focus_y + int(height * .04))
    if x2 - x1 < 64 or y2 - y1 < 64:
        return None
    return frame[y1:y2, x1:x2]


def _hoop_crop(frame, hoop_point: list[float] | None):
    if not hoop_point or len(hoop_point) != 2:
        return None
    height, width = frame.shape[:2]
    hoop_x = min(width - 1, max(0, int(hoop_point[0])))
    hoop_y = min(height - 1, max(0, int(hoop_point[1])))
    half_width = max(120, int(width * .13))
    above, below = max(100, int(height * .14)), max(160, int(height * .28))
    x1, x2 = max(0, hoop_x - half_width), min(width, hoop_x + half_width)
    y1, y2 = max(0, hoop_y - above), min(height, hoop_y + below)
    if x2 - x1 < 64 or y2 - y1 < 64:
        return None
    return frame[y1:y2, x1:x2]


def candidate_frame_inputs(
    video_path: Path,
    time_ms: int,
    focus_point: list[float] | None = None,
    *,
    include_outcome_extension: bool = False,
) -> list[dict]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise OpenAIShotReviewError("분석할 원본 영상을 열지 못했습니다.")
    images: list[dict] = []
    focus_images: list[dict] = []
    try:
        offsets = FRAME_OFFSETS_MS + (
            OUTCOME_EXTENSION_OFFSETS_MS if include_outcome_extension else ()
        )
        for offset in offsets:
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0, time_ms + offset))
            ok, frame = capture.read()
            if ok:
                images.extend([
                    {"type": "input_text", "text": f"full frame t={offset:+d}ms"},
                    {"type": "input_image", "image_url": _encode_frame(frame), "detail": "high"},
                ])
                if offset in FOCUS_OFFSETS_MS:
                    crop = _focus_crop(frame, focus_point)
                    if crop is not None:
                        focus_images.extend([
                            {"type": "input_text", "text": f"player crop t={offset:+d}ms"},
                            {
                                "type": "input_image",
                                "image_url": _encode_frame(crop),
                                "detail": "high",
                            },
                        ])
    finally:
        capture.release()
    if sum(item["type"] == "input_image" for item in images) < 3:
        raise OpenAIShotReviewError("슛 전후 장면을 충분히 추출하지 못했습니다.")
    return [*images, *focus_images]


def camera_context_frame_inputs(video_path: Path, candidates: list[dict]) -> list[dict]:
    """Extract a small representative bundle used once for a fixed camera scene."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise OpenAIShotReviewError("카메라 방향을 확인할 원본 영상을 열지 못했습니다.")
    times = sorted({max(0, int(candidate.get("time_ms", 0)) - 800) for candidate in candidates})
    if not times:
        capture.release()
        raise OpenAIShotReviewError("카메라 방향을 확인할 후보 시간이 없습니다.")
    if len(times) > 4:
        last = len(times) - 1
        indexes = sorted({0, round(last / 3), round(last * 2 / 3), last})
        times = [times[index] for index in indexes]
    elif len(times) == 1:
        base = times[0]
        times = sorted({max(0, base - 1200), base, base + 1800})
    images: list[dict] = []
    try:
        for time_ms in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, time_ms)
            ok, frame = capture.read()
            if not ok:
                continue
            images.extend([
                {"type": "input_text", "text": f"fixed-scene full frame at {time_ms}ms"},
                {"type": "input_image", "image_url": _encode_frame(frame), "detail": "high"},
            ])
    finally:
        capture.release()
    if sum(item["type"] == "input_image" for item in images) < 2:
        raise OpenAIShotReviewError("카메라 방향을 확인할 대표 장면을 충분히 추출하지 못했습니다.")
    return images


def candidate_outcome_frame_inputs(
    video_path: Path,
    time_ms: int,
    hoop_point: list[float] | None,
    evidence_end_ms: int | None = None,
) -> list[dict]:
    """Build a chronological full-scene + rim-crop bundle only for scoring."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise OpenAIShotReviewError("득점 여부를 확인할 원본 영상을 열지 못했습니다.")
    full_images: list[dict] = []
    hoop_images: list[dict] = []
    impact_offset = None
    if evidence_end_ms is not None:
        impact_offset = min(4000, max(800, evidence_end_ms - time_ms))
    dense_offsets = (
        tuple(impact_offset + delta for delta in (-600, -450, -300, -150, 0, 150, 300))
        if impact_offset is not None
        else ()
    )
    all_offsets = sorted(set(OUTCOME_OFFSETS_MS + dense_offsets))
    try:
        for offset in all_offsets:
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0, time_ms + offset))
            ok, frame = capture.read()
            if not ok:
                continue
            if offset in OUTCOME_OFFSETS_MS:
                full_images.extend([
                    {"type": "input_text", "text": f"full frame t={offset:+d}ms"},
                    {"type": "input_image", "image_url": _encode_frame(frame), "detail": "high"},
                ])
            crop = _hoop_crop(frame, hoop_point)
            if crop is not None:
                hoop_images.extend([
                    {"type": "input_text", "text": f"rim crop t={offset:+d}ms"},
                    {"type": "input_image", "image_url": _encode_frame(crop), "detail": "high"},
                ])
    finally:
        capture.release()
    if sum(item["type"] == "input_image" for item in full_images) < 3:
        raise OpenAIShotReviewError("득점 전후 장면을 충분히 추출하지 못했습니다.")
    return [*full_images, *hoop_images]


def _usage_dict(response) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _review_camera_context_once(
    client,
    model: str,
    images: list[dict],
) -> tuple[CameraContextDecision, dict[str, int]]:
    response = client.responses.parse(
        model=model,
        reasoning={"effort": "low"},
        store=False,
        input=[{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": (
                    "These are representative full frames from one fixed basketball camera scene. "
                    "Do not judge any shot. Build one stable camera-to-overhead-court orientation that "
                    "will be reused for every shot in this scene. Anchor the interpretation on the hoop, "
                    "baseline, paint rectangle, lane edges, free-throw circle and three-point arc. Standard "
                    "overhead coordinates place the hoop at (25, 5.25), court left at x=0, court right at "
                    "x=50, the baseline at y=0 and half court toward y=47. Determine which direction on "
                    "the screen leads toward overhead court-left and which direction leads toward half court. "
                    "Perspective may make these diagonal. Do not use a player's current position to name the "
                    "camera side, and do not assume image-left means court-left. Return unclear whenever the "
                    "visible court markings cannot support a stable mapping. The same answer must apply to all "
                    "supplied frames because the camera is fixed. Summarize concrete landmark evidence and the "
                    "screen-to-overhead projection rule in orientation_summary."
                ),
            }, *images],
        }],
        text_format=CameraContextDecision,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise OpenAIShotReviewError("AI가 카메라 방향 판정 형식에 맞는 결과를 반환하지 않았습니다.")
    return parsed, _usage_dict(response)


def _camera_context_is_unclear(context: CameraContextDecision) -> bool:
    return (
        context.camera_view == "unclear"
        or context.confidence < CAMERA_CONTEXT_CONFIDENCE
        or context.court_left_direction_on_screen == "unclear"
        or context.toward_half_court_direction_on_screen == "unclear"
    )


def _camera_context_prompt(context: CameraContextDecision | None) -> str:
    if context is None:
        return (
            "No reliable shared camera orientation is available. Do not guess a court zone; return "
            "needs_review with null coordinates."
        )
    return (
        "A fixed-scene camera analysis was already completed and must not be reinterpreted per shot. "
        f"camera_view={context.camera_view}; "
        f"court-left points toward screen {context.court_left_direction_on_screen}; "
        f"half-court points toward screen {context.toward_half_court_direction_on_screen}; "
        f"shared orientation: {context.orientation_summary}. "
        "Use this exact shared orientation to project the release shoes. Do not replace or reverse it."
    )


def _review_once(
    client,
    model: str,
    images: list[dict],
    *,
    effort: str = "low",
    camera_context: CameraContextDecision | None = None,
) -> tuple[ShotReviewDecision, dict[str, int]]:
    response = client.responses.parse(
        model=model,
        reasoning={"effort": effort},
        store=False,
        input=[{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": (
                    "The images are chronological frames from one basketball shot candidate. "
                    "Each image is preceded by its timestamp relative to the local release candidate. "
                    "The base window covers -2500 through +3500 milliseconds with denser samples "
                    "around release. Optional +4500 and +5500 full-scene frames are supplied only "
                    "when the base pass could not see the outcome. Player crops cover the release. "
                    "The local YOLO and pose pipeline already confirmed that this is a shot attempt. "
                    "Do not re-judge or reject the shot. Judge shot type, outcome, and the shooter's court "
                    "location independently, with a confidence for each. "
                    "For shot type, a running one-handed close finish that releases the ball upward beside "
                    "the rim is a layup, even when the shooter's hand reaches rim height. Use shot_type=dunk "
                    "only if the player keeps hold of the ball at rim height and forces it directly down "
                    "through the hoop. "
                    "Do not infer a dunk from proximity to the rim alone. "
                    + _camera_context_prompt(camera_context) + " "
                    "Location must now follow two steps: (1) project the shooter's actual release shoes using "
                    "the shared camera orientation into an overhead relation to the lane; (2) choose the "
                    "14-zone result from that projected relation and distance. Do not infer "
                    "overhead left/right from image left/right. Use the visible hoop, paint, free-throw lane "
                    "and three-point arc. Ignore stored calibration and do not treat the bottom-center of a "
                    "person box as an exact shoe location. Choose one of the supplied 14 court zones and estimate "
                    "standard half-court coordinates in feet: x=0..50 from left to right and y=0..47 from "
                    "the baseline toward half court, with the hoop at (25, 5.25). Left/right are from this "
                    "overhead court perspective. For three-point shots, x=17..33 is the center/top sector; "
                    "x<17 is left wing and x>33 is right wing. Return court_zone="
                    "needs_review and null coordinates when the feet or court lines cannot support a reliable "
                    "location. For outcome, first use visible rim passage or rim deflection. If those are not "
                    "clear, consider the ball falling below the rim, the player's rebound behavior, and a "
                    "visible scoreboard or shot-counter change as secondary evidence. Never invent evidence "
                    "that is off-screen. If a shot attempt and type are visible but the outcome is not, keep "
                    "the shot and type and return outcome=unclear. Set sequence offsets to "
                    "cover preparation through the visible result, normally 3 to 15 seconds total and "
                    "never beyond the schema limits. Use needs_review for shot type only when the movement "
                    "itself is not classifiable. List only evidence actually visible in the supplied frames."
                ),
            }, *images],
        }],
        text_format=ShotDetailDecision,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise OpenAIShotReviewError("AI가 판정 형식에 맞는 결과를 반환하지 않았습니다.")
    if isinstance(parsed, ShotReviewDecision):
        # Test doubles and older cached responses may still use the legacy shape.
        parsed.is_shot = True
        parsed.is_shot_confidence = 1.0
        return parsed, _usage_dict(response)
    confidence = min(
        parsed.outcome_confidence,
        parsed.shot_type_confidence,
        parsed.position_confidence,
    )
    return ShotReviewDecision(
        is_shot=True,
        confidence=confidence,
        is_shot_confidence=1.0,
        **parsed.model_dump(),
    ), _usage_dict(response)


def _review_outcome_once(
    client,
    model: str,
    images: list[dict],
    *,
    effort: str = "low",
) -> tuple[OutcomeReviewDecision, dict[str, int]]:
    response = client.responses.parse(
        model=model,
        reasoning={"effort": effort},
        store=False,
        input=[{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": (
                    "These chronological images contain the result of an already detected basketball "
                    "shot. Each full frame is followed later by a matching rim crop label. Decide only "
                    "whether the shot was made or missed. Prefer visible ball passage through the rim/net "
                    "or a clear rim deflection. A ball overlapping or touching the rim is not enough to call "
                    "a make: if it contacts the rim/backboard and then exits sideways without passing down "
                    "through the net, classify it as missed. Track the ball direction across adjacent rim "
                    "crops. Rebound behavior and a visible counter change are secondary "
                    "evidence. Return unclear when neither result is visible; do not re-judge whether a shot "
                    "was attempted and never invent off-screen evidence."
                ),
            }, *images],
        }],
        text_format=OutcomeReviewDecision,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise OpenAIShotReviewError("AI가 득점 판정 형식에 맞는 결과를 반환하지 않았습니다.")
    return parsed, _usage_dict(response)


def _review_position_once(
    client,
    model: str,
    images: list[dict],
    camera_context: CameraContextDecision | None = None,
) -> tuple[PositionReviewDecision, dict[str, int]]:
    response = client.responses.parse(
        model=model,
        reasoning={"effort": "low"},
        store=False,
        input=[{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": (
                    "The local YOLO and pose pipeline already confirmed this basketball shot. Decide only "
                    "the shooter's location at release. "
                    + _camera_context_prompt(camera_context) + " "
                    "Project the actual release shoes into an overhead left/aligned/right relation to the lane; "
                    "never equate image-left/image-right with court-left/court-right. Third, classify distance "
                    "and choose one of the 14 zones. Use the visible hoop, paint, lane and three-point arc. "
                    "Ignore stored calibration and do not use the bottom-center of a person box as an exact "
                    "foot point. Choose one of the supplied 14 zones and estimate "
                    "standard half-court coordinates in feet: x=0..50 left-to-right and y=0..47 from the "
                    "baseline toward half court, with the hoop at (25, 5.25). Left/right are from the "
                    "overhead court perspective. For three-point shots, x=17..33 is the center/top sector; "
                    "x<17 is left wing and x>33 is right wing. Return needs_review with null coordinates when the shoes "
                    "or court markings do not support a reliable location."
                ),
            }, *images],
        }],
        text_format=PositionReviewDecision,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise OpenAIShotReviewError("AI가 코트 위치 판정 형식에 맞는 결과를 반환하지 않았습니다.")
    return parsed, _usage_dict(response)


def _needs_fallback(decision: ShotReviewDecision) -> bool:
    return (
        decision.outcome == "unclear"
        or decision.shot_type == "needs_review"
        or min(
            decision.outcome_confidence,
            decision.shot_type_confidence,
        ) < ESCALATION_CONFIDENCE
    )


def _outcome_needs_dedicated_review(decision: ShotReviewDecision) -> bool:
    """Keep scoring review independent from shot-type confidence."""
    return decision.is_shot and decision.outcome == "unclear"


def _apply_outcome_review(
    decision: ShotReviewDecision,
    outcome: OutcomeReviewDecision,
) -> None:
    decision.outcome = outcome.outcome
    decision.outcome_confidence = outcome.confidence
    decision.visible_evidence = sorted(set(decision.visible_evidence + outcome.visible_evidence))
    decision.review_required = (
        outcome.outcome == "unclear"
        or outcome.review_required
        or decision.shot_type == "needs_review"
        or decision.shot_type_confidence < ESCALATION_CONFIDENCE
        or decision.court_zone == "needs_review"
        or decision.position_confidence < POSITION_ESCALATION_CONFIDENCE
    )
    decision.confidence = min(
        decision.outcome_confidence,
        decision.shot_type_confidence,
        decision.position_confidence,
    )
    decision.reason = f"{decision.reason}; 득점 전용 검토: {outcome.reason}"[:300]


def _position_needs_fallback(decision: ShotReviewDecision) -> bool:
    return (
        decision.court_zone == "needs_review"
        or decision.court_x is None
        or decision.court_y is None
        or decision.position_confidence < POSITION_ESCALATION_CONFIDENCE
    )


def _apply_position_review(
    decision: ShotReviewDecision,
    position: PositionReviewDecision,
) -> None:
    terra_zone = decision.court_zone
    models_disagree = (
        terra_zone != "needs_review"
        and position.court_zone != "needs_review"
        and terra_zone != position.court_zone
    )
    if models_disagree:
        decision.court_zone = "needs_review"
        decision.court_x = None
        decision.court_y = None
        decision.position_confidence = min(decision.position_confidence, position.confidence)
        decision.position_reason = (
            f"Terra 위치({terra_zone})와 Sol 위치({position.court_zone})가 서로 달라 확인 필요"
        )
        decision.review_required = True
        decision.confidence = min(
            decision.outcome_confidence,
            decision.shot_type_confidence,
            decision.position_confidence,
        )
        return
    decision.court_zone = position.court_zone
    decision.court_x = position.court_x
    decision.court_y = position.court_y
    decision.position_confidence = position.confidence
    decision.position_reason = position.reason
    decision.camera_view = position.camera_view
    decision.overhead_lateral_relation = position.overhead_lateral_relation
    decision.distance_band = position.distance_band
    decision.projection_evidence = position.projection_evidence
    position_unclear = (
        position.court_zone == "needs_review"
        or position.court_x is None
        or position.court_y is None
        or position.confidence < POSITION_ESCALATION_CONFIDENCE
    )
    decision.review_required = (
        position_unclear
        or decision.outcome == "unclear"
        or decision.outcome_confidence < ESCALATION_CONFIDENCE
        or decision.shot_type == "needs_review"
        or decision.shot_type_confidence < ESCALATION_CONFIDENCE
    )
    decision.confidence = min(
        decision.outcome_confidence,
        decision.shot_type_confidence,
        decision.position_confidence,
    )


def _consensus(primary: ShotReviewDecision, fallback: ShotReviewDecision) -> dict:
    shot_agrees = primary.is_shot == fallback.is_shot
    type_agrees = (
        primary.shot_type == fallback.shot_type
        and primary.shot_type != "needs_review"
    )
    outcome_agrees = (
        primary.outcome == fallback.outcome
        and primary.outcome in {"made", "missed"}
    )
    is_shot = primary.is_shot if shot_agrees else (primary.is_shot or fallback.is_shot)
    shot_type = primary.shot_type if type_agrees else "needs_review"
    outcome = primary.outcome if outcome_agrees else "unclear"
    position_clear = (
        primary.court_zone != "needs_review"
        and primary.court_x is not None
        and primary.court_y is not None
        and primary.position_confidence >= POSITION_ESCALATION_CONFIDENCE
    )
    unresolved = (
        not shot_agrees
        or (is_shot and (not type_agrees or not outcome_agrees or not position_clear))
    )
    reasons = []
    if not shot_agrees:
        reasons.append("Terra와 Sol의 슛 여부 판단이 다름")
    if is_shot and not type_agrees:
        reasons.append("Terra와 Sol의 슛 종류 판단이 다름")
    if is_shot and not outcome_agrees:
        reasons.append("성공·실패를 두 모델이 같은 근거로 확정하지 못함")
    if is_shot and not position_clear:
        reasons.append("슛 위치를 AI가 확정하지 못함")
    return {
        "is_shot": is_shot,
        "confidence": min(primary.confidence, fallback.confidence),
        "is_shot_confidence": min(primary.is_shot_confidence, fallback.is_shot_confidence),
        "outcome": outcome,
        "outcome_confidence": min(primary.outcome_confidence, fallback.outcome_confidence),
        "shot_type": shot_type,
        "shot_type_confidence": min(primary.shot_type_confidence, fallback.shot_type_confidence),
        "court_zone": primary.court_zone,
        "court_x": primary.court_x,
        "court_y": primary.court_y,
        "position_confidence": primary.position_confidence,
        "position_reason": primary.position_reason,
        "camera_view": primary.camera_view,
        "overhead_lateral_relation": primary.overhead_lateral_relation,
        "distance_band": primary.distance_band,
        "projection_evidence": primary.projection_evidence,
        "visible_evidence": sorted(set(primary.visible_evidence + fallback.visible_evidence)),
        "sequence_start_offset_ms": min(primary.sequence_start_offset_ms, fallback.sequence_start_offset_ms),
        "sequence_end_offset_ms": max(primary.sequence_end_offset_ms, fallback.sequence_end_offset_ms),
        "review_required": unresolved,
        "reason": "; ".join(reasons) or "Terra와 Sol의 판단이 일치함",
        "consensus": {
            "shot_agrees": shot_agrees,
            "shot_type_agrees": type_agrees,
            "outcome_agrees": outcome_agrees,
        },
    }


def _build_shared_camera_contexts(
    client,
    video_path: Path,
    candidates: list[dict],
    settings: Settings,
) -> tuple[dict[int, CameraContextDecision | None], list[dict], int]:
    """Resolve one reusable orientation for each fixed scene before shot review."""
    groups: dict[int, list[dict]] = {}
    for candidate in candidates:
        groups.setdefault(int(candidate.get("scene_id", 0)), []).append(candidate)
    contexts: dict[int, CameraContextDecision | None] = {}
    records: list[dict] = []
    escalated = 0
    for scene_id, scene_candidates in groups.items():
        attempts: list[dict] = []
        try:
            images = camera_context_frame_inputs(video_path, scene_candidates)
            primary, usage = _review_camera_context_once(
                client, settings.openai_vision_model, images,
            )
            attempts.append({
                "model": settings.openai_vision_model,
                "task": "camera_context",
                "effort": "low",
                "usage": usage,
            })
            chosen = primary
            decisions = {"primary": primary.model_dump()}
            final_model = settings.openai_vision_model
            resolved = not _camera_context_is_unclear(chosen)
            contexts[scene_id] = chosen if resolved else None
            records.append({
                "scene_id": scene_id,
                "status": "resolved" if resolved else "needs_review",
                "model": final_model,
                "decision": chosen.model_dump(),
                "attempts": attempts,
                "model_decisions": decisions,
            })
        except Exception:
            contexts[scene_id] = None
            records.append({
                "scene_id": scene_id,
                "status": "failed",
                "message": "장면 공통 카메라 방향을 확정하지 못했습니다.",
                "attempts": attempts,
            })
    return contexts, records, escalated


def _apply_shared_camera_context(
    decision: ShotReviewDecision,
    context: CameraContextDecision | None,
) -> None:
    """Prevent a per-shot response from silently changing a fixed camera orientation."""
    if context is None:
        decision.court_zone = "needs_review"
        decision.court_x = None
        decision.court_y = None
        decision.position_confidence = 0
        decision.position_reason = "장면 공통 카메라 방향을 확정하지 못해 위치 확인 필요"
        decision.camera_view = "unclear"
        decision.review_required = True
        decision.confidence = 0
        return
    decision.camera_view = context.camera_view


def _invalidate_inconsistent_camera_views(candidates: list[dict]) -> int:
    """A fixed scene cannot legitimately switch camera sides between shots."""
    groups: dict[int, list[dict]] = {}
    for candidate in candidates:
        groups.setdefault(int(candidate.get("scene_id", 0)), []).append(candidate)
    invalidated = 0
    for scene_candidates in groups.values():
        views = {
            candidate.get("ai_review", {}).get("camera_view")
            for candidate in scene_candidates
            if candidate.get("ai_review", {}).get("camera_view") not in {None, "unclear"}
        }
        if len(views) <= 1:
            continue
        view_text = ", ".join(sorted(views))
        for candidate in scene_candidates:
            review = candidate.get("ai_review") or {}
            if review.get("status") == "failed":
                continue
            review["court_zone"] = "needs_review"
            review["court_x"] = None
            review["court_y"] = None
            review["review_required"] = True
            review["position_reason"] = (
                f"같은 고정 장면의 카메라 방향 판정이 서로 다름({view_text})"
            )
            invalidated += 1
    return invalidated


def review_candidates_with_openai(
    video_path: Path,
    result: dict,
    settings: Settings,
    *,
    client=None,
) -> dict:
    """Attach AI review metadata without making local analysis depend on the API."""
    if not settings.openai_shot_review_enabled:
        result["ai_review"] = {"status": "disabled", "reviewed": 0, "escalated": 0}
        return result
    if not settings.openai_api_key:
        result["ai_review"] = {"status": "not_configured", "reviewed": 0, "escalated": 0}
        return result
    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)

    candidates = result.get("candidates", [])[: settings.openai_max_candidate_reviews]
    reviewed = 0
    camera_contexts, camera_context_records, escalated = _build_shared_camera_contexts(
        client, video_path, candidates, settings,
    )
    failed = 0
    for candidate in candidates:
        gate = candidate.get("shot_gate") or {}
        if gate.get("verdict") == "not_shot" and gate.get("confidence", 0) >= .9:
            candidate["ai_review"] = {
                "is_shot": False,
                "review_required": True,
                "local_ai_conflict": True,
                "reason": "독립적인 슛 여부 판정에서 슛을 확인하지 못함. 기록은 확인 필요로 보존",
                "shot_gate": gate,
            }
            reviewed += 1
            continue
        attempts: list[dict] = []
        try:
            camera_context = camera_contexts.get(int(candidate.get("scene_id", 0)))
            images = candidate_frame_inputs(
                video_path,
                int(candidate["time_ms"]),
                candidate.get("foot_point_px"),
            )
            primary, usage = _review_once(
                client,
                settings.openai_vision_model,
                images,
                camera_context=camera_context,
            )
            _apply_shared_camera_context(primary, camera_context)
            attempts.append({"model": settings.openai_vision_model, "task": "shot_review", "effort": "low", "usage": usage})
            final_model = settings.openai_vision_model
            model_decisions = {"primary": primary.model_dump()}
            extended_to_ms = 3500
            outcome_path_used = False
            if _outcome_needs_dedicated_review(primary):
                outcome_path_used = True
                outcome_images = candidate_outcome_frame_inputs(
                    video_path,
                    int(candidate["time_ms"]),
                    candidate.get("tracked_hoop_px"),
                    candidate.get("evidence_end_ms"),
                )
                outcome_review, usage = _review_outcome_once(
                    client,
                    settings.openai_vision_model,
                    outcome_images,
                )
                attempts.append({
                    "model": settings.openai_vision_model,
                    "task": "outcome_only",
                    "effort": "low",
                    "usage": usage,
                })
                model_decisions["outcome_primary"] = outcome_review.model_dump()
                extended_to_ms = 4500
                if (
                    outcome_review.outcome in {"made", "missed"}
                    and outcome_review.confidence >= ESCALATION_CONFIDENCE
                ):
                    _apply_outcome_review(primary, outcome_review)
                elif settings.openai_vision_fallback_model:
                    outcome_fallback, usage = _review_outcome_once(
                        client,
                        settings.openai_vision_fallback_model,
                        outcome_images,
                    )
                    attempts.append({
                        "model": settings.openai_vision_fallback_model,
                        "task": "outcome_only",
                        "effort": "low",
                        "usage": usage,
                    })
                    model_decisions["outcome_fallback"] = outcome_fallback.model_dump()
                    final_model = settings.openai_vision_fallback_model
                    escalated += 1
                    _apply_outcome_review(primary, outcome_fallback)
                else:
                    _apply_outcome_review(primary, outcome_review)
            if _position_needs_fallback(primary):
                primary.review_required = True
            final_review = primary.model_dump()
            if (
                settings.openai_vision_fallback_model
                and _needs_fallback(primary)
                and not outcome_path_used
            ):
                fallback_effort = "medium" if not primary.is_shot else "low"
                fallback, usage = _review_once(
                    client,
                    settings.openai_vision_fallback_model,
                    images,
                    effort=fallback_effort,
                    camera_context=camera_context,
                )
                _apply_shared_camera_context(fallback, camera_context)
                attempts.append({
                    "model": settings.openai_vision_fallback_model,
                    "task": "shot_review",
                    "effort": fallback_effort,
                    "usage": usage,
                })
                final_model = settings.openai_vision_fallback_model
                escalated += 1
                model_decisions["fallback"] = fallback.model_dump()
                final_review = _consensus(primary, fallback)
            local_ai_conflict = not final_review["is_shot"]
            candidate["ai_review"] = {
                **final_review,
                "review_required": final_review["review_required"] or local_ai_conflict,
                "local_ai_conflict": local_ai_conflict,
                "model": final_model,
                "attempts": attempts,
                "model_decisions": model_decisions,
                "input_window_ms": [-2500, extended_to_ms],
            }
            reviewed += 1
        except Exception:
            candidate["ai_review"] = {
                "status": "failed",
                "message": "AI 보조 판정을 완료하지 못했습니다. 로컬 후보는 그대로 유지됩니다.",
                "attempts": attempts,
            }
            failed += 1

    camera_conflicts = _invalidate_inconsistent_camera_views(candidates)

    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    for candidate in candidates:
        for attempt in (candidate.get("ai_review") or {}).get("attempts", []):
            for key in usage_totals:
                usage_totals[key] += int((attempt.get("usage") or {}).get(key, 0) or 0)
    for context in camera_context_records:
        for attempt in context.get("attempts", []):
            for key in usage_totals:
                usage_totals[key] += int((attempt.get("usage") or {}).get(key, 0) or 0)

    result["ai_review"] = {
        "status": "complete" if failed == 0 else "partial",
        "reviewed": reviewed,
        "failed": failed,
        "escalated": escalated,
        "primary_model": settings.openai_vision_model,
        "fallback_model": settings.openai_vision_fallback_model,
        "candidate_limit": settings.openai_max_candidate_reviews,
        "camera_conflicts": camera_conflicts,
        "camera_contexts": camera_context_records,
        "usage": usage_totals,
    }
    return result
