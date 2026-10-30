"""Bounded candidate rescans and an independent shot/not-shot check."""

from __future__ import annotations

from pathlib import Path

from app.analysis.adaptive_trial import add_focused_candidates
from app.analysis.phase7_pipeline import Phase7Canceled, analyze_video
from app.analysis.shot_gate_trial import review_prior_shot_existence, review_shot_existence
from app.core.config import Settings


def enrich_candidates(
    video_path: Path, model_path: Path, pose_model_path: Path,
    hoop_point: list[float], result: dict, settings: Settings,
    *, cancelled=None,
) -> dict:
    """Improve candidates while keeping uncertain or rejected ones reviewable."""
    if result.get("motion_hints"):
        try:
            add_focused_candidates(
                video_path, model_path, hoop_point, pose_model_path, result,
                cancelled=cancelled,
            )
        except Phase7Canceled:
            raise
        except Exception:
            result["focused_rescan"] = {"status": "failed", "added_candidates": 0}
    if not (settings.openai_shot_review_enabled and settings.openai_api_key):
        return result

    calls = 0
    reviewed_candidates = 0
    rescans = 0
    usage = {"input_tokens": 0, "output_tokens": 0}
    api_seconds = 0.0

    def count_result(decision: dict) -> None:
        nonlocal api_seconds
        reported = decision.get("usage") or {}
        for key in usage:
            usage[key] += int(reported.get(key, 0) or 0)
        api_seconds += float(decision.get("seconds", 0) or 0)

    max_calls = min(settings.openai_max_candidate_reviews,
                    settings.openai_max_shot_gate_reviews)
    for candidate in result["candidates"][:max_calls]:
        if cancelled and cancelled():
            raise Phase7Canceled("사용자가 분석을 취소했습니다.")
        if calls >= max_calls:
            break
        reviewed_candidates += 1
        time_ms = int(candidate["time_ms"])
        calls += 1
        try:
            gate = review_shot_existence(video_path, time_ms, settings)
        except Exception:
            candidate["shot_gate"] = {"status": "failed"}
            continue
        count_result(gate)
        candidate["shot_gate"] = gate
        if gate.get("verdict") != "not_shot" or gate.get("confidence", 0) < .9:
            continue

        # A narrow window can miss an early or late release. Recheck nearby
        # frames before marking this candidate uncertain; never delete it.
        if rescans >= 4 or calls >= max_calls:
            candidate["shot_gate"]["resolution"] = "needs_review"
            continue
        rescans += 1
        try:
            high = analyze_video(
                video_path, model_path, hoop_point,
                pose_model_path=pose_model_path, sample_fps=30,
                start_ms=max(0, time_ms - 2000),
                end_ms=min(int(result["duration_ms"]), time_ms + 2000),
                cancelled=cancelled,
            )
        except Phase7Canceled:
            raise
        except Exception:
            candidate["shot_gate"]["resolution"] = "rescan_failed"
            continue
        alternatives = sorted(
            (item for item in high["candidates"]
             if abs(int(item["time_ms"]) - time_ms) <= 1800),
            key=lambda item: abs(int(item["time_ms"]) - time_ms),
        )
        for alternative in alternatives[:3]:
            if calls >= max_calls:
                break
            alt_time = int(alternative["time_ms"])
            if abs(alt_time - time_ms) < 150:
                continue
            calls += 1
            try:
                alt_gate = review_shot_existence(video_path, alt_time, settings)
            except Exception:
                continue
            count_result(alt_gate)
            if alt_gate.get("verdict") != "shot" or alt_gate.get("confidence", 0) < .9:
                continue
            if any(
                other is not candidate
                and abs(int(other["time_ms"]) - alt_time) <= 1000
                for other in result["candidates"]
            ):
                continue
            candidate.update(alternative)
            candidate["shot_gate"] = {
                **alt_gate, "resolution": "retimed_after_rescan",
                "original_time_ms": time_ms,
            }
            break
        else:
            candidate["shot_gate"]["resolution"] = "needs_review"
            if calls >= max_calls or time_ms < 3200:
                continue
            calls += 1
            try:
                prior_gate = review_prior_shot_existence(video_path, time_ms, settings)
            except Exception:
                continue
            count_result(prior_gate)
            offset = prior_gate.get("release_offset_ms")
            if (
                prior_gate.get("verdict") != "shot"
                or prior_gate.get("confidence", 0) < .9
                or not isinstance(offset, int)
                or not -3200 <= offset <= -500
            ):
                continue
            release_time = time_ms + offset
            if any(
                other is not candidate
                and int(other.get("scene_id", 0)) == int(candidate.get("scene_id", 0))
                and abs(int(other["time_ms"]) - release_time) <= 1200
                for other in result["candidates"]
            ):
                continue
            candidate["time_ms"] = release_time
            candidate["frame_index"] = round(
                release_time / 1000 * float(result.get("source_fps", 0) or 0)
            )
            candidate["release_basis"] = "ai_prior_window"
            candidate["shot_gate"] = {
                **prior_gate, "resolution": "retimed_from_prior_window",
                "original_time_ms": time_ms,
            }

    result["candidates"].sort(key=lambda item: item["time_ms"])
    result["shot_gate_summary"] = {
        "calls": calls, "rescans": rescans, "call_limit": max_calls,
        "unreviewed_candidates": max(0, len(result["candidates"]) - reviewed_candidates),
        "usage": usage, "response_seconds": round(api_seconds, 2),
    }
    return result
