"""YOLO 탐지 결과에서 슛 릴리스 후보 시각을 찾는 초기 규칙 기반 도구."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PERSON_CLASS_ID = 0
SPORTS_BALL_CLASS_ID = 32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="슛 릴리스 후보 생성")
    parser.add_argument("result", type=Path, help="yolo_baseline.py가 만든 result.json")
    parser.add_argument("--max-lookahead", type=float, default=0.25)
    parser.add_argument("--min-rise-pixels", type=float, default=60.0)
    parser.add_argument("--cooldown", type=float, default=2.0)
    return parser.parse_args()


def best_detection(record: dict[str, Any], class_id: int) -> dict[str, Any] | None:
    detections = [item for item in record["detections"] if item["class_id"] == class_id]
    return max(detections, key=lambda item: item["confidence"], default=None)


def ball_is_near_release_area(ball: dict[str, Any], person: dict[str, Any]) -> bool:
    ball_x, ball_y = ball["center"]
    x1, y1, x2, y2 = person["xyxy"]
    width = x2 - x1
    height = y2 - y1
    return (
        x1 - width * 0.20 <= ball_x <= x2 + width * 0.20
        and y1 - height * 0.25 <= ball_y <= y1 + height * 0.35
    )


def find_candidates(
    records: list[dict[str, Any]],
    max_lookahead: float,
    min_rise_pixels: float,
    cooldown: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        ball = best_detection(record, SPORTS_BALL_CLASS_ID)
        person = best_detection(record, PERSON_CLASS_ID)
        if ball is None or person is None or not ball_is_near_release_area(ball, person):
            continue

        current_seconds = float(record["seconds"])
        current_y = float(ball["center"][1])
        person_top = float(person["xyxy"][1])

        for next_record in records[index + 1 :]:
            elapsed = float(next_record["seconds"]) - current_seconds
            if elapsed > max_lookahead:
                break
            next_ball = best_detection(next_record, SPORTS_BALL_CLASS_ID)
            if next_ball is None:
                continue

            next_y = float(next_ball["center"][1])
            rise_pixels = current_y - next_y
            if rise_pixels < min_rise_pixels or next_y >= person_top:
                continue
            if candidates and current_seconds - candidates[-1]["seconds"] < cooldown:
                break

            confidence = min(float(ball["confidence"]), float(next_ball["confidence"]))
            candidates.append(
                {
                    "seconds": current_seconds,
                    "next_seconds": float(next_record["seconds"]),
                    "rise_pixels": round(rise_pixels, 2),
                    "confidence": round(confidence, 4),
                    "reason": "공이 선수의 상체 근처에서 손 위 방향으로 빠르게 분리됨",
                }
            )
            break

    return candidates


def run(args: argparse.Namespace) -> Path:
    result_path = args.result.resolve()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    candidates = find_candidates(
        payload["records"],
        max_lookahead=args.max_lookahead,
        min_rise_pixels=args.min_rise_pixels,
        cooldown=args.cooldown,
    )
    output = {
        "source_result": str(result_path),
        "method": "initial-rule-based-release-candidate",
        "notice": "자동 확정이 아니라 사람이 검토해야 하는 초기 후보입니다.",
        "candidates": candidates,
    }
    output_path = result_path.with_name("release-candidates.json")
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output_path


if __name__ == "__main__":
    run(parse_args())
