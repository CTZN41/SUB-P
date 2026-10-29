"""Exercise the same rule-first, temporal-fallback court suggestion used by the API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.analysis.build_court_keypoint_dataset import frame_at
from app.services.court_landmark_detection import (
    detect_court_landmarks,
    detect_court_landmarks_temporally,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--rim-model", type=Path, required=True)
    parser.add_argument("--pose-model", type=Path, action="append", required=True)
    parser.add_argument("--second", type=float, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    reports = []
    for second in args.second:
        center = frame_at(args.video, second)
        rule = detect_court_landmarks(center, args.rim_model)
        frames = [
            frame_at(args.video, max(0.0, second + offset))
            for offset in (-.75, -.5, -.25, 0.0, .25, .5, .75)
        ]
        temporal = {
            model.name: detect_court_landmarks_temporally(frames, model)
            for model in args.pose_model
        }
        chosen = rule
        if not rule["usable"]:
            chosen = next((result for result in temporal.values() if result["usable"]), rule)
        reports.append({"second": second, "rule": rule, "temporal": temporal, "chosen": chosen})
    text = json.dumps(reports, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
