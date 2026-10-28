"""검토한 추가 영상으로 기존 농구 데이터셋을 확장한다.

공을 확실히 찾은 프레임만 추가해, 화면에 공이 있는데 정답 상자가 없는
잘못된 학습 자료가 늘어나는 일을 줄인다. Pexels 세로 영상은 카메라가
고정되어 있으므로 사람이 확인한 고정 림 위치를 함께 사용한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from ultralytics import YOLO
import cv2

from .build_yolo_dataset import CLASS_NAMES, draw_preview, xyxy_to_yolo


PERSON_COCO_ID = 0
SPORTS_BALL_COCO_ID = 32

# 1080x1920 고정 세로 영상에서 사람이 대표 장면 6장을 확인한 림 위치입니다.
FIXED_RIM_BOXES = {
    "pexels-35599326": [235.0, 830.0, 360.0, 945.0],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="농구 YOLO 데이터셋 다양성 확장")
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--base", type=Path, default=Path("datasets/basketball-v1"))
    parser.add_argument("--output", type=Path, default=Path("datasets/basketball-v2"))
    parser.add_argument("--model", default="models/yolo26n.pt")
    parser.add_argument("--every", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--preview", type=Path, default=Path("work/dataset-preview-v2"))
    return parser.parse_args()


def select_boxes(result: Any) -> list[tuple[int, list[float], float]]:
    people: list[tuple[int, list[float], float]] = []
    balls: list[tuple[int, list[float], float]] = []
    for box in result.boxes:
        source_class = int(box.cls.item())
        confidence = float(box.conf.item())
        xyxy = [float(value) for value in box.xyxy[0].tolist()]
        x1, y1, x2, y2 = xyxy
        width = x2 - x1
        height = y2 - y1
        if source_class == PERSON_COCO_ID and confidence >= 0.25:
            people.append((0, xyxy, confidence))
        elif source_class == SPORTS_BALL_COCO_ID and confidence >= 0.10:
            aspect = width / max(height, 1.0)
            if 8 <= width <= 600 and 8 <= height <= 600 and 0.45 <= aspect <= 2.2:
                balls.append((1, xyxy, confidence))
    if not balls:
        return []
    return people + [max(balls, key=lambda item: item[2])]


def remove_ball_inside_rim(
    boxes: list[tuple[int, list[float], float]], rim: list[float]
) -> list[tuple[int, list[float], float]]:
    rim_x1, rim_y1, rim_x2, rim_y2 = rim
    filtered: list[tuple[int, list[float], float]] = []
    for class_id, xyxy, confidence in boxes:
        if class_id != 1:
            filtered.append((class_id, xyxy, confidence))
            continue
        x1, y1, x2, y2 = xyxy
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        if rim_x1 <= center_x <= rim_x2 and rim_y1 <= center_y <= rim_y2:
            continue
        filtered.append((class_id, xyxy, confidence))
    return filtered


def run(args: argparse.Namespace) -> Path:
    base = args.base.resolve()
    output = args.output.resolve()
    preview = args.preview.resolve()
    if not base.is_dir():
        raise FileNotFoundError(base)
    if output.exists():
        raise FileExistsError(f"기존 데이터 보호를 위해 덮어쓰지 않습니다: {output}")
    if args.every <= 0:
        raise ValueError("every는 0보다 커야 합니다.")

    shutil.copytree(base, output)
    preview.mkdir(parents=True, exist_ok=True)
    model = YOLO(args.model)
    added = {"images": 0, "person": 0, "basketball": 0, "rim": 0}
    source_counts: dict[str, int] = {}
    preview_count = 0

    for raw_source in args.sources:
        source = raw_source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError(f"영상을 열 수 없습니다: {source}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frames / fps if fps else 0.0
        timestamp = 0.0
        source_counts[source.stem] = 0

        try:
            while timestamp <= duration:
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = capture.read()
                if not ok:
                    break
                height, width = frame.shape[:2]
                result = model.predict(
                    frame,
                    classes=[PERSON_COCO_ID, SPORTS_BALL_COCO_ID],
                    conf=0.05,
                    imgsz=args.imgsz,
                    device="cpu",
                    verbose=False,
                )[0]
                fixed_rim = FIXED_RIM_BOXES.get(source.stem)
                boxes = select_boxes(result)
                if fixed_rim is not None:
                    boxes = remove_ball_inside_rim(boxes, fixed_rim)
                if not any(class_id == 1 for class_id, _, _ in boxes):
                    timestamp += args.every
                    continue
                if fixed_rim is not None:
                    boxes.append((2, fixed_rim, 1.0))

                stem = f"{source.stem}-{timestamp:08.3f}s"
                image_path = output / "images" / "train" / f"{stem}.jpg"
                label_path = output / "labels" / "train" / f"{stem}.txt"
                cv2.imwrite(str(image_path), frame)
                labels = [xyxy_to_yolo(class_id, xyxy, width, height) for class_id, xyxy, _ in boxes]
                label_path.write_text("\n".join(labels) + "\n", encoding="utf-8")

                added["images"] += 1
                source_counts[source.stem] += 1
                for class_id, _, _ in boxes:
                    added[CLASS_NAMES[class_id]] += 1
                if preview_count < 40:
                    cv2.imwrite(str(preview / f"{stem}.jpg"), draw_preview(frame, boxes))
                    preview_count += 1
                timestamp += args.every
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
        "sources": [str(source.resolve()) for source in args.sources],
        "sampling_seconds": args.every,
        "added": added,
        "per_source_images": source_counts,
        "held_out": "pexels-6249343.mp4는 외부 재시험용으로 추가하지 않음",
        "warning": "공을 자동 탐지한 프레임만 추가한 시험용 표시이며 사람 검토가 필요합니다.",
    }
    (output / "manifest-v2.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return yaml_path


if __name__ == "__main__":
    run(parse_args())
