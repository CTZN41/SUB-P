"""Suggest named basketball-court landmarks without silently inventing hidden points."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.court_landmark_consensus import court_landmark_consensus
from app.services.court_zones import FREE_THROW_LINE_Y_FT, PAINT_LEFT_X_FT, PAINT_RIGHT_X_FT


LANE_COURT_POINTS = [
    [PAINT_LEFT_X_FT, 0.0], [PAINT_RIGHT_X_FT, 0.0],
    [PAINT_RIGHT_X_FT, FREE_THROW_LINE_Y_FT],
    [PAINT_LEFT_X_FT, FREE_THROW_LINE_Y_FT],
]
LANDMARK_NAMES = [
    "베이스라인 왼쪽",
    "베이스라인 오른쪽",
    "자유투선 오른쪽",
    "자유투선 왼쪽",
]


@lru_cache(maxsize=2)
def _model(path: str):
    from ultralytics import YOLO

    return YOLO(path)


def _detect_hoop(frame: np.ndarray, model_path: Path) -> tuple[list[float] | None, float]:
    if not model_path.is_file():
        return None, 0.0
    model = _model(str(model_path.resolve()))
    names = {int(key): str(value) for key, value in model.names.items()}
    rim_classes = [key for key, value in names.items() if value == "rim"]
    if not rim_classes:
        return None, 0.0
    result = model.predict(frame, classes=rim_classes, conf=.10, imgsz=960, device="cpu", verbose=False)[0]
    candidates: list[tuple[float, list[float]]] = []
    if result.boxes is not None:
        for xyxy, confidence in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist()):
            x1, y1, x2, y2 = map(float, xyxy)
            candidates.append((float(confidence), [(x1 + x2) / 2, (y1 + y2) / 2]))
    if not candidates:
        return None, 0.0
    confidence, center = max(candidates, key=lambda item: item[0])
    return [round(center[0], 2), round(center[1], 2)], round(confidence, 4)


def _order_lane(points: np.ndarray, hoop: list[float]) -> list[list[float]] | None:
    """Return baseline-left/right then free-throw-right/left."""
    pts = points.astype(np.float64)
    distances = np.linalg.norm(pts - np.asarray(hoop, dtype=np.float64), axis=1)
    baseline_indices = list(np.argsort(distances)[:2])
    free_indices = [index for index in range(4) if index not in baseline_indices]
    # The two closest points must describe one edge, not opposite corners.
    polygon = cv2.convexHull(pts.astype(np.float32), returnPoints=True).reshape(-1, 2)
    if len(polygon) != 4:
        return None
    baseline = pts[baseline_indices]
    free = pts[free_indices]
    direct = np.linalg.norm(baseline[0] - free[0]) + np.linalg.norm(baseline[1] - free[1])
    crossed = np.linalg.norm(baseline[0] - free[1]) + np.linalg.norm(baseline[1] - free[0])
    if direct <= crossed:
        pairs = [(baseline[0], free[0]), (baseline[1], free[1])]
    else:
        pairs = [(baseline[0], free[1]), (baseline[1], free[0])]
    pairs.sort(key=lambda pair: pair[0][0])
    left_baseline, left_free = pairs[0]
    right_baseline, right_free = pairs[1]
    return [
        [round(float(left_baseline[0]), 2), round(float(left_baseline[1]), 2)],
        [round(float(right_baseline[0]), 2), round(float(right_baseline[1]), 2)],
        [round(float(right_free[0]), 2), round(float(right_free[1]), 2)],
        [round(float(left_free[0]), 2), round(float(left_free[1]), 2)],
    ]


def _colored_lane(frame: np.ndarray, hoop: list[float]) -> tuple[list[list[float]] | None, float]:
    """Find a distinctly painted lane as a convex four-corner floor region."""
    height, width = frame.shape[:2]
    scaled_width = 640
    scale = scaled_width / width
    scaled_height = max(1, round(height * scale))
    small = cv2.resize(frame, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
    floor_start = round(scaled_height * .34)
    roi = small[floor_start:]
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    pixels = lab.reshape(-1, 3).astype(np.float32)
    cv2.setRNGSeed(42)
    _, labels, _ = cv2.kmeans(
        pixels,
        8,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, .5),
        3,
        cv2.KMEANS_PP_CENTERS,
    )
    labels = labels.reshape(roi.shape[:2])
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    candidates: list[tuple[float, np.ndarray]] = []
    frame_area = float(scaled_width * scaled_height)
    for cluster in range(8):
        mask = np.uint8(labels == cluster) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8), iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if not frame_area * .025 <= area <= frame_area * .24:
                continue
            hull = cv2.convexHull(contour)
            approximation = cv2.approxPolyDP(hull, .03 * cv2.arcLength(hull, True), True)
            if len(approximation) != 4:
                continue
            points = approximation.reshape(-1, 2).astype(np.float32)
            points[:, 1] += floor_start
            x, y, box_width, box_height = cv2.boundingRect(points.astype(np.int32))
            if not (.25 <= box_width / scaled_width <= .90 and .10 <= box_height / scaled_height <= .48):
                continue
            if x <= 2 and x + box_width >= scaled_width - 2:
                continue
            if y + box_height / 2 < scaled_height * .48:
                continue
            fill = area / max(1.0, cv2.contourArea(hull))
            if fill < .62:
                continue
            cluster_pixels = hsv[floor_start:][labels == cluster]
            saturation = float(np.median(cluster_pixels[:, 1])) / 255 if len(cluster_pixels) else 0.0
            # Painted lanes are typically more chromatic than the surrounding wood/asphalt.
            score = min(1.0, fill * .45 + saturation * .35 + min(area / frame_area / .10, 1.0) * .20)
            candidates.append((score, points / scale))
    if not candidates:
        return None, 0.0
    score, points = max(candidates, key=lambda item: item[0])
    ordered = _order_lane(points, hoop)
    return ordered, round(score, 4) if ordered else 0.0


def detect_court_landmarks(frame: np.ndarray, model_path: Path) -> dict[str, Any]:
    hoop, hoop_confidence = _detect_hoop(frame, model_path)
    if hoop is None:
        return {
            "usable": False,
            "method": "painted-lane-and-yolo-rim-v1",
            "confidence": 0.0,
            "image_points": [],
            "court_points": [],
            "hoop_point": None,
            "landmark_names": LANDMARK_NAMES,
            "warnings": ["골대 중심을 자동으로 찾지 못했습니다."],
        }
    points, lane_confidence = _colored_lane(frame, hoop)
    if points is None:
        return {
            "usable": False,
            "method": "painted-lane-and-yolo-rim-v1",
            "confidence": round(hoop_confidence * .35, 4),
            "image_points": [],
            "court_points": [],
            "hoop_point": hoop,
            "landmark_names": LANDMARK_NAMES,
            "warnings": ["이 장면에서 이름을 확정할 수 있는 페인트존 네 모서리를 찾지 못했습니다."],
        }
    confidence = round(lane_confidence * .75 + hoop_confidence * .25, 4)
    usable = confidence >= .55
    warnings = [] if usable else ["자동 기준점의 신뢰도가 낮아 직접 확인이 필요합니다."]
    return {
        "usable": usable,
        "method": "painted-lane-and-yolo-rim-v1",
        "confidence": confidence,
        "image_points": points,
        "court_points": LANE_COURT_POINTS,
        "hoop_point": hoop,
        "landmark_names": LANDMARK_NAMES,
        "warnings": warnings,
    }


def detect_court_landmarks_temporally(
    frames: list[np.ndarray], pose_model_path: Path
) -> dict[str, Any]:
    """Use a pose model only when several nearby frames agree on the same five points."""
    if not frames or not pose_model_path.is_file():
        return {
            "usable": False,
            "method": "temporal-yolo-pose-v1",
            "confidence": 0.0,
            "image_points": [],
            "court_points": [],
            "hoop_point": None,
            "landmark_names": LANDMARK_NAMES,
            "warnings": ["시간축 코트 기준점 모델을 사용할 수 없습니다."],
        }
    height, width = frames[0].shape[:2]
    model = _model(str(pose_model_path.resolve()))
    predictions = []
    for frame in frames:
        if frame.shape[:2] != (height, width):
            continue
        result = model.predict(frame, conf=.05, imgsz=512, device="cpu", verbose=False)[0]
        if result.boxes is None or result.keypoints is None or len(result.boxes) == 0:
            continue
        index = int(result.boxes.conf.argmax().item())
        points = result.keypoints.xy[index].cpu().numpy()
        if points.shape != (5, 2):
            continue
        predictions.append({
            "confidence": float(result.boxes.conf[index].item()),
            "points": points.tolist(),
        })
    consensus = court_landmark_consensus(predictions, width=width, height=height)
    if not consensus["usable"]:
        return {
            "usable": False,
            "method": "temporal-yolo-pose-v1",
            "confidence": 0.0,
            "image_points": [],
            "court_points": [],
            "hoop_point": None,
            "landmark_names": LANDMARK_NAMES,
            "warnings": [consensus["warning"]],
        }
    points = consensus["points"]
    return {
        "usable": True,
        "method": "temporal-yolo-pose-v1",
        "confidence": consensus["confidence"],
        "image_points": points[:4],
        "court_points": LANE_COURT_POINTS,
        "hoop_point": points[4],
        "landmark_names": LANDMARK_NAMES,
        "warnings": ["여러 프레임의 보조 제안입니다. 저장 전에 코트 선과 점 위치를 확인해 주세요."],
    }
