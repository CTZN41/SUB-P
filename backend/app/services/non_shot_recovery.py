"""Keep rejected retrieval motion in the audit, rather than counting a second shot.

Never merge two accepted shots or discard an uncertain release/hoop candidate.
"""
from math import dist, hypot


def _same_player_motion(first: dict, second: dict) -> bool:
    if first.get('player_track_id') is not None and second.get('player_track_id') is not None:
        if first['player_track_id'] != second['player_track_id']:
            return False
    a, b, size = first.get('foot_point_px'), second.get('foot_point_px'), first.get('frame_size_px')
    if not a or not b or not size:
        return False
    diagonal = hypot(*size)
    if dist(a,b) <= diagonal * .15:
        return True
    # A dribbler can move substantially before release. Require a temporal
    # bridge rather than increasing the static distance threshold for everyone.
    tail = sorted((s for s in first.get('foot_track',[]) if not s.get('foot_point_clipped')),key=lambda s:s['time_ms'])[-3:]
    head = sorted((s for s in second.get('foot_track',[]) if not s.get('foot_point_clipped')),key=lambda s:s['time_ms'])[:3]
    if len(tail)<2 or len(head)<2:
        return False
    left,right=tail[-1],head[0]
    gap=right['time_ms']-left['time_ms']
    if not 0<gap<=600:
        return False
    movement=dist(left['foot_point_px'],right['foot_point_px'])
    return movement <= diagonal*.05 and movement/(gap/1000) <= diagonal*.25


def separate_recovery_motion(result: dict) -> dict:
    candidates = sorted(result.get("candidates", []), key=lambda item: item["time_ms"])
    kept, excluded = [], list(result.get("excluded_non_shot_candidates", []))
    for candidate in candidates:
        gate = candidate.get("shot_gate") or {}
        review = candidate.get("ai_review") or {}
        sources = set(candidate.get("candidate_sources") or [])
        rejected_retrieval = (
            gate.get("status") == "complete"
            and gate.get("verdict") == "not_shot"
            and gate.get("confidence", 0) >= .98
            and review.get("is_shot") is False
            and candidate.get("candidate_source") == "trajectory"
            and "rim_approach_backtrack" not in sources
            and candidate.get("release_basis") not in {"ai_shot_gate_window", "ai_rescan_window", "ai_prior_window"}
        )
        previous = next((other for other in reversed(kept)
                         if int(other.get("scene_id", 0)) == int(candidate.get("scene_id", 0))
                         and 0 < candidate["time_ms"] - other["time_ms"] <= 6000), None)
        following = next((other for other in candidates
                          if int(other.get('scene_id', 0)) == int(candidate.get('scene_id', 0))
                          and 800 <= other['time_ms'] - candidate['time_ms'] <= 3200
                          and (other.get('shot_gate') or {}).get('verdict') == 'shot'
                          and (other.get('shot_gate') or {}).get('confidence', 0) >= .9
                          and (other.get('ai_review') or {}).get('is_shot') is True
                          and (other.get('ai_review') or {}).get('outcome') in {'made', 'missed'}), None)
        if rejected_retrieval and following is not None:
            if _same_player_motion(candidate, following):
                candidate['exclusion'] = {'reason': 'high_confidence_pre_shot_dribble',
                                          'related_shot_time_ms': following['time_ms']}
                excluded.append(candidate)
                continue
        if rejected_retrieval and previous is not None:
            previous_gate = previous.get("shot_gate") or {}
            previous_review = previous.get("ai_review") or {}
            gap = candidate["time_ms"] - previous["time_ms"]
            if (4000 <= gap <= 6000 and previous_gate.get("verdict") == "shot"
                    and previous_gate.get("confidence", 0) >= .9
                    and previous_review.get("is_shot") is True
                    and previous_review.get("outcome") in {"made", "missed"}):
                candidate["exclusion"] = {
                    "reason": "high_confidence_non_shot_after_completed_attempt",
                    "related_shot_time_ms": previous["time_ms"],
                }
                excluded.append(candidate)
                continue
        kept.append(candidate)
    result["candidates"] = kept
    result["excluded_non_shot_candidates"] = excluded
    result["non_shot_recovery_summary"] = {"excluded_count": len(excluded), "retained_count": len(kept)}
    return result
