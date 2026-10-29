"""Build a small, reproducible court-landmark pose dataset from approved sample videos."""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

import cv2
import numpy as np
import yaml


KEYPOINTS = ["baseline_left", "baseline_right", "free_throw_right", "free_throw_left", "hoop"]


def frame_at(source: Path, second: float) -> np.ndarray:
    capture = cv2.VideoCapture(str(source))
    capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"프레임을 읽지 못했습니다: {source} {second}s")
    return frame


def label_line(points: np.ndarray, width: int, height: int) -> str:
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    margin = np.asarray([width, height], dtype=np.float32) * .035
    minimum = np.maximum([0, 0], minimum - margin)
    maximum = np.minimum([width - 1, height - 1], maximum + margin)
    center = (minimum + maximum) / 2
    size = maximum - minimum
    values = [0, center[0] / width, center[1] / height, size[0] / width, size[1] / height]
    for x, y in points:
        values.extend([x / width, y / height, 2])
    return " ".join(str(int(value)) if isinstance(value, int) else f"{float(value):.7f}" for value in values)


def write_sample(root: Path, split: str, name: str, frame: np.ndarray, points: np.ndarray) -> None:
    image_path = root / "images" / split / f"{name}.jpg"
    label_path = root / "labels" / split / f"{name}.txt"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    height, width = frame.shape[:2]
    label_path.write_text(label_line(points, width, height) + "\n", encoding="utf-8")


def augmented(frame: np.ndarray, points: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = random.Random(seed)
    height, width = frame.shape[:2]
    jitter = min(width, height) * .035
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    target = source + np.float32([[rng.uniform(-jitter, jitter), rng.uniform(-jitter, jitter)] for _ in range(4)])
    matrix = cv2.getPerspectiveTransform(source, target)
    result = cv2.warpPerspective(frame, matrix, (width, height), borderMode=cv2.BORDER_REFLECT_101)
    transformed = cv2.perspectiveTransform(points.reshape(1, -1, 2).astype(np.float32), matrix)[0]
    transformed[:, 0] = np.clip(transformed[:, 0], 0, width - 1)
    transformed[:, 1] = np.clip(transformed[:, 1], 0, height - 1)
    alpha = rng.uniform(.78, 1.18)
    beta = rng.uniform(-18, 18)
    result = cv2.convertScaleAbs(result, alpha=alpha, beta=beta)
    if rng.random() < .45:
        result = cv2.GaussianBlur(result, (3, 3), 0)
    return result, transformed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gi", type=Path, required=True)
    parser.add_argument("--mx", type=Path, required=True)
    parser.add_argument("--negative", type=Path, action="append", default=[])
    parser.add_argument(
        "--photo-annotations",
        type=Path,
        help="JSON file containing reusable still-photo paths and five labelled points",
    )
    parser.add_argument("--output", type=Path, default=Path("work/court-keypoints-dataset"))
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists():
        shutil.rmtree(root)

    # Point order: baseline left/right, free-throw right/left, hoop.
    references = [
        ("gi1535", frame_at(args.gi, 1535), np.float32([[1254, 615], [1917, 663], [1842, 849], [852, 711], [1550, 213]])),
        ("gi1550", frame_at(args.gi, 1550), np.float32([[1178, 712], [1864, 779], [1765, 937], [643, 795], [1433, 297]])),
        ("mx90", frame_at(args.mx, 90), np.float32([[390, 780], [1200, 740], [1650, 990], [550, 910], [1039, 377]])),
    ]
    counter = 0
    for prefix, frame, points in references:
        for index in range(36):
            transformed_frame, transformed_points = augmented(frame, points, counter + 4100)
            split = "val" if index % 6 == 0 else "train"
            write_sample(root, split, f"{prefix}-{index:02d}", transformed_frame, transformed_points)
            counter += 1

    # Real held-out frames from the fixed indoor camera preserve player occlusion changes.
    mx_points = references[2][2]
    for index, second in enumerate(range(60, 181, 5)):
        split = "val" if index % 5 == 0 else "train"
        write_sample(root, split, f"mx-real-{second}", frame_at(args.mx, second), mx_points)

    if args.photo_annotations:
        photo_manifest = json.loads(args.photo_annotations.read_text(encoding="utf-8-sig"))
        for photo_index, item in enumerate(photo_manifest):
            source = Path(item["path"])
            if not source.is_absolute():
                source = (args.photo_annotations.parent / source).resolve()
            frame = cv2.imread(str(source))
            points = np.asarray(item["points"], dtype=np.float32)
            if frame is None:
                raise RuntimeError(f"사진을 읽지 못했습니다: {source}")
            if points.shape != (5, 2):
                raise RuntimeError(f"사진 기준점은 다섯 개여야 합니다: {source}")
            height, width = frame.shape[:2]
            if np.any(points < 0) or np.any(points[:, 0] >= width) or np.any(points[:, 1] >= height):
                raise RuntimeError(f"사진 기준점이 화면 범위를 벗어났습니다: {source}")
            variants = int(item.get("variants", 18))
            for variant in range(variants):
                transformed_frame, transformed_points = augmented(
                    frame, points, 12000 + photo_index * 100 + variant
                )
                split = "val" if variant % 6 == 0 else "train"
                write_sample(
                    root,
                    split,
                    f"photo-{photo_index:02d}-{variant:02d}",
                    transformed_frame,
                    transformed_points,
                )

    for index, source in enumerate(args.negative):
        frame = cv2.imread(str(source))
        if frame is None:
            raise RuntimeError(f"음성 표본을 읽지 못했습니다: {source}")
        for variant in range(10):
            transformed, _ = augmented(frame, np.float32([[frame.shape[1] / 2, frame.shape[0] / 2]]), 9000 + index * 20 + variant)
            split = "val" if variant % 5 == 0 else "train"
            image_path = root / "images" / split / f"negative-{index:02d}-{variant:02d}.jpg"
            label_path = root / "labels" / split / f"negative-{index:02d}-{variant:02d}.txt"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(image_path), transformed, [cv2.IMWRITE_JPEG_QUALITY, 92])
            label_path.write_text("", encoding="utf-8")

    config = {
        "path": str(root),
        "train": "images/train",
        "val": "images/val",
        "names": {0: "court"},
        "kpt_shape": [5, 3],
        "flip_idx": [1, 0, 3, 2, 4],
        "keypoint_names": {0: KEYPOINTS},
    }
    (root / "data.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(root / "data.yaml")


if __name__ == "__main__":
    main()
