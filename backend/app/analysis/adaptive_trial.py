"""Read-only trial of focused high-fps rescanning for missed shot candidates."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from .phase7_pipeline import analyze_video


def select_rescan_windows(
    result: dict[str, Any], *, max_windows: int = 4,
) -> list[tuple[int, int]]:
    """Rescan strong pose clues that are not already represented by a shot."""
    start = int(result["range_start_ms"])
    end = int(result["range_end_ms"])
    candidates = result.get("candidates", [])
    hints = sorted(result.get("motion_hints", []), key=lambda item: item["time_ms"])
    windows: list[tuple[int, int]] = []
    for hint in hints:
        time_ms = int(hint["time_ms"])
        if hint["confidence"] < .5 or any(
            abs(time_ms - int(candidate["time_ms"])) <= 2500
            and int(hint["scene_id"]) == int(candidate.get("scene_id", 0))
            for candidate in candidates
        ):
            continue
        proposed = (max(start, time_ms - 1800), min(end, time_ms + 1800))
        if proposed[1] <= proposed[0]:
            continue
        if windows and proposed[0] <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], proposed[1]))
        elif len(windows) < max_windows:
            windows.append(proposed)
    return windows


def add_focused_candidates(
    source: Path, model_path: Path, hoop_point: list[float],
    pose_model_path: Path, result: dict[str, Any], *,
    cancelled=None, max_windows: int = 4, rescan_fps: float = 30,
) -> dict[str, Any]:
    """Add only distinct high-fps candidates; preserve every original candidate."""
    windows = select_rescan_windows(result, max_windows=max_windows)
    reports: list[dict[str, Any]] = []
    added = 0
    for start_ms, end_ms in windows:
        high = analyze_video(
            source, model_path, hoop_point, pose_model_path=pose_model_path,
            sample_fps=rescan_fps, start_ms=start_ms, end_ms=end_ms,
            cancelled=cancelled,
        )
        for candidate in high["candidates"]:
            if any(
                abs(int(candidate["time_ms"]) - int(existing["time_ms"])) <= 1800
                and int(candidate.get("scene_id", 0)) == int(existing.get("scene_id", 0))
                for existing in result["candidates"]
            ):
                continue
            candidate["rescan_source"] = "unrepresented_pose_hint"
            result["candidates"].append(candidate)
            added += 1
        reports.append({"window_ms": [start_ms, end_ms], "candidate_count": len(high["candidates"])})
    result["candidates"].sort(key=lambda item: item["time_ms"])
    result["focused_rescan"] = {"windows": reports, "added_candidates": added}
    return result


def run_adaptive_trial(
    source: Path, model_path: Path, hoop_point: list[float], pose_model_path: Path,
    *, start_ms: int = 0, end_ms: int | None = None,
    base_fps: float = 8, rescan_fps: float = 30,
) -> dict[str, Any]:
    """Compare base and focused rescans without changing stored analyses."""
    started = perf_counter()
    base = analyze_video(
        source, model_path, hoop_point, pose_model_path=pose_model_path,
        sample_fps=base_fps, start_ms=start_ms, end_ms=end_ms,
        collect_motion_hints=True,
    )
    base_seconds = perf_counter() - started
    windows = select_rescan_windows(base)
    rescans: list[dict[str, Any]] = []
    novel: list[dict[str, Any]] = []
    for window_start, window_end in windows:
        started = perf_counter()
        high = analyze_video(
            source, model_path, hoop_point, pose_model_path=pose_model_path,
            sample_fps=rescan_fps, start_ms=window_start, end_ms=window_end,
        )
        rescans.append({
            "window_ms": [window_start, window_end],
            "seconds": round(perf_counter() - started, 2),
            "sampled_frames": high["sampled_frames"],
            "candidate_times_ms": [item["time_ms"] for item in high["candidates"]],
        })
        for item in high["candidates"]:
            if all(abs(item["time_ms"] - prior["time_ms"]) > 1800
                   for prior in [*base["candidates"], *novel]):
                novel.append(item)
    return {
        "base": base,
        "base_seconds": round(base_seconds, 2),
        "rescans": rescans,
        "novel_candidates": novel,
    }


def rescan_rejected_times(
    source: Path, model_path: Path, hoop_point: list[float], pose_model_path: Path,
    trial: dict[str, Any], rejected_times_ms: list[int], *, rescan_fps: float = 30,
) -> dict[str, Any]:
    """Check rejected local timestamps at high fps before any exclusion."""
    start = int(trial["base"]["range_start_ms"])
    end = int(trial["base"]["range_end_ms"])
    windows: list[tuple[int, int]] = []
    for time_ms in sorted(rejected_times_ms):
        proposed = (max(start, time_ms - 2000), min(end, time_ms + 2000))
        if proposed[1] <= proposed[0]:
            continue
        if windows and proposed[0] <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], proposed[1]))
        else:
            windows.append(proposed)
    reports = []
    candidates = []
    for window_start, window_end in windows:
        started = perf_counter()
        high = analyze_video(
            source, model_path, hoop_point, pose_model_path=pose_model_path,
            sample_fps=rescan_fps, start_ms=window_start, end_ms=window_end,
        )
        reports.append({
            "window_ms": [window_start, window_end],
            "seconds": round(perf_counter() - started, 2),
            "sampled_frames": high["sampled_frames"],
            "candidate_times_ms": [item["time_ms"] for item in high["candidates"]],
        })
        for item in high["candidates"]:
            if all(abs(item["time_ms"] - prior["time_ms"]) > 1000 for prior in candidates):
                candidates.append(item)
    return {"rescans": reports, "candidates": candidates}
