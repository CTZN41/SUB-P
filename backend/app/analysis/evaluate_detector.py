"""학습된 농구 탐지 모델을 영상의 지정 구간에서 재시험한다."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ultralytics import YOLO
import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLO 농구 탐지 모델 영상 재시험")
    parser.add_argument("source", type=Path, help="시험할 MP4 파일")
    parser.add_argument("--model", type=Path, required=True, help="시험할 학습 모델")
    parser.add_argument("--start", type=float, default=0.0, help="시작 시각(초)")
    parser.add_argument("--end", type=float, required=True, help="종료 시각(초)")
    parser.add_argument("--sample-fps", type=float, default=4.0, help="초당 검사 장수")
    parser.add_argument("--imgsz", type=int, default=640, help="모델 입력 크기")
    parser.add_argument("--conf", type=float, default=0.15, help="최소 신뢰도")
    parser.add_argument("--output", type=Path, default=Path("work/model-tests"))
    parser.add_argument("--max-images", type=int, default=20)
    return parser.parse_args()


def run(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    model_path = args.model.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"영상을 찾을 수 없습니다: {source}")
    if not model_path.is_file():
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {model_path}")
    if args.sample_fps <= 0 or args.end <= args.start:
        raise ValueError("검사 시간과 sample-fps를 확인해 주세요.")

    model = YOLO(str(model_path))
    names = {int(key): str(value) for key, value in model.names.items()}
    class_ids = {name: class_id for class_id, name in names.items()}
    wanted = {name: class_ids[name] for name in ("person", "basketball", "rim") if name in class_ids}
    if not wanted:
        raise ValueError(f"필요한 클래스가 없습니다: {names}")

    output_dir = args.output.resolve() / f"{source.stem}-{model_path.stem}-{args.start:g}-{args.end:g}"
    image_dir = output_dir / "detections"
    image_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {source}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / source_fps if source_fps else 0.0
    end_seconds = min(args.end, duration)
    step = max(1, round(source_fps / args.sample_fps))
    current_frame = int(args.start * source_fps)
    end_frame = int(end_seconds * source_fps)
    per_class_frames = {name: 0 for name in wanted}
    per_class_boxes = {name: 0 for name in wanted}
    sampled_frames = 0
    saved_images = 0
    inference_seconds = 0.0
    records: list[dict[str, object]] = []

    try:
        while current_frame <= end_frame:
            capture.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
            ok, frame = capture.read()
            if not ok:
                break

            started = time.perf_counter()
            result = model.predict(
                frame,
                classes=list(wanted.values()),
                conf=args.conf,
                imgsz=args.imgsz,
                device="cpu",
                verbose=False,
            )[0]
            inference_seconds += time.perf_counter() - started
            sampled_frames += 1

            counts = {name: 0 for name in wanted}
            for box in result.boxes:
                class_id = int(box.cls.item())
                class_name = names[class_id]
                if class_name in counts:
                    counts[class_name] += 1
            for name, count in counts.items():
                per_class_boxes[name] += count
                if count:
                    per_class_frames[name] += 1

            timestamp = current_frame / source_fps
            records.append({"seconds": round(timestamp, 3), "counts": counts})
            if any(counts.values()) and saved_images < args.max_images:
                cv2.imwrite(str(image_dir / f"{timestamp:08.3f}s.jpg"), result.plot())
                saved_images += 1
            current_frame += step
    finally:
        capture.release()

    summary = {
        "source": str(source),
        "model": str(model_path),
        "classes": names,
        "range": {"start": args.start, "end": end_seconds},
        "settings": {"sample_fps": args.sample_fps, "imgsz": args.imgsz, "confidence": args.conf},
        "summary": {
            "sampled_frames": sampled_frames,
            "frames_with_class": per_class_frames,
            "frame_rates": {
                name: round(count / sampled_frames, 4) if sampled_frames else 0
                for name, count in per_class_frames.items()
            },
            "detected_boxes": per_class_boxes,
            "average_inference_ms": round(inference_seconds / sampled_frames * 1000, 2)
            if sampled_frames
            else None,
        },
        "records": records,
    }
    output_path = output_dir / "result.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["summary"], ensure_ascii=False, indent=2))
    print(f"결과 파일: {output_path}")
    return output_path


if __name__ == "__main__":
    run(parse_args())
