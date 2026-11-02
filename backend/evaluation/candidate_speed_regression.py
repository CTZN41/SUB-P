"""Compare short-clip candidates locally; no OpenAI imports, calls, or DB writes."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from app.analysis.adaptive_trial import add_focused_candidates
from app.analysis.phase7_pipeline import analyze_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--openvino", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    model = root / ("models/basketball-yolo26n-pilot-v4_openvino_model" if args.openvino else "models/basketball-yolo26n-pilot-v4.pt")
    pose = root / ("models/yolo26n-pose_openvino_model" if args.openvino else "models/yolo26n-pose.pt")
    cases = [
        ("indoor27", root / "uploads/26c104ab-2c5e-4032-8a4f-3e2b5ab31964/original.mp4", [1007.56, 205.75]),
        ("outdoor18", root / "uploads/2702f9e4-1ab3-45a5-a84a-a970502bb5e4/original.mp4", [720, 305]),
        ("solo22", root / "uploads/e119ecc1-1ed2-47fa-b0ef-8a984c9aeef2/original.mp4", [1669.95, 149.09]),
        ("mixkit2278", root / "uploads/test-clips/subp-candidate-2278.mp4", [640, 20]),
        ("mixkit44448", root / "uploads/test-clips/subp-candidate-44448.mp4", [250, 150]),
        ("pexels5191594", root / "uploads/test-clips/subp-candidate-5191594.mp4", [600, 140]),
    ]
    reports = []
    for name, source, hoop in cases:
        started = perf_counter()
        result = analyze_video(source, model, hoop, pose_model_path=pose,
                               inference_device=args.device, collect_motion_hints=True)
        base_ms = [item["time_ms"] for item in result["candidates"]]
        add_focused_candidates(source, model, hoop, pose, result, inference_device=args.device)
        reports.append({
            "clip": name, "duration_ms": result["duration_ms"],
            "wall_seconds": round(perf_counter() - started, 4),
            "base_times_ms": base_ms,
            "candidate_times_ms": [item["time_ms"] for item in result["candidates"]],
            "foot_points": [item["foot_point_px"] for item in result["candidates"]],
            "performance": result["performance"],
            "rescan": result["focused_rescan"],
        })
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
