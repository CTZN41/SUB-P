"""영상에서 학습·검토용 대표 프레임을 일정 간격으로 추출한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="영상 대표 프레임 추출")
    parser.add_argument("source", type=Path, help="원본 MP4 파일")
    parser.add_argument("--start", type=float, default=0.0, help="시작 시각(초)")
    parser.add_argument("--end", type=float, default=None, help="종료 시각(초)")
    parser.add_argument("--every", type=float, default=1.0, help="이미지를 저장할 간격(초)")
    parser.add_argument("--output", type=Path, default=Path("work/extracted-frames"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"영상을 찾을 수 없습니다: {source}")
    if args.every <= 0:
        raise ValueError("every는 0보다 커야 합니다.")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {source}")

    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps else 0.0
    end = min(args.end if args.end is not None else duration, duration)
    if args.start < 0 or args.start >= end:
        raise ValueError("시작·종료 시각을 확인해 주세요.")

    output_dir = args.output.resolve() / source.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, object]] = []
    timestamp = args.start

    try:
        while timestamp <= end:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            image_path = output_dir / f"{timestamp:09.3f}s.jpg"
            if not cv2.imwrite(str(image_path), frame):
                raise RuntimeError(f"이미지를 저장할 수 없습니다: {image_path}")
            saved.append({"seconds": round(timestamp, 3), "file": image_path.name})
            timestamp += args.every
    finally:
        capture.release()

    manifest = {
        "source": str(source),
        "video_fps": round(fps, 3),
        "duration_seconds": round(duration, 3),
        "range": {"start": args.start, "end": end, "every": args.every},
        "saved_count": len(saved),
        "frames": saved,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"saved_count": len(saved), "output": str(output_dir)}, ensure_ascii=False))
    return manifest_path


if __name__ == "__main__":
    run(parse_args())
