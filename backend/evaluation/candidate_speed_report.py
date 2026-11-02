"""Save compact evidence from the read-only speed runs."""
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    inputs = root / "work/performance"
    runs = []
    for name in ("torch-180s", "openvino-cpu-180s", "openvino-gpu-180s"):
        data = json.loads((inputs / f"{name}.json").read_text(encoding="utf-8"))
        runs.append({"runtime": name, "seconds": data["wall_seconds"],
                     "candidate_times_ms": [c["time_ms"] for c in data["candidates"]],
                     "performance": data["performance"], "inference_sizes": data["inference_sizes"]})
    before = json.loads((inputs / "shorts-torch.json").read_text(encoding="utf-8"))
    after = json.loads((inputs / "shorts-openvino-cpu.json").read_text(encoding="utf-8"))
    clips = [{"clip": a["clip"], "before_seconds": a["wall_seconds"],
              "after_seconds": b["wall_seconds"], "candidate_times_ms": b["candidate_times_ms"],
              "same_times": a["candidate_times_ms"] == b["candidate_times_ms"],
              "same_foot_points": a["foot_points"] == b["foot_points"]}
             for a, b in zip(before, after)]
    output = root / "evaluation/results/candidate-speed-2026-11-02.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "date": "2026-11-02", "cpu": "Intel Core i7-12700", "gpu": "Intel UHD 770",
        "method": "Same v4/pose FP32 weights and frame sampling. One sequential run per configuration; no DB callbacks.",
        "openai_calls": 0, "input_tokens": 0, "output_tokens": 0,
        "runs_180s": runs, "short_clips": clips,
        "limitation": "Agreement with previous candidates is not a general shot-detection accuracy estimate. No new independent ground truth for the 180-second loop.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
