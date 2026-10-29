"""Review-first shot candidates from local MP4; no made/missed judgement."""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable

SAMPLE_FPS = 8.0


class Phase7Error(RuntimeError):
    pass


class Phase7Canceled(RuntimeError):
    pass


def center(box: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = box["xyxy"]
    return (x1 + x2) / 2, (y1 + y2) / 2


def choose(boxes: list[dict[str, Any]], previous: dict[str, Any] | None, width: int, height: int, limit: float) -> dict[str, Any] | None:
    if not boxes:
        return None
    if previous:
        nearest = min(boxes, key=lambda box: math.dist(center(box), center(previous)))
        if math.dist(center(nearest), center(previous)) < math.hypot(width, height) * limit:
            return nearest
    return max(boxes, key=lambda box: box["confidence"])


def release_candidate(previous: dict[str, Any], current: dict[str, Any], hoop: list[float], height: int) -> dict[str, Any] | None:
    if not previous.get("hoop_valid") or not current.get("hoop_valid"):
        return None
    person, ball, next_ball = previous.get("person"), previous.get("ball"), current.get("ball")
    if not person or not ball or not next_ball:
        return None
    bx, by = center(ball)
    nx, ny = center(next_ball)
    x1, y1, x2, y2 = person["xyxy"]
    w, h = x2 - x1, y2 - y1
    if not (x1 - .3*w <= bx <= x2 + .3*w and y1 - .3*h <= by <= y1 + .55*h):
        return None
    elapsed = current["time_ms"] - previous["time_ms"]
    if not 0 < elapsed <= 750 or by - ny < height * .035:
        return None
    if math.dist((nx, ny), hoop) >= math.dist((bx, by), hoop):
        return None
    confidence = min(person["confidence"], ball["confidence"], next_ball["confidence"])
    reasons = ["탐지 신뢰도가 낮음"] if confidence < .35 else []
    if previous.get("camera_shift") or current.get("camera_shift"):
        reasons.append("골대 위치 변화 — 코트 좌표 재확인 필요")
    if previous.get("rim_missing") or current.get("rim_missing"):
        reasons.append("림 자동 탐지 실패 — 기준점으로 판단")
    return {
        "time_ms": previous["time_ms"], "next_time_ms": current["time_ms"],
        "frame_index": previous["frame_index"],
        "foot_point_px": [round((x1+x2)/2, 2), round(y2, 2)],
        "ball_point_px": [round(bx, 2), round(by, 2)],
        "confidence": round(confidence, 4),
        "review_reasons": reasons,
    }


def merge_candidates(candidates: list[dict[str, Any]], cooldown_ms: int = 2000) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["time_ms"]):
        if merged and candidate["time_ms"] - merged[-1]["time_ms"] < cooldown_ms:
            if candidate["confidence"] > merged[-1]["confidence"]:
                merged[-1] = candidate
        else:
            merged.append(candidate)
    return merged


def _detections(result: Any, names: dict[int, str]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {"person": [], "basketball": [], "rim": []}
    for box in result.boxes:
        name = names.get(int(box.cls.item()))
        if name in found:
            found[name].append({"confidence": float(box.conf.item()), "xyxy": [float(value) for value in box.xyxy[0].tolist()]})
    return found


def analyze_video(source: Path, model_path: Path, hoop_point: list[float], *, sample_fps: float = SAMPLE_FPS,
                  start_ms: int = 0, end_ms: int | None = None,
                  progress: Callable[[int], None] | None = None, cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Read source frames at fixed intervals and retain their original timestamps."""
    if not source.is_file() or not model_path.is_file():
        raise Phase7Error("원본 영상 또는 탐지 모델 파일을 찾을 수 없습니다.")
    if sample_fps <= 0 or len(hoop_point) != 2:
        raise Phase7Error("분석 간격 또는 골대 기준점을 확인해 주세요.")
    from ultralytics import YOLO  # Torch must load before OpenCV on Windows.
    import cv2
    model = YOLO(str(model_path))
    names = {int(key): str(value) for key, value in model.names.items()}
    if not {"person", "basketball"}.issubset(names.values()):
        raise Phase7Error("탐지 모델에 사람 또는 농구공 항목이 없습니다.")
    wanted = [key for key, value in names.items() if value in {"person", "basketball", "rim"}]
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise Phase7Error("원본 영상을 읽을 수 없습니다.")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or count <= 0 or width <= 0 or height <= 0:
            raise Phase7Error("영상의 길이, 크기 또는 프레임 수를 읽을 수 없습니다.")
        if not (0 <= hoop_point[0] < width and 0 <= hoop_point[1] < height):
            raise Phase7Error("저장한 골대 기준점이 영상 화면 밖에 있습니다.")
        duration_ms = round(count / fps * 1000)
        if start_ms < 0 or start_ms >= duration_ms or (end_ms is not None and end_ms <= start_ms):
            raise Phase7Error("분석할 영상 구간이 올바르지 않습니다.")
        step = max(1, round(fps / sample_fps))
        first_frame = round(start_ms / 1000 * fps)
        last_frame = min(count, round((end_ms or duration_ms) / 1000 * fps))
        previous = person_last = ball_last = None
        raw: list[dict[str, Any]] = []
        issues: Counter[str] = Counter()
        sampled = person_frames = ball_frames = rim_frames = 0
        for frame_index in range(first_frame, last_frame, step):
            if cancelled and cancelled():
                raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok:
                issues["읽지 못한 프레임"] += 1
                continue
            result = model.predict(frame, classes=wanted, conf=.12, imgsz=640, device="cpu", verbose=False)[0]
            groups = _detections(result, names)
            rim = min(groups["rim"], key=lambda box: math.dist(center(box), hoop_point)) if groups["rim"] else None
            rim_distance = math.dist(center(rim), hoop_point) if rim else 0.0
            hoop_valid = rim is None or rim_distance < math.hypot(width, height) * .18
            camera_shift = bool(rim and rim_distance > math.hypot(width, height) * .05)
            if not hoop_valid:
                issues["카메라 구도 변경 — 기준점 무효"] += 1
            person = choose(groups["person"], person_last, width, height, .18)
            ball = choose(groups["basketball"], ball_last, width, height, .25)
            time_ms = round(frame_index / fps * 1000)
            sampled += 1
            person_frames += person is not None
            ball_frames += ball is not None
            rim_frames += bool(groups["rim"])
            if person is None:
                issues["선수 가림 또는 탐지 실패"] += 1
            if ball is None:
                issues["공 가림 또는 탐지 실패"] += 1
            current = {"time_ms": time_ms, "frame_index": frame_index, "person": person, "ball": ball,
                       "hoop_valid": hoop_valid, "camera_shift": camera_shift, "rim_missing": rim is None}
            candidate = release_candidate(previous, current, list(center(rim)) if rim else hoop_point, height) if previous else None
            if candidate:
                raw.append(candidate)
            previous, person_last, ball_last = current if hoop_valid else None, person if hoop_valid else None, ball if hoop_valid else None
            if progress and (sampled % 8 == 0 or frame_index + step >= last_frame):
                progress(time_ms)
        if sampled == 0 or person_frames == 0 or ball_frames == 0:
            raise Phase7Error("선수와 공을 함께 확인할 영상 증거가 부족합니다.")
        candidates = merge_candidates(raw)
        if not candidates:
            issues["슛 후보 없음 — 직접 확인 필요"] += 1
        if rim_frames == 0:
            issues["림 자동 탐지 실패 — 확인된 골대 위치 사용"] += 1
        return {"method": "phase7-yolo-candidates-v1", "notice": "슛 후보이며 성공 여부와 종류는 아직 판단하지 않았습니다.",
                "source_fps": round(fps, 4), "sample_fps": round(fps/step, 4), "source_frame_count": count,
                "duration_ms": duration_ms, "range_start_ms": start_ms, "range_end_ms": round(last_frame/fps*1000),
                "sampled_frames": sampled, "person_frames": person_frames,
                "ball_frames": ball_frames, "rim_frames": rim_frames, "hoop_point_px": hoop_point,
                "candidates": candidates, "issues": dict(issues)}
    finally:
        cap.release()


def save_result(work_path: Path, analysis_id: str, result: dict[str, Any]) -> Path:
    """Write atomically so canceled jobs cannot leave a partial result."""
    directory = (work_path / analysis_id).resolve()
    if work_path.resolve() not in directory.parents:
        raise Phase7Error("분석 결과 경로가 올바르지 않습니다.")
    directory.mkdir(parents=True, exist_ok=True)
    output, temporary = directory / "phase7-candidates.json", directory / "phase7-candidates.tmp"
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
