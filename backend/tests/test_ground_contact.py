from copy import deepcopy
import numpy as np
from app.services.ground_contact import plant_sample, refresh_retimed_foot_tracks
from app.services.openai_shot_review import _projected_plant_position, _projected_foot_position, _needs_arc_review
from test_detail_parallel_trial import decision


def sample(time, x, y, **extra):
    return {'time_ms': time, 'foot_point_px': [x, y+8], 'ankle_points_px': [[x-5, y-2], [x+5, y]],
            'person_box_px': [x-50, y-300, x+50, y+8], **extra}


def test_airborne_fixed_minus_400_sample_is_replaced_by_stable_pre_takeoff_pair():
    candidate = {'time_ms': 2000, 'foot_track': [sample(1100, 100, 500), sample(1233, 101, 500),
                 sample(1500, 102, 470), sample(1633, 103, 450), sample(1766, 105, 448)]}
    assert plant_sample(candidate)['time_ms'] == 1233
    assert _projected_plant_position([[.1, 0, 0], [0, .05, 0], [0, 0, 1]], candidate) == ((10.1, 25.4), 1233)


def test_only_airborne_or_missing_pose_or_clipped_feet_do_not_become_plant():
    for track in ([sample(1400, 100, 450), sample(1533, 100, 420)],
                  [sample(1400, 100, 450, foot_point_clipped=True), sample(1533, 100, 450)],
                  [{'time_ms':1400, 'foot_point_px':[100,450]}, {'time_ms':1533, 'foot_point_px':[100,450]}]):
        assert plant_sample({'time_ms': 2000, 'foot_track': track}) is None
    assert _projected_foot_position(np.eye(3), {'foot_point_px':[25,20], 'foot_point_source':'person_box_lower_midpoint'}) is None


def test_retimed_release_refreshes_tracks_without_relabelling_old_pixels(monkeypatch):
    original = {'time_ms': 5000, 'scene_id': 4, 'foot_point_px':[100,500], 'foot_point_time_ms':4100,
                'foot_track':[sample(3700,100,500)], 'shot_gate':{'verdict':'shot', 'detector_candidate_time_ms':4100}}
    seen = []
    def scan(*args, **kw):
        seen.append(kw)
        kw['foot_track_targets'][0]['foot_track'] = [sample(4200,100,500),sample(4333,100,500)]
        return {'performance':{'total_seconds':1}}
    monkeypatch.setattr('app.analysis.phase7_pipeline.analyze_video',scan)
    activity = []
    result=refresh_retimed_foot_tracks(None,None,None,None,{'duration_ms':10000,'candidates':[original]},
                                     activity_progress=lambda *event: activity.append(event))
    assert activity == [('발 추적 보정', 0, 1), ('발 추적 보정', 1, 1)]
    assert seen[0]['start_ms'] == 3600 and seen[0]['end_ms'] == 5150
    assert original['foot_point_time_ms'] == 4100
    assert plant_sample(original)['time_ms'] == 4333
    assert result['foot_track_refresh_summary']['refreshed'] == 1


def test_missing_coordinates_trigger_independent_visual_review_without_lowering_confidence():
    primary = decision(court_x=None,court_y=None,court_zone='needs_review',position_confidence=.38)
    before=deepcopy(primary.model_dump())
    assert _needs_arc_review(primary) is True
    assert primary.model_dump() == before
