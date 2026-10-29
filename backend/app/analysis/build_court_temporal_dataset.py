"""Add real fixed-camera frames with visibility-aware optical-flow landmark labels."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.analysis.build_court_keypoint_dataset import KEYPOINTS, frame_at


def _label_line(points: np.ndarray, visible: np.ndarray, width: int, height: int) -> str:
    shown = points[visible]
    if len(shown) < 3:
        raise ValueError("보이는 기준점이 세 개보다 적습니다.")
    minimum = shown.min(axis=0)
    maximum = shown.max(axis=0)
    margin = np.asarray([width, height], dtype=np.float32) * .035
    minimum = np.maximum([0, 0], minimum - margin)
    maximum = np.minimum([width - 1, height - 1], maximum + margin)
    center = (minimum + maximum) / 2
    size = maximum - minimum
    values = [0, center[0] / width, center[1] / height, size[0] / width, size[1] / height]
    for point, is_visible in zip(points, visible):
        if is_visible:
            values.extend([point[0] / width, point[1] / height, 2])
        else:
            values.extend([0.0, 0.0, 0])
    return " ".join(f"{float(value):.7f}" for value in values)


def _track(anchor: np.ndarray, target: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    anchor_gray = cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY)
    target_gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
    source = points.reshape(-1, 1, 2).astype(np.float32)
    settings = dict(winSize=(51, 51), maxLevel=5,
                    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, .01))
    forward, status_forward, _ = cv2.calcOpticalFlowPyrLK(anchor_gray, target_gray, source, None, **settings)
    backward, status_backward, _ = cv2.calcOpticalFlowPyrLK(target_gray, anchor_gray, forward, None, **settings)
    tracked = forward.reshape(-1, 2)
    round_trip_error = np.linalg.norm(backward.reshape(-1, 2) - points, axis=1)
    visible = (status_forward.reshape(-1) == 1) & (status_backward.reshape(-1) == 1)
    visible &= round_trip_error <= 4.0
    return tracked, visible, round_trip_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(base, output)

    anchors = [
        (5.0, np.float32([[1254, 615], [1917, 663], [1842, 849], [852, 711], [1550, 213]])),
        (20.0, np.float32([[1178, 712], [1864, 779], [1765, 937], [643, 795], [1433, 297]])),
    ]
    anchor_frames = [(second, frame_at(args.video, second), points) for second, points in anchors]
    records = []
    for second in np.arange(6.0, 20.0, 1.0):
        anchor_second, anchor_frame, anchor_points = min(anchor_frames, key=lambda item: abs(item[0] - second))
        target = frame_at(args.video, float(second))
        tracked, visible, errors = _track(anchor_frame, target, anchor_points)
        if int(visible.sum()) < 3:
            records.append({"second": second, "saved": False, "visible": int(visible.sum()),
                            "round_trip_error": errors.round(2).tolist()})
            continue
        height, width = target.shape[:2]
        name = f"gi-temporal-{int(second):02d}"
        cv2.imwrite(str(output / "images" / "train" / f"{name}.jpg"), target,
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        (output / "labels" / "train" / f"{name}.txt").write_text(
            _label_line(tracked, visible, width, height) + "\n", encoding="utf-8"
        )
        records.append({"second": second, "saved": True, "anchor": anchor_second,
                        "visible": int(visible.sum()), "points": tracked.round(2).tolist(),
                        "round_trip_error": errors.round(2).tolist()})

    config = yaml.safe_load((output / "data.yaml").read_text(encoding="utf-8"))
    config["path"] = str(output)
    config["keypoint_names"] = {0: KEYPOINTS}
    (output / "data.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (output / "temporal-labels.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"saved": sum(bool(item["saved"]) for item in records),
                      "total": len(records), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
