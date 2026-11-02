"""Read-only Phase 14 candidate check for a locally available MP4.

This invokes the current YOLO/pose candidate pipeline only. It does not call
OpenAI or write to the database, and it cannot measure made/missed or zones.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.analysis.adaptive_trial import add_focused_candidates
from app.analysis.phase7_pipeline import analyze_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--hoop-x", type=float, required=True, help="rim center X in video pixels")
    parser.add_argument("--hoop-y", type=float, required=True, help="rim center Y in video pixels")
    args = parser.parse_args()

    if not args.video.is_file():
        parser.error("video file does not exist")

    backend = Path(__file__).resolve().parents[1]
    model = backend / "models/basketball-yolo26n-pilot-v4.pt"
    pose = backend / "models/yolo26n-pose.pt"
    started = perf_counter()
    base = analyze_video(
        args.video,
        model,
        [args.hoop_x, args.hoop_y],
        pose_model_path=pose,
        collect_motion_hints=True,
    )
    base_times = [round(item["time_ms"] / 1000, 3) for item in base["candidates"]]
    result = add_focused_candidates(
        args.video, model, [args.hoop_x, args.hoop_y], pose, base
    )
    report = {
        "video": args.video.name,
        "duration_s": result["duration_ms"] / 1000,
        "sample_fps": result["sample_fps"],
        "base_candidate_times_s": base_times,
        "rescan_windows": result["focused_rescan"]["windows"],
        "candidate_times_s": [
            round(item["time_ms"] / 1000, 3) for item in result["candidates"]
        ],
        "elapsed_s": round(perf_counter() - started, 2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
