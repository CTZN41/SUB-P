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


def test_pre_shot_dribble_is_excluded_only_with_completed_nearby_same_player_shot():
    before={'time_ms':475600,'scene_id':0,'candidate_source':'trajectory',
            'candidate_sources':['trajectory','pose_shot_motion'],'foot_point_px':[900,800],'frame_size_px':[1920,1080],
            'shot_gate':{'status':'complete','verdict':'not_shot','confidence':.99},'ai_review':{'is_shot':False}}
    actual={'time_ms':478000,'scene_id':0,'foot_point_px':[950,790],
            'shot_gate':{'verdict':'shot','confidence':.99},'ai_review':{'is_shot':True,'outcome':'made'}}
    result=separate_recovery_motion({'candidates':[deepcopy(before),deepcopy(actual)]})
    assert result['candidates'] == [actual]
    assert result['excluded_non_shot_candidates'][0]['exclusion']['reason'] == 'high_confidence_pre_shot_dribble'
    for change in ({'scene_id':1},{'foot_point_px':[50,50]}, {'time_ms':480000}, {'ai_review':{'is_shot':True,'outcome':'unclear'}}):
        result=separate_recovery_motion({'candidates':[deepcopy(before),{**deepcopy(actual),**change}]})
        assert len(result['candidates'])==2


def test_moving_dribbler_requires_continuous_track_bridge_not_a_larger_distance_limit():
    from app.services.non_shot_recovery import _same_player_motion
    a={'foot_point_px':[788,856],'frame_size_px':[1920,1080], 'foot_track':[
        {'time_ms':475733,'foot_point_px':[739,870]}, {'time_ms':475867,'foot_point_px':[713,889]}]}
    b={'foot_point_px':[445,707],'foot_track':[
        {'time_ms':476267,'foot_point_px':[652,867]}, {'time_ms':476400,'foot_point_px':[609,827]}]}
    assert _same_player_motion(a,b)
    assert not _same_player_motion({**a,'foot_track':[]},b)
    assert not _same_player_motion({**a,'player_track_id':1},{**b,'player_track_id':2})
