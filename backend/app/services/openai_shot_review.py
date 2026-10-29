"""Use OpenAI vision as a bounded, optional reviewer for local YOLO candidates."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

import cv2
from pydantic import BaseModel, Field

from app.core.config import Settings


FRAME_OFFSETS_MS = (-2400, -1500, -900, -300, 0, 300, 700, 1200, 1800, 2500)
FOCUS_OFFSETS_MS = (-900, -300, 0, 300)
ESCALATION_CONFIDENCE = 0.65


class ShotReviewDecision(BaseModel):
    is_shot: bool
    confidence: float = Field(ge=0, le=1)
    outcome: Literal["made", "missed", "unclear"]
    shot_type: Literal[
        "catch_and_shoot",
        "set_shot",
        "dribble_pull_up",
        "layup",
        "fadeaway",
        "step_back",
        "turnaround",
        "hook_shot",
        "other",
        "needs_review",
    ]
    sequence_start_offset_ms: int = Field(ge=-20000, le=0)
    sequence_end_offset_ms: int = Field(ge=0, le=20000)
    review_required: bool
    reason: str = Field(max_length=300)


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


def candidate_frame_inputs(
    video_path: Path,
    time_ms: int,
    focus_point: list[float] | None = None,
) -> list[dict]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise OpenAIShotReviewError("분석할 원본 영상을 열지 못했습니다.")
    images: list[dict] = []
    focus_images: list[dict] = []
    try:
        for offset in FRAME_OFFSETS_MS:
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0, time_ms + offset))
            ok, frame = capture.read()
            if ok:
                images.append({"type": "input_image", "image_url": _encode_frame(frame), "detail": "high"})
                if offset in FOCUS_OFFSETS_MS:
                    crop = _focus_crop(frame, focus_point)
                    if crop is not None:
                        focus_images.append({
                            "type": "input_image",
                            "image_url": _encode_frame(crop),
                            "detail": "high",
                        })
    finally:
        capture.release()
    if len(images) < 3:
        raise OpenAIShotReviewError("슛 전후 장면을 충분히 추출하지 못했습니다.")
    return [*images, *focus_images]


def _usage_dict(response) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _review_once(
    client,
    model: str,
    images: list[dict],
    *,
    effort: str = "low",
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
                    "The first ten images show the full scene at offsets -2400, -1500, -900, -300, "
                    "0, +300, +700, +1200, +1800, and +2500 milliseconds from the candidate. "
                    "If more images follow, they are chronological "
                    "close-ups of the detected player at -900, -300, 0, and +300 milliseconds. "
                    "Decide whether a player actually attempts a shot. If visible, classify make or miss "
                    "and choose exactly one shot type from the response schema. Set sequence offsets to "
                    "cover preparation through the visible result, normally 3 to 15 seconds total and "
                    "never beyond the schema limits. Judge only visible evidence; use unclear for outcome, "
                    "needs_review for shot type, and review_required when the release, rim, ball path, or "
                    "shot type is insufficient. Do not infer from pose alone."
                ),
            }, *images],
        }],
        text_format=ShotReviewDecision,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise OpenAIShotReviewError("AI가 판정 형식에 맞는 결과를 반환하지 않았습니다.")
    return parsed, _usage_dict(response)


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
    escalated = 0
    failed = 0
    for candidate in candidates:
        attempts: list[dict] = []
        try:
            images = candidate_frame_inputs(
                video_path,
                int(candidate["time_ms"]),
                candidate.get("foot_point_px"),
            )
            decision, usage = _review_once(client, settings.openai_vision_model, images)
            attempts.append({"model": settings.openai_vision_model, "effort": "low", "usage": usage})
            final_model = settings.openai_vision_model
            if (
                settings.openai_vision_fallback_model
                and (
                    not decision.is_shot
                    or decision.review_required
                    or decision.confidence < ESCALATION_CONFIDENCE
                )
            ):
                fallback_effort = "medium" if not decision.is_shot else "low"
                decision, usage = _review_once(
                    client,
                    settings.openai_vision_fallback_model,
                    images,
                    effort=fallback_effort,
                )
                attempts.append({
                    "model": settings.openai_vision_fallback_model,
                    "effort": fallback_effort,
                    "usage": usage,
                })
                final_model = settings.openai_vision_fallback_model
                escalated += 1
            local_ai_conflict = not decision.is_shot
            candidate["ai_review"] = {
                **decision.model_dump(),
                "review_required": decision.review_required or local_ai_conflict,
                "local_ai_conflict": local_ai_conflict,
                "model": final_model,
                "attempts": attempts,
            }
            reviewed += 1
        except Exception:
            candidate["ai_review"] = {
                "status": "failed",
                "message": "AI 보조 판정을 완료하지 못했습니다. 로컬 후보는 그대로 유지됩니다.",
                "attempts": attempts,
            }
            failed += 1

    result["ai_review"] = {
        "status": "complete" if failed == 0 else "partial",
        "reviewed": reviewed,
        "failed": failed,
        "escalated": escalated,
        "primary_model": settings.openai_vision_model,
        "fallback_model": settings.openai_vision_fallback_model,
        "candidate_limit": settings.openai_max_candidate_reviews,
    }
    return result
