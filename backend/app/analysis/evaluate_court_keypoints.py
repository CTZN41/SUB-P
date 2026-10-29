"""Evaluate court pose models on source-disjoint, manually labelled frames."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


def _truth(label_path: Path, width: int, height: int) -> np.ndarray:
    values = [float(value) for value in label_path.read_text(encoding="utf-8").split()]
    points = np.asarray(values[5:], dtype=np.float32).reshape(-1, 3)[:, :2]
    points[:, 0] *= width
    points[:, 1] *= height
    return points


def _draw_overlay(frame: np.ndarray, truth: np.ndarray, predicted: np.ndarray | None) -> np.ndarray:
    overlay = frame.copy()
    for index, (x, y) in enumerate(truth):
        point = (int(round(x)), int(round(y)))
        cv2.circle(overlay, point, 8, (0, 200, 0), 2)
        cv2.putText(overlay, f"T{index}", (point[0] + 9, point[1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 150, 0), 2)
    if predicted is not None:
        for index, (x, y) in enumerate(predicted):
            point = (int(round(x)), int(round(y)))
            cv2.drawMarker(overlay, point, (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
            cv2.putText(overlay, f"P{index}", (point[0] + 9, point[1] + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 200), 2)
    return overlay


def evaluate(model_path: Path, dataset: Path, imgsz: int, visual_dir: Path | None = None) -> dict:
    model = YOLO(str(model_path))
    samples = []
    all_errors: list[float] = []
    detected = 0
    for image_path in sorted((dataset / "images" / "val").glob("*.jpg")):
        frame = cv2.imread(str(image_path))
        height, width = frame.shape[:2]
        truth = _truth(dataset / "labels" / "val" / f"{image_path.stem}.txt", width, height)
        result = model.predict(frame, conf=.05, imgsz=imgsz, device="cpu", verbose=False)[0]
        if result.boxes is None or result.keypoints is None or len(result.boxes) == 0:
            samples.append({"sample": image_path.stem, "detected": False})
            if visual_dir:
                visual_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(visual_dir / f"{image_path.stem}.jpg"), _draw_overlay(frame, truth, None))
            continue
        index = int(result.boxes.conf.argmax().item())
        predicted = result.keypoints.xy[index].cpu().numpy()
        if predicted.shape != truth.shape:
            samples.append({"sample": image_path.stem, "detected": False})
            continue
        errors = np.linalg.norm(predicted - truth, axis=1)
        if visual_dir:
            visual_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(visual_dir / f"{image_path.stem}.jpg"), _draw_overlay(frame, truth, predicted))
        diagonal = float(np.hypot(width, height))
        all_errors.extend(errors.tolist())
        detected += 1
        samples.append({
            "sample": image_path.stem,
            "detected": True,
            "mean_error_px": round(float(errors.mean()), 2),
            "max_error_px": round(float(errors.max()), 2),
            "mean_error_percent_diagonal": round(float(errors.mean() / diagonal * 100), 2),
        })
    return {
        "model": str(model_path),
        "imgsz": imgsz,
        "detected": detected,
        "total": len(samples),
        "detection_rate": round(detected / max(1, len(samples)), 4),
        "mean_error_px": round(float(np.mean(all_errors)), 2) if all_errors else None,
        "p95_error_px": round(float(np.percentile(all_errors, 95)), 2) if all_errors else None,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, action="append", required=True)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--visual-dir", type=Path)
    args = parser.parse_args()
    report = []
    for path in args.model:
        visual_dir = None
        if args.visual_dir:
            visual_dir = args.visual_dir / path.parent.parent.name
        report.append(evaluate(path.resolve(), args.dataset.resolve(), args.imgsz, visual_dir))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
