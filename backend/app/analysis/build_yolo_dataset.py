"""샘플 영상에서 YOLO26 1차 학습용 데이터셋을 반자동으로 만든다.

사람과 농구공은 COCO 기본 모델의 탐지를 초기 표시로 사용한다. 림은
이번 야외 샘플의 붉은색 림을 영상 처리로 찾는다. 자동 표시는 완전한
정답이 아니며 최종 학습 전 사람이 검토해야 한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ultralytics import YOLO
import cv2
import numpy as np


PERSON_COCO_ID = 0
SPORTS_BALL_COCO_ID = 32
CLASS_NAMES = {0: "person", 1: "basketball", 2: "rim"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="농구 영상 YOLO 데이터셋 생성")
    parser.add_argument("source", type=Path)
    parser.add_argument("--start", type=float, default=575.0)
    parser.add_argument("--end", type=float, default=625.0)
    parser.add_argument("--every", type=float, default=0.25)
    parser.add_argument("--val-start", type=float, default=615.0)
    parser.add_argument("--model", default="models/yolo26n.pt")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--output", type=Path, default=Path("datasets/basketball-v1"))
    parser.add_argument("--preview", type=Path, default=Path("work/dataset-preview"))
    return parser.parse_args()


def xyxy_to_yolo(class_id: int, xyxy: list[float], width: int, height: int) -> str:
    x1, y1, x2, y2 = xyxy
    center_x = ((x1 + x2) / 2) / width
    center_y = ((y1 + y2) / 2) / height
    box_width = (x2 - x1) / width
    box_height = (y2 - y1) / height
    values = [
        max(0.0, min(1.0, center_x)),
        max(0.0, min(1.0, center_y)),
        max(0.0, min(1.0, box_width)),
        max(0.0, min(1.0, box_height)),
    ]
    return f"{class_id} " + " ".join(f"{value:.6f}" for value in values)


def find_rim(frame: np.ndarray) -> list[float] | None:
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower_red = cv2.inRange(hsv, np.array([0, 100, 70]), np.array([14, 255, 255]))
    upper_red = cv2.inRange(hsv, np.array([165, 100, 70]), np.array([179, 255, 255]))
    mask = cv2.bitwise_or(lower_red, upper_red)
    mask[int(height * 0.44) :, :] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[float, list[float]]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width < 35 or box_width > width * 0.22:
            continue
        if box_height < 8 or box_height > height * 0.11:
            continue
        if box_width / max(box_height, 1) < 1.40:
            continue
        if y + box_height / 2 > height * 0.38:
            continue
        area = cv2.contourArea(contour)
        score = area + box_width * 4 - y * 0.05
        pad_x = box_width * 0.10
        pad_y = box_height * 0.25
        candidates.append(
            (
                score,
                [
                    max(0.0, x - pad_x),
                    max(0.0, y - pad_y),
                    min(float(width), x + box_width + pad_x),
                    min(float(height), y + box_height + pad_y),
                ],
            )
        )
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def choose_yolo_boxes(result: Any) -> list[tuple[int, list[float], float]]:
    people: list[tuple[int, list[float], float]] = []
    balls: list[tuple[int, list[float], float]] = []
    for box in result.boxes:
        source_class = int(box.cls.item())
        confidence = float(box.conf.item())
        xyxy = [float(value) for value in box.xyxy[0].tolist()]
        x1, y1, x2, y2 = xyxy
        box_width = x2 - x1
        box_height = y2 - y1
        if source_class == PERSON_COCO_ID and confidence >= 0.30:
            people.append((0, xyxy, confidence))
        elif source_class == SPORTS_BALL_COCO_ID and confidence >= 0.12:
            aspect = box_width / max(box_height, 1)
            if 10 <= box_width <= 260 and 10 <= box_height <= 260 and 0.45 <= aspect <= 2.2:
                balls.append((1, xyxy, confidence))
    if balls:
        balls = [max(balls, key=lambda item: item[2])]
    return people + balls


def draw_preview(frame: np.ndarray, boxes: list[tuple[int, list[float], float]]) -> np.ndarray:
    colors = {0: (255, 80, 40), 1: (40, 230, 230), 2: (40, 40, 255)}
    preview = frame.copy()
    for class_id, xyxy, confidence in boxes:
        x1, y1, x2, y2 = (int(value) for value in xyxy)
        color = colors[class_id]
        cv2.rectangle(preview, (x1, y1), (x2, y2), color, 3)
        text = f"{CLASS_NAMES[class_id]} {confidence:.2f}" if confidence else CLASS_NAMES[class_id]
        cv2.putText(preview, text, (x1, max(25, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
    return preview


def run(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    output = args.output.resolve()
    preview_dir = args.preview.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if args.start >= args.val_start or args.val_start >= args.end:
        raise ValueError("start < val-start < end 순서여야 합니다.")

    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {source}")
    model = YOLO(args.model)
    counts = {"train": 0, "val": 0, "person": 0, "basketball": 0, "rim": 0}
    preview_count = 0
    timestamp = args.start

    try:
        while timestamp <= args.end:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            height, width = frame.shape[:2]
            split = "train" if timestamp < args.val_start else "val"
            stem = f"sample-b-{timestamp:09.3f}s"

            result = model.predict(
                frame,
                classes=[PERSON_COCO_ID, SPORTS_BALL_COCO_ID],
                conf=0.10,
                imgsz=args.imgsz,
                device="cpu",
                verbose=False,
            )[0]
            boxes = choose_yolo_boxes(result)
            rim = find_rim(frame)
            if rim is not None:
                boxes.append((2, rim, 1.0))

            image_path = output / "images" / split / f"{stem}.jpg"
            label_path = output / "labels" / split / f"{stem}.txt"
            cv2.imwrite(str(image_path), frame)
            labels = [xyxy_to_yolo(class_id, xyxy, width, height) for class_id, xyxy, _ in boxes]
            label_path.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")

            counts[split] += 1
            for class_id, _, _ in boxes:
                counts[CLASS_NAMES[class_id]] += 1
            if preview_count < 30 and (preview_count < 10 or any(item[0] == 1 for item in boxes)):
                cv2.imwrite(str(preview_dir / f"{stem}.jpg"), draw_preview(frame, boxes))
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
        "source": str(source),
        "range": {"start": args.start, "val_start": args.val_start, "end": args.end, "every": args.every},
        "counts": counts,
        "warning": "반자동 초기 표시이므로 정식 모델 전 사람 검토가 필요합니다.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return yaml_path


if __name__ == "__main__":
    run(parse_args())
