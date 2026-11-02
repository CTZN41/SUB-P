"""Read-only candidate speed/consistency trial; never calls OpenAI or writes DB."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from app.analysis.phase7_pipeline import analyze_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--model", type=Path, default=Path("models/basketball-yolo26n-pilot-v4.pt"))
    parser.add_argument("--pose", type=Path, default=Path("models/yolo26n-pose.pt"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--hoop-x", type=float, required=True)
    parser.add_argument("--hoop-y", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    last_report = [-1]
    def progress(time_ms: int) -> None:
        bucket = time_ms // 30000
        if bucket > last_report[0]:
            print(f"video_seconds={time_ms / 1000:.1f}", flush=True)
            last_report[0] = bucket
    started = perf_counter()
    result = analyze_video(
        args.video, args.model, [args.hoop_x, args.hoop_y],
        pose_model_path=args.pose, inference_device=args.device,
        end_ms=round(args.seconds * 1000), collect_motion_hints=True,
        progress=progress,
    )
    result["wall_seconds"] = round(perf_counter() - started, 4)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "wall_seconds": result["wall_seconds"],
        "candidate_times_ms": [c["time_ms"] for c in result["candidates"]],
        "performance": result["performance"],
        "inference_sizes": result["inference_sizes"],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
