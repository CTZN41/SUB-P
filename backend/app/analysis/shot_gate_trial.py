"""Read-only OpenAI trial for deciding whether a YOLO candidate is a shot."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Literal

import cv2
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.services.openai_shot_review import _encode_frame
from app.analysis.candidate_windows import GATE_BEFORE_MS, GATE_AFTER_MS, PRIOR_BEFORE_MS, PRIOR_AFTER_MS


OFFSETS_MS = tuple(sorted(set(t for t in (-GATE_BEFORE_MS, -1000, -700, -400, -200, 0, 200, 500, GATE_AFTER_MS)
                               if -GATE_BEFORE_MS <= t <= GATE_AFTER_MS)))
PRIOR_OFFSETS_MS = tuple(sorted(set(t for t in (-PRIOR_BEFORE_MS, -2800, -2400, -2000, -1700, -1400, -1100, -800, -500, -200, PRIOR_AFTER_MS)
                                     if -PRIOR_BEFORE_MS <= t <= PRIOR_AFTER_MS)))


class ShotExistenceDecision(BaseModel):
    verdict: Literal["shot", "not_shot", "unclear"]
    confidence: float = Field(ge=0, le=1)
    release_offset_ms: int | None = None
    evidence: str = Field(max_length=300)


def shot_gate_frames(
    video_path: Path, time_ms: int, *, offsets_ms: tuple[int, ...] = OFFSETS_MS,
) -> list[dict]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("후보 검토용 원본 영상을 열지 못했습니다.")
    content: list[dict] = []
    try:
        for offset in offsets_ms:
            if time_ms + offset < 0:
                continue  # Do not count repeated frame zero as distinct evidence.
            capture.set(cv2.CAP_PROP_POS_MSEC, time_ms + offset)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            if width > 960:
                frame = cv2.resize(
                    frame, (960, round(height * 960 / width)),
                    interpolation=cv2.INTER_AREA,
                )
            content.extend([
                {"type": "input_text", "text": f"t={offset:+d}ms"},
                {"type": "input_image", "image_url": _encode_frame(frame), "detail": "high"},
            ])
    finally:
        capture.release()
    if sum(item["type"] == "input_image" for item in content) < 5:
        raise ValueError("슛 여부를 판단할 장면이 부족합니다.")
    return content


def review_shot_existence(
    video_path: Path, time_ms: int, settings: Settings, *, client=None,
) -> dict:
    """Return an isolated decision; never change stored shots in this trial."""
    return _cached_gate(video_path, time_ms, settings, client, False)


def review_prior_shot_existence(
    video_path: Path, time_ms: int, settings: Settings, *, client=None,
) -> dict:
    """Rescue a real release shortly before a late rebound or falling-ball cue."""
    return _cached_gate(video_path, time_ms, settings, client, True)


def _cached_gate(video_path, time_ms, settings, client, prior_window):
    compute = lambda: _review_shot_gate(video_path, time_ms, settings, client=client, prior_window=prior_window)
    if client is None or not hasattr(client, 'cached_review'):
        return compute()
    stat = video_path.stat()
    return client.cached_review({'task': 'prior_shot_gate' if prior_window else 'shot_gate',
        'source': [str(video_path.resolve()), stat.st_size, stat.st_mtime_ns],
        'anchor_ms': time_ms, 'model': settings.openai_vision_model,
        'offsets_ms': PRIOR_OFFSETS_MS if prior_window else OFFSETS_MS}, compute)


def _review_shot_gate(
    video_path: Path, time_ms: int, settings: Settings, *, client=None,
    prior_window: bool = False,
) -> dict:
    if not settings.openai_api_key:
        return {"status": "not_configured"}
    if client is None:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key,
                        timeout=settings.openai_timeout_seconds)
    frames = (
        shot_gate_frames(video_path, time_ms, offsets_ms=PRIOR_OFFSETS_MS)
        if prior_window else shot_gate_frames(video_path, time_ms)
    )
    prompt = (
        "These are chronological basketball frames relative to a late local candidate at t=0. "
        "Check whether the same player makes a real shot attempt anywhere in the earlier "
        f"-{PRIOR_BEFORE_MS}..0ms window, even if the local candidate at 0 is only a rebound or falling ball. "
        "A dunk attempt counts even if it misses. Return shot only if there is a visible intentional "
        "release or downward force toward the hoop. If shot, return release_offset_ms relative to "
        "t=0 as the earliest clear throw or dunk motion. Otherwise return not_shot, or unclear "
        "when visibility is insufficient. Describe concrete evidence."
        if prior_window else
        "These are chronological basketball frames around t=0. Decide only whether "
        "this specific candidate is a new shot release: the player intentionally sends "
        "the ball toward the basket. Do not assume the detector is right. A rebound, "
        "catch, pass, dribble, or ball returning to the player is not a new shot. "
        "Do not count an earlier or later shot outside this narrow window. If the "
        "release cannot be seen, return unclear. For a confirmed shot, give the "
        "earliest visible release_offset_ms relative to t=0; use null when its "
        "timing is uncertain. State only concrete visible evidence."
    )
    started = perf_counter()
    response = client.responses.parse(
        model=settings.openai_vision_model,
        reasoning={"effort": "low"},
        store=False,
        input=[{"role": "user", "content": [{
            "type": "input_text",
            "text": prompt,
        }, *frames]}],
        text_format=ShotExistenceDecision,
    )
    decision = response.output_parsed
    if decision is None:
        raise ValueError("AI가 슛 여부 판정 형식에 맞는 결과를 반환하지 않았습니다.")
    usage = response.usage
    offsets = PRIOR_OFFSETS_MS if prior_window else OFFSETS_MS
    used_offsets = [int(item['text'][2:-2]) for item in frames if item['type'] == 'input_text']
    return {
        "status": "complete",
        "model": settings.openai_vision_model,
        **decision.model_dump(),
        "usage": {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        },
        "seconds": round(perf_counter() - started, 2),
        'window': {'anchor_ms': time_ms, 'purpose': 'prior_shot_gate' if prior_window else 'shot_gate',
                   'requested_offsets_ms': [min(offsets), max(offsets)], 'used_offsets_ms': used_offsets,
                   'edge_truncated': len(used_offsets) < len(offsets)},
    }


def review_adaptive_candidates(
    video_path: Path, trial: dict, settings: Settings, *, client=None,
) -> dict:
    """Keep false candidates out of trial results; try an earlier pose clue first."""
    candidates = [*trial["base"]["candidates"], *trial["novel_candidates"]]
    hints = trial["base"].get("motion_hints", [])
    decisions: list[dict] = []
    accepted_times: list[int] = []
    needs_rescan_times: list[int] = []
    needs_review_times: list[int] = []
    for candidate in candidates:
        time_ms = int(candidate["time_ms"])
        try:
            verdict = review_shot_existence(video_path, time_ms, settings, client=client)
        except Exception:
            decisions.append({"candidate_time_ms": time_ms, "status": "failed"})
            needs_review_times.append(time_ms)
            continue
        record = {"candidate_time_ms": time_ms, "review": verdict}
        if verdict.get("verdict") == "not_shot" and verdict.get("confidence", 0) >= .9:
            alternatives = [
                hint for hint in hints
                if 500 <= time_ms - int(hint["time_ms"]) <= 1800
                and hint["confidence"] >= .5
                and int(hint["scene_id"]) == int(candidate.get("scene_id", 0))
            ]
            if alternatives:
                hint = max(alternatives, key=lambda item: item["confidence"])
                alt_time = int(hint["time_ms"])
                try:
                    alt_review = review_shot_existence(
                        video_path, alt_time, settings, client=client,
                    )
                except Exception:
                    alt_review = {"status": "failed"}
                record["earlier_pose_hint"] = {
                    "time_ms": alt_time, "review": alt_review,
                }
                if (alt_review.get("verdict") == "shot"
                        and alt_review.get("confidence", 0) >= .9):
                    record["trial_action"] = "retime_to_pose_hint"
                    accepted_times.append(alt_time)
                    decisions.append(record)
                    continue
                if alt_review.get("status") == "failed":
                    record["trial_action"] = "needs_review"
                    needs_review_times.append(time_ms)
                    decisions.append(record)
                    continue
            record["trial_action"] = "needs_rescan"
            needs_rescan_times.append(time_ms)
        elif verdict.get("verdict") == "shot" and verdict.get("confidence", 0) >= .9:
            record["trial_action"] = "keep"
            accepted_times.append(time_ms)
        else:
            record["trial_action"] = "needs_review"
            needs_review_times.append(time_ms)
        decisions.append(record)
    return {
        "decisions": decisions,
        "accepted_times_ms": sorted(accepted_times),
        "needs_rescan_times_ms": sorted(needs_rescan_times),
        "needs_review_times_ms": sorted(needs_review_times),
    }
