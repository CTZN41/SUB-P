"""YOLO26 기본 모델로 농구 영상의 사람·공 탐지 가능성을 점검한다.

이 도구는 학습이나 최종 슛 판정을 하지 않는다. 일정 간격으로 영상을
확인해 COCO 기본 모델이 사람과 sports ball을 얼마나 찾는지 기록한다.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

# Windows에서는 OpenCV가 먼저 DLL을 올리면 일부 PyTorch 버전의 c10.dll
# 초기화가 실패할 수 있으므로 PyTorch를 사용하는 YOLO를 먼저 불러온다.
from ultralytics import YOLO
import cv2


PERSON_CLASS_ID = 0
SPORTS_BALL_CLASS_ID = 32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="농구 영상 YOLO26 기본 탐지 시험")
    parser.add_argument("source", type=Path, help="분석할 MP4 파일")
    parser.add_argument("--start", type=float, default=0.0, help="시작 시각(초)")
    parser.add_argument("--end", type=float, default=None, help="종료 시각(초)")
    parser.add_argument("--sample-fps", type=float, default=4.0, help="초당 검사 프레임 수")
    parser.add_argument("--imgsz", type=int, default=960, help="YOLO 입력 이미지 크기")
    parser.add_argument("--conf", type=float, default=0.10, help="최소 탐지 신뢰도")
    parser.add_argument("--model", default="models/yolo26n.pt", help="Ultralytics 모델 이름 또는 경로")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/yolo-baseline"),
        help="JSON과 확인용 이미지를 저장할 폴더",
    )
    parser.add_argument("--max-images", type=int, default=30, help="저장할 공 탐지 이미지 최대 수")
    return parser.parse_args()


def box_to_dict(box: Any, names: dict[int, str]) -> dict[str, Any]:
    class_id = int(box.cls.item())
    x1, y1, x2, y2 = (round(float(value), 2) for value in box.xyxy[0].tolist())
    return {
        "class_id": class_id,
        "class_name": names[class_id],
        "confidence": round(float(box.conf.item()), 4),
        "xyxy": [x1, y1, x2, y2],
        "center": [round((x1 + x2) / 2, 2), round((y1 + y2) / 2, 2)],
    }


def run(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"영상을 찾을 수 없습니다: {source}")
    if args.sample_fps <= 0:
        raise ValueError("sample-fps는 0보다 커야 합니다.")

    output_root = args.output.resolve() / source.stem
    image_dir = output_root / "ball-detections"
    image_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {source}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / source_fps if source_fps else 0.0
    end_seconds = min(args.end if args.end is not None else duration, duration)
    if args.start < 0 or args.start >= end_seconds:
        raise ValueError("시작·종료 시각을 확인해 주세요.")

    capture.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    sampling_interval = max(1, round(source_fps / args.sample_fps))
    next_frame_to_sample = int(args.start * source_fps)
    capture.set(cv2.CAP_PROP_POS_FRAMES, next_frame_to_sample)

    model = YOLO(args.model)
    names = {int(key): value for key, value in model.names.items()}
    records: list[dict[str, Any]] = []
    inference_seconds = 0.0
    saved_images = 0
    current_frame = next_frame_to_sample

    try:
        while current_frame / source_fps <= end_seconds:
            capture.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
            ok, frame = capture.read()
            if not ok:
                break

            started = time.perf_counter()
            result = model.predict(
                frame,
                classes=[PERSON_CLASS_ID, SPORTS_BALL_CLASS_ID],
                conf=args.conf,
                imgsz=args.imgsz,
                device="cpu",
                verbose=False,
            )[0]
            inference_seconds += time.perf_counter() - started

            detections = [box_to_dict(box, names) for box in result.boxes]
            timestamp = current_frame / source_fps
            record = {
                "frame": current_frame,
                "seconds": round(timestamp, 3),
                "people": sum(item["class_id"] == PERSON_CLASS_ID for item in detections),
                "balls": sum(item["class_id"] == SPORTS_BALL_CLASS_ID for item in detections),
                "detections": detections,
            }
            records.append(record)

            if record["balls"] and saved_images < args.max_images:
                image_path = image_dir / f"{timestamp:08.3f}s.jpg"
                cv2.imwrite(str(image_path), result.plot())
                saved_images += 1

            current_frame += sampling_interval
    finally:
        capture.release()

    sampled_frames = len(records)
    people_frames = sum(record["people"] > 0 for record in records)
    ball_frames = sum(record["balls"] > 0 for record in records)
    summary = {
        "source": str(source),
        "model": args.model,
        "video": {
            "width": width,
            "height": height,
            "fps": round(source_fps, 3),
            "frames": frame_count,
            "duration_seconds": round(duration, 3),
        },
        "range": {"start": args.start, "end": end_seconds},
        "settings": {
            "sample_fps": args.sample_fps,
            "actual_sample_fps": round(source_fps / sampling_interval, 3),
            "imgsz": args.imgsz,
            "confidence": args.conf,
        },
        "summary": {
            "sampled_frames": sampled_frames,
            "frames_with_person": people_frames,
            "person_frame_rate": round(people_frames / sampled_frames, 4) if sampled_frames else 0,
            "frames_with_ball": ball_frames,
            "ball_frame_rate": round(ball_frames / sampled_frames, 4) if sampled_frames else 0,
            "saved_ball_images": saved_images,
            "average_inference_ms": round(inference_seconds / sampled_frames * 1000, 2)
            if sampled_frames
            else None,
        },
        "records": records,
    }

    output_path = output_root / "result.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["summary"], ensure_ascii=False, indent=2))
    print(f"결과 파일: {output_path}")
    return output_path


if __name__ == "__main__":
    run(parse_args())
