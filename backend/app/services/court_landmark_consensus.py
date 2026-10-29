"""Combine fixed-camera court predictions and reject unstable landmark guesses."""

from __future__ import annotations

from typing import Any, Iterable

import cv2
import numpy as np


def _valid_geometry(points: np.ndarray, width: int, height: int) -> bool:
    if points.shape != (5, 2) or not np.isfinite(points).all():
        return False
    if np.any(points[:, 0] < 0) or np.any(points[:, 0] >= width):
        return False
    if np.any(points[:, 1] < 0) or np.any(points[:, 1] >= height):
        return False
    floor = points[:4].astype(np.float32)
    hoop = points[4]
    if hoop[1] >= float(np.median(floor[:, 1]) - height * .06):
        return False
    hull = cv2.convexHull(floor).reshape(-1, 2)
    if len(hull) != 4 or cv2.contourArea(hull) < width * height * .012:
        return False
    return True


def court_landmark_consensus(
    predictions: Iterable[dict[str, Any]],
    *,
    width: int,
    height: int,
    minimum_confidence: float = .20,
    minimum_support: int = 3,
    cluster_radius_ratio: float = .035,
    maximum_jitter_ratio: float = .025,
) -> dict[str, Any]:
    """Return a stable five-point estimate or an explicit unusable result."""
    candidates: list[tuple[np.ndarray, float]] = []
    for prediction in predictions:
        confidence = float(prediction.get("confidence", 0.0))
        points = np.asarray(prediction.get("points", []), dtype=np.float32)
        if confidence >= minimum_confidence and _valid_geometry(points, width, height):
            candidates.append((points, confidence))
    if len(candidates) < minimum_support:
        return {
            "usable": False,
            "support": len(candidates),
            "confidence": 0.0,
            "points": [],
            "jitter_px": None,
            "warning": "신뢰할 수 있는 코트 기준점 프레임이 부족합니다.",
        }

    diagonal = float(np.hypot(width, height))
    radius = diagonal * cluster_radius_ratio
    arrays = [item[0] for item in candidates]
    distances = np.zeros((len(arrays), len(arrays)), dtype=np.float32)
    for left in range(len(arrays)):
        for right in range(left + 1, len(arrays)):
            distance = float(np.linalg.norm(arrays[left] - arrays[right], axis=1).mean())
            distances[left, right] = distances[right, left] = distance
    neighbors = [np.flatnonzero(distances[index] <= radius) for index in range(len(arrays))]
    best = max(range(len(neighbors)), key=lambda index: (len(neighbors[index]), candidates[index][1]))
    cluster_indices = neighbors[best]
    if len(cluster_indices) < minimum_support:
        return {
            "usable": False,
            "support": int(len(cluster_indices)),
            "confidence": 0.0,
            "points": [],
            "jitter_px": round(float(np.median(distances[best])), 2),
            "warning": "프레임마다 코트 기준점이 다른 위치로 움직입니다.",
        }

    cluster = np.stack([arrays[index] for index in cluster_indices])
    consensus = np.median(cluster, axis=0)
    jitter = np.linalg.norm(cluster - consensus, axis=2)
    p95_jitter = float(np.percentile(jitter, 95))
    support_ratio = len(cluster_indices) / len(candidates)
    usable = p95_jitter <= diagonal * maximum_jitter_ratio and support_ratio >= .60
    confidence = float(np.mean([candidates[index][1] for index in cluster_indices])) * support_ratio
    return {
        "usable": usable,
        "support": int(len(cluster_indices)),
        "candidate_count": len(candidates),
        "support_ratio": round(support_ratio, 4),
        "confidence": round(confidence, 4) if usable else 0.0,
        "points": consensus.round(2).tolist() if usable else [],
        "jitter_px": round(p95_jitter, 2),
        "warning": None if usable else "코트 기준점의 시간축 흔들림이 안전 기준을 넘었습니다.",
    }
