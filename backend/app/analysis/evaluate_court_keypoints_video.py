"""Measure court-keypoint stability on a real video and save visual evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from app.services.court_landmark_consensus import court_landmark_consensus


def _draw(frame: np.ndarray, points: np.ndarray | None, confidence: float) -> np.ndarray:
    output = frame.copy()
    cv2.putText(output, f"court confidence {confidence:.3f}", (20, 38),
                cv2.FONT_HERSHEY_SIMPLEX, .9, (255, 255, 255), 3)
    cv2.putText(output, f"court confidence {confidence:.3f}", (20, 38),
                cv2.FONT_HERSHEY_SIMPLEX, .9, (20, 20, 20), 1)
    if points is not None:
        for index, (x, y) in enumerate(points):
            point = (int(round(x)), int(round(y)))
            cv2.circle(output, point, 9, (0, 0, 255), 2)
            cv2.putText(output, f"P{index}", (point[0] + 10, point[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 255), 2)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-every", type=float, default=1.0)
    parser.add_argument("--imgsz", type=int, default=512)
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(args.video.resolve()))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열지 못했습니다: {args.video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    capture_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps
    model = YOLO(str(args.model.resolve()))

    rows: list[dict] = []
    detected_points: list[np.ndarray] = []
    sample_times = np.arange(0.0, duration, args.sample_every)
    for sample_index, seconds in enumerate(sample_times):
        capture.set(cv2.CAP_PROP_POS_MSEC, float(seconds * 1000))
        ok, frame = capture.read()
        if not ok:
            continue
        result = model.predict(frame, conf=.05, imgsz=args.imgsz, device="cpu", verbose=False)[0]
        confidence = 0.0
        points = None
        if result.boxes is not None and result.keypoints is not None and len(result.boxes):
            index = int(result.boxes.conf.argmax().item())
            confidence = float(result.boxes.conf[index].item())
            points = result.keypoints.xy[index].cpu().numpy()
            if points.shape != (5, 2):
                points = None
        if points is not None:
            detected_points.append(points)
        rows.append({
            "seconds": round(float(seconds), 3),
            "detected": points is not None,
            "confidence": round(confidence, 5),
            "points": points.round(2).tolist() if points is not None else [],
        })
        cv2.imwrite(str(output / f"frame-{sample_index:03d}-{seconds:06.2f}s.jpg"),
                    _draw(frame, points, confidence))
    capture.release()

    step_jitter: list[float] = []
    for previous, current in zip(detected_points, detected_points[1:]):
        step_jitter.append(float(np.linalg.norm(current - previous, axis=1).mean()))
    report = {
        "model": str(args.model.resolve()),
        "video": str(args.video.resolve()),
        "duration_seconds": round(duration, 3),
        "samples": len(rows),
        "detected": sum(bool(row["detected"]) for row in rows),
        "detection_rate": round(sum(bool(row["detected"]) for row in rows) / max(1, len(rows)), 4),
        "mean_confidence": round(float(np.mean([row["confidence"] for row in rows])), 4),
        "mean_step_jitter_px": round(float(np.mean(step_jitter)), 2) if step_jitter else None,
        "temporal_consensus": court_landmark_consensus(
            rows,
            width=int(capture_width),
            height=int(capture_height),
        ),
        "frames": rows,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "report.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=("seconds", "detected", "confidence", "points"))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: value for key, value in report.items() if key != "frames"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
