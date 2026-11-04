from app.analysis.candidate_windows import rescan_window
from app.analysis.adaptive_trial import select_rescan_windows
from app.analysis.candidate_windows import WINDOW_RULES
from app.analysis.shot_gate_trial import OFFSETS_MS, PRIOR_OFFSETS_MS


def test_same_role_rescans_use_fixed_range_and_truncate_only_at_edges():
    assert rescan_window(204933,0,600000) == (202933,208433)
    assert rescan_window(600,0,30000) == (0,4100)
    assert rescan_window(29800,0,30000) == (27800,30000)
    assert rescan_window(204933,204000,208000) == (204000,208000)


def test_pose_hints_share_rescan_policy_and_overlaps_are_unioned_without_repeating_frames():
    result={'range_start_ms':0,'range_end_ms':600000,'candidates':[], 'motion_hints':[
        {'time_ms':204933,'confidence':.62,'scene_id':0},
        {'time_ms':206400,'confidence':.6,'scene_id':0}]}
    assert select_rescan_windows(result) == [(202933,209900)]


def test_stage_rules_have_distinct_anchors_and_match_image_sampling_bounds():
    assert WINDOW_RULES['pose_hint'].anchor != WINDOW_RULES['rejected_candidate'].anchor
    assert rescan_window(5000,0,10000,purpose='plant_refresh') == (3600,5150)
    for name,offsets in [('shot_gate',OFFSETS_MS),('prior_shot_gate',PRIOR_OFFSETS_MS)]:
        rule=WINDOW_RULES[name]
        assert (min(offsets),max(offsets)) == (-rule.before_ms,rule.after_ms)


def test_start_edge_never_duplicates_frame_zero_to_satisfy_evidence_count(monkeypatch):
    import numpy as np
    import pytest
    from app.analysis import shot_gate_trial as gate
    positions=[]
    class Capture:
        def isOpened(self): return True
        def set(self,_,value): positions.append(value)
        def read(self): return True,np.zeros((20,20,3),dtype=np.uint8)
        def release(self): pass
    monkeypatch.setattr(gate.cv2,'VideoCapture',lambda *a:Capture())
    monkeypatch.setattr(gate,'_encode_frame',lambda frame:'test-image')
    frames=gate.shot_gate_frames(None,200)
    assert positions == [0,200,400,700,1100]
    assert sum(x['type']=='input_image' for x in frames)==5
    positions.clear()
    with pytest.raises(ValueError):
        gate.shot_gate_frames(None,0)
    assert positions == [0,200,500,900]


def test_close_candidates_from_different_scenes_are_not_joined_by_rescan_consolidation():
    from app.analysis.phase7_pipeline import merge_candidate_evidence
    first={'time_ms':1000,'next_time_ms':1300,'scene_id':0,'foot_point_px':[100,200],
           'ball_point_px':[100,100],'confidence':.9,'candidate_source':'trajectory'}
    second={**first,'time_ms':1400,'next_time_ms':1600,'scene_id':1,'candidate_source':'rim_approach_backtrack'}
    assert len(merge_candidate_evidence([first,second],1920,1080)) == 2
