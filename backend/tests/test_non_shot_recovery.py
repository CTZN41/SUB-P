from copy import deepcopy
import pytest

from app.services.non_shot_recovery import separate_recovery_motion


def examples():
    return {"candidates": [
        {"time_ms": 57444, "scene_id": 0, "shot_gate": {"verdict": "shot", "confidence": .99},
         "ai_review": {"is_shot": True, "outcome": "missed"}},
        {"time_ms": 62195, "scene_id": 0, "candidate_source": "trajectory",
         "candidate_sources": ["pose_shot_motion", "trajectory"],
         "shot_gate": {"status": "complete", "verdict": "not_shot", "confidence": .98},
         "ai_review": {"is_shot": False}},
    ]}


def test_rejected_retrieval_preserves_full_audit_and_is_idempotent():
    result = examples()
    rejected = deepcopy(result["candidates"][1])
    separate_recovery_motion(result)
    assert [c["time_ms"] for c in result["candidates"]] == [57444]
    assert result["excluded_non_shot_candidates"][0]["shot_gate"] == rejected["shot_gate"]
    assert result["excluded_non_shot_candidates"][0]["exclusion"]["related_shot_time_ms"] == 57444
    separate_recovery_motion(result)
    assert len(result["excluded_non_shot_candidates"]) == 1


@pytest.mark.parametrize("change", [
    {"time_ms": 60000}, {"time_ms": 64000}, {"scene_id": 1},
    {"candidate_source": "pose_shot_motion"},
    {"candidate_sources": ["rim_approach_backtrack"]},
    {"release_basis": "ai_rescan_window"},
    {"shot_gate": {"status": "complete", "verdict": "not_shot", "confidence": .97}},
    {"shot_gate": {"status": "complete", "verdict": "shot", "confidence": .99}},
    {"ai_review": {"is_shot": True, "outcome": "made"}},
])
def test_real_or_ambiguous_second_attempts_are_preserved(change):
    result = examples()
    result["candidates"][1].update(change)
    assert len(separate_recovery_motion(result)["candidates"]) == 2
