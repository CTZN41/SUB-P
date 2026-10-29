"""Build a court-keypoint dataset whose validation sources never appear in training."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.analysis.build_court_keypoint_dataset import frame_at, label_line


KEYPOINTS = ["baseline_left", "baseline_right", "free_throw_right", "free_throw_left", "hoop"]


def _copy_training_samples(source: Path, output: Path, excluded_prefixes: tuple[str, ...]) -> int:
    copied = 0
    for split in ("train", "val"):
        for image in (source / "images" / split).glob("*.jpg"):
            if image.stem.startswith(excluded_prefixes):
                continue
            label = source / "labels" / split / f"{image.stem}.txt"
            target_image = output / "images" / "train" / image.name
            target_label = output / "labels" / "train" / label.name
            target_image.parent.mkdir(parents=True, exist_ok=True)
            target_label.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, target_image)
            shutil.copy2(label, target_label)
            copied += 1
    return copied


def _write_validation(output: Path, name: str, image, points: np.ndarray) -> None:
    image_path = output / "images" / "val" / f"{name}.jpg"
    label_path = output / "labels" / "val" / f"{name}.txt"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    height, width = image.shape[:2]
    label_path.write_text(label_line(points, width, height) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--gi", type=Path, required=True)
    parser.add_argument("--photo-annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regime", choices=("generalize", "target"), default="generalize")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    manifest = json.loads(args.photo_annotations.read_text(encoding="utf-8-sig"))
    heldout = next(item for item in manifest if Path(item["path"]).name == "user-heldout.png")
    heldout_path = (args.photo_annotations.parent / heldout["path"]).resolve()
    image = cv2.imread(str(heldout_path))
    if image is None:
        raise RuntimeError(f"검증 사진을 읽지 못했습니다: {heldout_path}")
    _write_validation(output, "heldout-user-court", image, np.asarray(heldout["points"], dtype=np.float32))
    if args.regime == "generalize":
        copied = _copy_training_samples(
            args.source_dataset.resolve(), output, ("gi1535-", "gi1550-", "photo-08-")
        )
        _write_validation(
            output,
            "heldout-gi1535",
            frame_at(args.gi, 1535),
            np.float32([[1254, 615], [1917, 663], [1842, 849], [852, 711], [1550, 213]]),
        )
        _write_validation(
            output,
            "heldout-gi1550",
            frame_at(args.gi, 1550),
            np.float32([[1178, 712], [1864, 779], [1765, 937], [643, 795], [1433, 297]]),
        )
        validation_count = 3
    else:
        copied = _copy_training_samples(
            args.source_dataset.resolve(), output, ("photo-07-", "photo-08-")
        )
        commons = next(item for item in manifest if Path(item["path"]).name == "commons-23.jpg")
        commons_path = (args.photo_annotations.parent / commons["path"]).resolve()
        commons_image = cv2.imread(str(commons_path))
        if commons_image is None:
            raise RuntimeError(f"검증 사진을 읽지 못했습니다: {commons_path}")
        _write_validation(
            output,
            "heldout-commons-23",
            commons_image,
            np.asarray(commons["points"], dtype=np.float32),
        )
        validation_count = 2

    config = {
        "path": str(output),
        "train": "images/train",
        "val": "images/val",
        "names": {0: "court"},
        "kpt_shape": [5, 3],
        "flip_idx": [1, 0, 3, 2, 4],
        "keypoint_names": {0: KEYPOINTS},
    }
    (output / "data.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(json.dumps({"train": copied, "val": validation_count, "data": str(output / "data.yaml")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
