"""YOLO-World의 사용자 지정 단어 탐지를 학습 자료 후보 장면에서 확인한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import YOLOWorld
import cv2


PROMPTS = ["person", "basketball", "basketball hoop"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLO-World 농구 대상 미리보기")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model", default="models/yolov8s-worldv2.pt")
    parser.add_argument("--output", type=Path, default=Path("work/zero-shot-preview"))
    parser.add_argument("--conf", type=float, default=0.08)
    parser.add_argument("--imgsz", type=int, default=960)
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    args.output.mkdir(parents=True, exist_ok=True)
    model = YOLOWorld(args.model)
    model.set_classes(PROMPTS)
    for image_path in args.images:
        frame = cv2.imread(str(image_path))
        if frame is None:
            raise FileNotFoundError(image_path)
        result = model.predict(
            frame,
            conf=args.conf,
            imgsz=args.imgsz,
            device="cpu",
            verbose=False,
        )[0]
        target = args.output / f"{image_path.parent.parent.name}-{image_path.name}"
        cv2.imwrite(str(target), result.plot())
        counts = {name: 0 for name in PROMPTS}
        for box in result.boxes:
            counts[PROMPTS[int(box.cls.item())]] += 1
        print(f"{image_path}: {counts} -> {target}")


if __name__ == "__main__":
    run(parse_args())
