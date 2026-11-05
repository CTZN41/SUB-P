"""Bounded candidate rescans and an independent shot/not-shot check."""

from __future__ import annotations

from pathlib import Path
from math import ceil

from app.analysis.adaptive_trial import add_focused_candidates
from app.analysis.phase7_pipeline import Phase7Canceled, analyze_video
from app.analysis.shot_gate_trial import review_prior_shot_existence, review_shot_existence, review_release_timing, TIMING_OFFSETS_MS
from app.core.config import Settings
from app.analysis.candidate_windows import (rescan_window, RESCAN_BEFORE_MS, RESCAN_AFTER_MS,
    GATE_BEFORE_MS, GATE_AFTER_MS, PRIOR_BEFORE_MS, window_policy)


def enrich_candidates(
    video_path: Path, model_path: Path, pose_model_path: Path,
    hoop_point: list[float], result: dict, settings: Settings,
    *, cancelled=None, inference_device: str = "cpu", prefetch_frames: bool = False,
    model_reuse=None, gate_workers: int = 1, gate_client=None, gate_progress=None,
    activity_progress=None,
) -> dict:
    """Improve candidates while keeping uncertain or rejected ones reviewable."""
    result['candidate_window_policy'] = window_policy()
    if result.get("motion_hints"):
        try:
            add_focused_candidates(
                video_path, model_path, hoop_point, pose_model_path, result,
                cancelled=cancelled,
                inference_device=inference_device,
                prefetch_frames=prefetch_frames, model_reuse=model_reuse,
                **({'activity_progress': activity_progress} if activity_progress else {}),
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

    def apply_release_offset(candidate: dict, gate: dict, reference_time: int, basis: str) -> None:
        nonlocal calls
        if (gate.get('status') == 'complete' and gate.get('verdict') == 'shot' and gate.get('confidence', 0) >= .9
                and 'release_offset_ms' in gate and gate['release_offset_ms'] is None
                and (gate.get('window') or {}).get('purpose') == 'shot_gate'
                and 'timing_review' not in gate and calls < max_calls):
            if cancelled and cancelled():
                raise Phase7Canceled('사용자가 분석을 취소했습니다.')
            calls += 1
            if activity_progress:
                activity_progress('추가 슛 검증', 0, 1)
            try:
                timing = review_release_timing(video_path, reference_time, settings,
                    scene_start_ms=candidate.get('scene_start_ms', result.get('range_start_ms', 0)) or 0,
                    scene_end_ms=candidate.get('scene_end_ms', result['duration_ms']),
                    **({'client': gate_client} if gate_client is not None else {}))
                count_result(timing)
                gate['timing_review'] = timing
                if (timing.get('status') == 'complete' and timing.get('verdict') == 'shot'
                        and timing.get('confidence', 0) >= .9 and type(timing.get('release_offset_ms')) is int
                        and timing['release_offset_ms'] in TIMING_OFFSETS_MS
                        and timing['release_offset_ms'] in (timing.get('window') or {}).get('used_offsets_ms', [])):
                    gate['original_release_offset_ms'] = None
                    gate['release_offset_ms'] = timing['release_offset_ms']
                    gate['release_timing_source'] = 'dense_timing_review'
            except Exception as error:
                gate['timing_review'] = {'status': 'failed', 'error_type': type(error).__name__}
            if activity_progress:
                activity_progress('추가 슛 검증', 1, 1)
        offset = gate.get("release_offset_ms")
        if (
            gate.get("verdict") != "shot" or gate.get("confidence", 0) < .9
            or not isinstance(offset, int) or isinstance(offset, bool)
            or not -GATE_BEFORE_MS <= offset <= GATE_AFTER_MS or abs(offset) < 200
        ):
            return
        release_time = reference_time + offset
        if not 0 <= release_time <= int(result["duration_ms"]):
            return
        if any(
            other is not candidate
            and int(other.get("scene_id", 0)) == int(candidate.get("scene_id", 0))
            and abs(int(other["time_ms"]) - release_time) <= 1000
            for other in result["candidates"]
        ):
            return
        # Changing the release timestamp does not retime the detected pixels.
        candidate.setdefault('foot_point_time_ms', candidate.get('time_ms', reference_time))
        candidate["time_ms"] = release_time
        candidate["frame_index"] = round(
            release_time / 1000 * float(result.get("source_fps", 0) or 0)
        )
        candidate["next_time_ms"] = max(
            release_time, int(candidate.get("next_time_ms", release_time))
        )
        candidate["release_basis"] = basis
        candidate["shot_gate"]["detector_candidate_time_ms"] = reference_time

    if gate_workers not in (1, 2):
        raise ValueError("trial gate workers must be one or two")
    max_calls = settings.openai_max_shot_gate_reviews
    primary_results = None
    if gate_workers == 2:
        from app.services.ordered_review_trial import bounded_ordered_map
        from threading import Lock
        progress_lock, completed = Lock(), [0]
        items = result["candidates"][:min(settings.openai_max_candidate_reviews,max_calls)]
        if gate_progress:
            gate_progress(0, len(items))
        def initial_gate(candidate):
            if cancelled and cancelled():
                raise Phase7Canceled("trial canceled")
            try:
                value = review_shot_existence(video_path,int(candidate["time_ms"]),settings,client=gate_client)
            except Exception:
                value = {"status":"failed"}
            with progress_lock:
                completed[0] += 1
                if gate_progress:
                    gate_progress(completed[0],len(items))
            return value
        primary_results = bounded_ordered_map(initial_gate,items,workers=2)
        calls = len(primary_results)
    max_rescans = min(48, max(4, len(result["candidates"]) // 8,
                              ceil(int(result["duration_ms"])/60000)*2))
    rescan_total = min(max_rescans, sum(
        gate.get('verdict') == 'not_shot' and gate.get('confidence', 0) >= .9
        for gate in primary_results)) if primary_results is not None else None
    for candidate_index, candidate in enumerate(result["candidates"][:settings.openai_max_candidate_reviews]):
        if cancelled and cancelled():
            raise Phase7Canceled("사용자가 분석을 취소했습니다.")
        if primary_results is None and calls >= max_calls:
            break
        if primary_results is not None and candidate_index >= len(primary_results):
            break
        reviewed_candidates += 1
        time_ms = int(candidate["time_ms"])
        if primary_results is not None:
            gate = primary_results[candidate_index]
            if gate.get("status") == "failed":
                candidate["shot_gate"] = gate
                continue
        else:
            calls += 1
            try:
                gate = review_shot_existence(video_path, time_ms, settings, **({"client":gate_client} if gate_client is not None else {}))
            except Exception:
                candidate["shot_gate"] = {"status": "failed"}
                continue
        count_result(gate)
        candidate["shot_gate"] = gate
        apply_release_offset(candidate, gate, time_ms, "ai_shot_gate_window")
        if gate.get("verdict") != "not_shot" or gate.get("confidence", 0) < .9:
            continue

        # A narrow window can miss an early or late release. Recheck nearby
        # frames before marking this candidate uncertain; never delete it.
        if rescans >= max_rescans or calls >= max_calls:
            candidate["shot_gate"]["resolution"] = "needs_review"
            continue
        rescans += 1
        if activity_progress:
            activity_progress('추가 후보 탐지', rescans - 1, rescan_total)
        rescan_start, rescan_end = rescan_window(time_ms, 0, int(result['duration_ms']))
        try:
            high = analyze_video(
                video_path, model_path, hoop_point,
                pose_model_path=pose_model_path, sample_fps=30,
                start_ms=rescan_start,
                end_ms=rescan_end,
                cancelled=cancelled,
                inference_device=inference_device,
                prefetch_frames=prefetch_frames, model_reuse=model_reuse,
            )
        except Phase7Canceled:
            raise
        except Exception:
            candidate["shot_gate"]["resolution"] = "rescan_failed"
            continue
        alternatives = sorted(
            (item for item in high["candidates"]
             if -RESCAN_BEFORE_MS <= int(item["time_ms"]) - time_ms <= RESCAN_AFTER_MS),
            key=lambda item: abs(int(item["time_ms"]) - time_ms),
        )
        if activity_progress:
            activity_progress('추가 후보 탐지', rescans, rescan_total)
        for alternative_index, alternative in enumerate(alternatives[:3]):
            if calls >= max_calls:
                break
            alt_time = int(alternative["time_ms"])
            if abs(alt_time - time_ms) < 150:
                continue
            calls += 1
            if activity_progress:
                activity_progress('추가 슛 검증', alternative_index, min(3, len(alternatives)))
            try:
                alt_gate = review_shot_existence(video_path, alt_time, settings, **({"client":gate_client} if gate_client is not None else {}))
            except Exception:
                continue
            finally:
                if activity_progress:
                    activity_progress('추가 슛 검증', alternative_index + 1, min(3, len(alternatives)))
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
            apply_release_offset(candidate, alt_gate, alt_time, "ai_rescan_window")
            break
        else:
            candidate["shot_gate"]["resolution"] = "needs_review"
            if calls >= max_calls or time_ms < PRIOR_BEFORE_MS:
                continue
            calls += 1
            if activity_progress:
                activity_progress('추가 슛 검증', 0, 1)
            try:
                prior_gate = review_prior_shot_existence(video_path, time_ms, settings, **({"client":gate_client} if gate_client is not None else {}))
            except Exception:
                continue
            finally:
                if activity_progress:
                    activity_progress('추가 슛 검증', 1, 1)
            count_result(prior_gate)
            offset = prior_gate.get("release_offset_ms")
            if (
                prior_gate.get("verdict") != "shot"
                or prior_gate.get("confidence", 0) < .9
                or not isinstance(offset, int)
                or not -PRIOR_BEFORE_MS <= offset <= -500
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

    from app.services.ground_contact import refresh_retimed_foot_tracks
    refresh_retimed_foot_tracks(video_path, model_path, pose_model_path, hoop_point, result,
                               cancelled=cancelled, inference_device=inference_device,
                               prefetch_frames=prefetch_frames, model_reuse=model_reuse,
                               **({'activity_progress': activity_progress} if activity_progress else {}))
    result["candidates"].sort(key=lambda item: item["time_ms"])
    result["shot_gate_summary"] = {
        "calls": calls, "rescans": rescans, "call_limit": max_calls,
        "unreviewed_candidates": max(0, len(result["candidates"]) - reviewed_candidates),
        "usage": usage, "response_seconds": round(api_seconds, 2),
    }
    return result
