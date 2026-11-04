from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.sole_contact import (ContactSequence, evaluate_contact_sequence, observe_free_throw_line,
    apply_contact_evidence, needs_contact_verification, prepare_contact_sequence, review_contact_pixels)
from test_midrange_boundary import evidence
from test_detail_parallel_trial import decision


def sample(y=210):
    candidate = {'calibration_view_valid': True, 'validated_court_transform': [[.1, 0, 0], [0, .1, 0], [0, 0, 1]]}
    meta = {'status': 'prepared', 'source_fps': 30, 'sent_frame_indices': [10, 12, 14, 16], 'roi_px': [0, 0, 500, 400],
            'verified_contact_frames': {str(i):{'verified':True,'landmark_displacement_px':0} for i in (10,12,14,16)},
            'line_observations': [{'frame_index': i, 'status': 'observed', 'pixel_error_px': 2., 'observed_y_ft': [19., 19.]}
                                  for i in (10, 12, 14)]}
    seq = ContactSequence.model_validate({'takeoff_frame_index': 16, 'reason': 'synthetic contact', 'frames': [
        {'frame_index': i, 'state': 'both_grounded', 'contacts': [
            {'foot_id': f'foot_{k}', 'x': x, 'y': y, 'error_px': 1, 'confidence': .8} for k, x in ((1, 245), (2, 255))]}
        for i in (10, 12, 14)]+[{'frame_index': 16, 'state': 'airborne', 'contacts': []}]})
    return seq, meta, candidate


@pytest.mark.parametrize('y,zone', [(210, 'mid_center'), (170, 'paint_center')])
def test_multiple_visible_sole_measurements_resolve_one_side_of_free_throw_line(y, zone):
    seq, meta, c = sample(y)
    result = evaluate_contact_sequence(seq, meta, c)
    assert result['status'] == 'resolved' and result['court_zone'] == zone
    assert len(result['measured_frames']) == 3 and result['confidence'] == .8
    assert result['court_x'] == 25 and result['court_y'] == y/10


@pytest.mark.parametrize('weakness', ['straddling', 'low_confidence', 'duplicate', 'made_up_frame', 'airborne',
                                    'takeoff_missing', 'same_foot', 'same_point', 'clipped', 'line_missing',
                                    'invalid_calibration', 'unverified_view', 'far_from_takeoff', 'one_frame', 'unstable'])
def test_incomplete_or_ambiguous_contact_never_confirms_location(weakness):
    seq, meta, c = sample(190 if weakness == 'straddling' else 210)
    if weakness == 'low_confidence':
        for f in seq.frames[:3]: f.contacts[1].confidence = .69
    if weakness == 'duplicate': seq.frames.append(seq.frames[0].model_copy(deep=True))
    if weakness == 'made_up_frame': meta['sent_frame_indices'] = [16]
    if weakness == 'airborne':
        for f in seq.frames[:3]: f.state = 'airborne'
    if weakness == 'takeoff_missing': seq.takeoff_frame_index = None
    if weakness == 'same_foot':
        for f in seq.frames[:3]: f.contacts[1].foot_id = 'foot_1'
    if weakness == 'same_point':
        for f in seq.frames[:3]: f.contacts[1].x = f.contacts[0].x
    if weakness == 'clipped':
        for f in seq.frames[:3]: f.contacts[0].x = 0
    if weakness == 'line_missing': meta['line_observations'] = []
    if weakness == 'invalid_calibration': c['validated_court_transform'] = None
    if weakness == 'unverified_view': c['calibration_view_valid'] = False
    if weakness == 'far_from_takeoff': seq.takeoff_frame_index = 100; meta['sent_frame_indices'].append(100); seq.frames[-1].frame_index = 100
    if weakness == 'one_frame': seq.frames = [seq.frames[0], seq.frames[-1]]
    if weakness == 'unstable':
        for n, f in enumerate(seq.frames[:3]):
            for p in f.contacts: p.x += n*20
    result = evaluate_contact_sequence(seq, meta, c)
    assert result['status'] == 'needs_review'
    if weakness == 'low_confidence':
        assert result['reason'] == 'sole_contact_occluded_or_low_confidence'
        assert len(result['diagnostic_frames']) == 3 and not result['measured_frames']


def test_occluded_line_needs_verified_fixed_view_before_temporal_reuse():
    seq, meta, c = sample()
    meta['line_observations'] = [meta['line_observations'][0], meta['line_observations'][2]]
    assert evaluate_contact_sequence(seq, meta, c)['status'] == 'needs_review'
    meta['verified_line_borrows'] = {'12': {'reference_frame_index': 10, 'landmark_displacement_px': .5}}
    assert evaluate_contact_sequence(seq, meta, c)['status'] == 'resolved'


def test_roi_offsets_are_applied_once_without_using_box_midpoint():
    seq, meta, c = sample()
    meta['roi_px'] = [100, 100, 600, 500]
    for f in seq.frames[:3]:
        for p in f.contacts: p.x -= 100; p.y -= 100
    result = evaluate_contact_sequence(seq, meta, c)
    assert (result['court_x'], result['court_y']) == (25, 21)


def test_actual_white_line_required_and_calibration_guides_search_only():
    c = sample()[2]
    blank = np.full((480, 640, 3), 60, dtype=np.uint8)
    assert observe_free_throw_line(blank, c['validated_court_transform'])['status'] == 'unavailable'
    frame = blank.copy()
    cv2.line(frame, (170, 190), (330, 190), (235, 235, 235), 4)
    line = observe_free_throw_line(frame, c['validated_court_transform'], blocked_box=[240, 100, 260, 210])
    assert line['status'] == 'observed' and line['coverage'] > .8
    assert max(abs(y-19) for y in line['observed_y_ft']) < .1


def test_nearby_white_curve_or_large_disagreement_is_not_trusted_as_free_throw_line():
    c = sample()[2]
    frame = np.full((480, 640, 3), 60, dtype=np.uint8)
    cv2.line(frame, (170, 200), (330, 200), (235, 235, 235), 3)
    result = observe_free_throw_line(frame, c['validated_court_transform'])
    assert result['status'] == 'unavailable'


def test_only_location_changes_and_low_contact_scores_cannot_be_raised():
    seq, meta, c = sample()
    result = evaluate_contact_sequence(seq, meta, c)
    r = decision(court_zone='needs_review', court_x=None, court_y=None, position_confidence=.4).model_dump()
    before = deepcopy(r)
    apply_contact_evidence(r, result)
    assert r['court_zone'] == 'mid_center' and r['position_confidence'] == .8
    assert {k:r[k] for k in ('outcome','outcome_confidence','shot_type','shot_type_confidence','is_shot')} == {k:before[k] for k in ('outcome','outcome_confidence','shot_type','shot_type_confidence','is_shot')}
    apply_contact_evidence(r, {'status': 'needs_review', 'reason': 'unclear'})
    assert r['court_x'] is None and r['position_confidence'] == 0
    with pytest.raises(ValueError): apply_contact_evidence(r, {**result, 'confidence': .69})


def test_contact_trigger_requires_the_correct_area_shot_style_and_validated_evidence():
    d, c, _ = evidence()
    assert needs_contact_verification(d.model_dump(), c, (18.6,20.3))
    assert needs_contact_verification(d.model_dump(), c, (18,30))  # AI XY is still near the line
    far = d.model_dump(); far.update(court_x=12, court_y=30)
    assert not needs_contact_verification(far, c, (12,30))
    for field, value in [('is_shot',False),('shot_type','layup'),('shot_type','dunk'),('visual_arc_relation','outside')]:
        changed = d.model_dump(); changed[field] = value
        assert not needs_contact_verification(changed, c, (18.6,20.3))
    c['calibration_view_valid'] = False
    assert not needs_contact_verification(d.model_dump(), c, (18.6,20.3))


def test_pipeline_accounts_for_contact_usage_and_keeps_type_and_outcome(tmp_path, monkeypatch):
    from app.core.config import Settings
    from app.services import openai_shot_review as review
    from test_openai_shot_review import camera_context
    d, c, shoes = evidence(); d.court_zone = 'mid_wing_left'
    file = tmp_path/'native.mp4'; file.write_bytes(b'test')
    monkeypatch.setattr(review,'_build_shared_camera_contexts',lambda *a:({0:camera_context()},[],0))
    monkeypatch.setattr(review,'candidate_frame_inputs',lambda *a:[])
    monkeypatch.setattr('app.services.review_frame_trial.dense_shoe_inputs',lambda *a:([{'type':'input_image'}],shoes))
    monkeypatch.setattr(review,'_review_once',lambda *a,**k:(d,{'input_tokens':10,'output_tokens':2}))
    seq, meta, candidate = sample()
    resolved = evaluate_contact_sequence(seq,meta,candidate)
    monkeypatch.setattr('app.services.sole_contact.review_contact_pixels',lambda *a:({**resolved,'api_called':True},{'input_tokens':20,'output_tokens':3}))
    result = review.review_candidates_with_openai(file,{'candidates':[c]},Settings(_env_file=None,openai_api_key='test',openai_vision_fallback_model=None),
        client=object(),calibration_transform=c['validated_court_transform'])
    r=result['candidates'][0]['ai_review']
    assert r['court_zone']=='mid_center' and r['court_x']==25
    assert r['outcome']=='made' and r['shot_type']=='set_shot'
    assert [a['task'] for a in r['attempts']]==['shot_review','sole_contact_pixels']
    assert result['ai_review']['usage']=={'input_tokens':30,'output_tokens':5}


def test_no_paid_request_when_the_actual_line_is_not_observed(monkeypatch):
    import app.services.sole_contact as module
    monkeypatch.setattr(module, 'prepare_contact_sequence', lambda *a: ([{'type':'input_image'}],
        {'status':'prepared','line_observations':[]}, {}))
    def forbidden(*a): raise AssertionError('No paid request without a line')
    monkeypatch.setattr(module, 'locate_soles_once', forbidden)
    result, usage = review_contact_pixels(object(), 'test', Path('unused'), {})
    assert result['status']=='needs_review' and not result['api_called'] and usage=={}


def test_wider_pixel_error_sensitivity_can_veto_an_initially_resolved_point(monkeypatch):
    import app.services.sole_contact as module
    seq, meta, c = sample(196)
    assert evaluate_contact_sequence(seq, meta, c)['status']=='resolved'
    monkeypatch.setattr(module, 'prepare_contact_sequence', lambda *a: ([{'type':'input_image'}], meta, {}))
    monkeypatch.setattr(module, 'locate_soles_once', lambda *a: (seq, {'input_tokens':10,'output_tokens':2}))
    result, usage = review_contact_pixels(object(), 'test', Path('unused'), c)
    assert result['status']=='needs_review' and result['reason']=='pixel_error_sensitivity_crosses_boundary'
    assert result['api_called'] and usage['input_tokens']==10


def test_native_frames_are_decoded_at_source_fps_without_resizing_the_shoe_crop(tmp_path, monkeypatch):
    import app.services.sole_contact as module
    video=tmp_path/'native.mp4'
    writer=cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 60, (640,480))
    assert writer.isOpened()
    for _ in range(180): writer.write(np.full((480,640,3),60,dtype=np.uint8))
    writer.release()
    candidate={'time_ms':2000,'calibration_view_valid':True,'validated_court_transform':np.eye(3).tolist(),
               'foot_track':[{'time_ms':t,'foot_point_px':[320,300],'ankle_points_px':[[320,295]],
                              'person_box_px':[290,90,350,300]} for t in (1400,1533)]}
    monkeypatch.setattr(module,'observe_free_throw_line',lambda *a,**k:{'status':'unavailable'})
    images, meta, frames=prepare_contact_sequence(video,candidate)
    assert meta['source_fps']==60 and 35<=len(frames)<=37
    assert meta['source_frame_indices']==list(range(min(frames),max(frames)+1))
    assert meta['anchor_time_ms']==1533 and not meta['native_crop_resized']
    assert all(f.shape==(480,640,3) for f in frames.values())
    assert len(meta['sent_frame_indices'])>=18


def test_visible_line_alone_cannot_grant_permission_to_use_an_unverified_floor():
    seq, meta, c = sample()
    meta.pop('verified_contact_frames')
    result = evaluate_contact_sequence(seq, meta, c)
    assert result['status']=='needs_review' and not result['measured_frames']


def test_fixed_view_guard_rejects_camera_motion_and_checks_floor_patches(monkeypatch):
    from app.services.sole_contact import verify_fixed_floor
    from app.services import view_alignment_trial as alignment
    calls=[]
    monkeypatch.setattr(alignment,'align_view',lambda *a:(np.eye(3),{'holdout_median_px':.5}))
    monkeypatch.setattr(alignment,'validate_floor_alignment',lambda *a:calls.append('floor') or [.9]*4)
    assert verify_fixed_floor(None,None,np.eye(3))['verified'] and calls==['floor']
    warp=np.eye(3);warp[0,2]=3
    monkeypatch.setattr(alignment,'align_view',lambda *a:(warp,{'holdout_median_px':.5}))
    assert not verify_fixed_floor(None,None,np.eye(3))['verified']
