"""검토한 공 없음 장면을 기존 YOLO 데이터셋에 추가한다.

공처럼 보이기 쉬운 머리카락이나 조명 등이 실제로는 농구공이 아니라는
정보를 학습시키기 위한 보정 도구다. 지정한 시각의 프레임만 추가하며,
사람 상자는 기본 YOLO 모델로 만든 뒤 사용자가 미리 화면을 확인한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
from ultralytics import YOLO

from .build_yolo_dataset import draw_preview, xyxy_to_yolo


PERSON_COCO_ID = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="검토한 공 없음 장면 추가")
    parser.add_argument("source", type=Path)
    parser.add_argument("--timestamps", nargs="+", type=float, required=True)
    parser.add_argument("--base", type=Path, default=Path("datasets/basketball-v2"))
    parser.add_argument("--output", type=Path, default=Path("datasets/basketball-v3"))
    parser.add_argument("--model", default="models/yolo26n.pt")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--preview", type=Path, default=Path("work/dataset-preview-v3"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    base = args.base.resolve()
    output = args.output.resolve()
    preview = args.preview.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not base.is_dir():
        raise FileNotFoundError(base)
    if output.exists():
        raise FileExistsError(f"기존 데이터 보호를 위해 덮어쓰지 않습니다: {output}")

    shutil.copytree(base, output)
    preview.mkdir(parents=True, exist_ok=True)
    model = YOLO(args.model)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {source}")

    added: list[dict[str, object]] = []
    try:
        for timestamp in sorted(set(args.timestamps)):
            if timestamp < 0:
                raise ValueError("timestamp는 0 이상이어야 합니다.")
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"{timestamp:.3f}초 프레임을 읽지 못했습니다.")
            height, width = frame.shape[:2]
            result = model.predict(
                frame,
                classes=[PERSON_COCO_ID],
                conf=0.25,
                imgsz=args.imgsz,
                device="cpu",
                verbose=False,
            )[0]
            boxes: list[tuple[int, list[float], float]] = []
            for box in result.boxes:
                xyxy = [float(value) for value in box.xyxy[0].tolist()]
                boxes.append((0, xyxy, float(box.conf.item())))

            stem = f"{source.stem}-hard-negative-{timestamp:08.3f}s"
            image_path = output / "images" / "train" / f"{stem}.jpg"
            label_path = output / "labels" / "train" / f"{stem}.txt"
            if not cv2.imwrite(str(image_path), frame):
                raise RuntimeError(f"이미지를 저장하지 못했습니다: {image_path}")
            labels = [xyxy_to_yolo(0, xyxy, width, height) for _, xyxy, _ in boxes]
            label_path.write_text(
                ("\n".join(labels) + "\n") if labels else "", encoding="utf-8"
            )
            cv2.imwrite(str(preview / f"{stem}.jpg"), draw_preview(frame, boxes))
            added.append({"timestamp": timestamp, "people": len(boxes)})
    finally:
        capture.release()

    yaml_path = output / "data.yaml"
    yaml_path.write_text(
        "path: " + output.as_posix() + "\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: person\n"
        "  1: basketball\n"
        "  2: rim\n",
        encoding="utf-8",
    )
    manifest = {
        "base": str(base),
        "source": str(source),
        "added": added,
        "meaning": "사람은 있지만 농구공은 없는 것으로 사람이 확인한 장면",
        "warning": "이 장면들은 오탐 보정용이며 독립 성능 시험에는 사용하지 않습니다.",
    }
    (output / "manifest-v3.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return yaml_path


if __name__ == "__main__":
    run(parse_args())
