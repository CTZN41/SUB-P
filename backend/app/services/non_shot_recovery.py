"""Keep rejected retrieval motion in the audit, rather than counting a second shot.

Never merge two accepted shots or discard an uncertain release/hoop candidate.
"""


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
