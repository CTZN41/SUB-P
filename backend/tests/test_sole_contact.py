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


@pytest.mark.parametrize('reason', ['observed_free_throw_line_missing',
                                   'sole_contact_occluded_or_low_confidence', 'contact_verification_failed'])
def test_inconclusive_extra_measurement_keeps_independently_supported_visual_position(reason):
    d, candidate, shoes = evidence()
    d.court_x, d.court_y, d.court_zone = 25, 22, 'mid_center'
    d.uncertainty_scope = 'coordinate'
    before = d.model_dump()
    result = {'status': 'needs_review', 'reason': reason,
              'diagnostic_frames': [{'side': 'outside'}],
              'wider_error_check': {'diagnostic_sides': ['outside']}}
    apply_contact_evidence(before, result, candidate, shoes)
    assert before == d.model_dump()
    assert result['application'] == 'preserved_visual_position'


@pytest.mark.parametrize('weakness', ['physical_scope', 'view_scope', 'low_confidence', 'straddling',
                                    'opposite_side', 'unverified_view', 'missing_shoes', 'clipped',
                                    'near_boundary', 'physical_error', 'unknown_reason', 'missing_diagnostics',
                                    'wider_straddling', 'wider_missing'])
def test_extra_contact_cannot_preserve_unsupported_or_conflicting_position(weakness):
    d, candidate, shoes = evidence()
    d.court_x, d.court_y, d.court_zone = 25, 22, 'mid_center'
    d.uncertainty_scope = 'coordinate'
    result = {'status': 'needs_review', 'reason': 'sole_contact_occluded_or_low_confidence',
              'diagnostic_frames': [{'side': 'outside'}],
              'wider_error_check': {'diagnostic_sides': ['outside']}}
    if weakness == 'physical_scope': d.uncertainty_scope = 'physical_boundary'
    if weakness == 'view_scope': d.uncertainty_scope = 'view'
    if weakness == 'low_confidence': d.coordinate_confidence = .64
    if weakness == 'straddling': result['diagnostic_frames'].append({'side': 'straddling'})
    if weakness == 'opposite_side': result['diagnostic_frames'][0]['side'] = 'inside'
    if weakness == 'unverified_view': candidate['calibration_view_valid'] = False
    if weakness == 'missing_shoes': shoes['status'] = 'unavailable'
    if weakness == 'clipped': shoes['image_edge_offsets_ms'] = shoes['offsets_ms']
    if weakness == 'near_boundary': d.court_y = 19.5
    if weakness == 'physical_error': result['reason'] = 'pixel_error_sensitivity_crosses_boundary'
    if weakness == 'unknown_reason': result['reason'] = 'unknown'
    if weakness == 'missing_diagnostics': result['diagnostic_frames'] = []
    if weakness == 'wider_straddling': result['wider_error_check']['diagnostic_sides'].append('straddling')
    if weakness == 'wider_missing': result['wider_error_check'] = {}
    value = d.model_dump()
    apply_contact_evidence(value, result, candidate, shoes)
    assert value['court_x'] is None and value['position_confidence'] == 0
    assert result['application'] == 'held_for_review'


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


def test_invalid_measured_contact_is_held_instead_of_treated_as_optional_request_failure(tmp_path, monkeypatch):
    from app.core.config import Settings
    from app.services import openai_shot_review as review
    from test_openai_shot_review import camera_context
    d, candidate, shoes = evidence()
    d.court_x, d.court_y, d.court_zone = 25, 21, 'mid_center'
    d.uncertainty_scope = 'coordinate'
    video = tmp_path / 'native.mp4'
    video.write_bytes(b'test')
    monkeypatch.setattr(review, '_build_shared_camera_contexts', lambda *a: ({0: camera_context()}, [], 0))
    monkeypatch.setattr(review, 'candidate_frame_inputs', lambda *a: [])
    monkeypatch.setattr('app.services.review_frame_trial.dense_shoe_inputs', lambda *a: ([], shoes))
    monkeypatch.setattr(review, '_review_once', lambda *a, **k: (d, {}))
    invalid = {'status': 'resolved', 'confidence': .69, 'court_x': 25, 'court_y': 21, 'court_zone': 'mid_center'}
    monkeypatch.setattr('app.services.sole_contact.review_contact_pixels', lambda *a: (invalid, {}))
    result = review.review_candidates_with_openai(video, {'candidates': [candidate]},
        Settings(_env_file=None, openai_api_key='test', openai_vision_fallback_model=None),
        client=object(), calibration_transform=candidate['validated_court_transform'])
    ai = result['candidates'][0]['ai_review']
    assert ai['court_x'] is None
    assert ai['sole_contact']['reason'] == 'contact_measurement_invalid'
    assert ai['outcome'] == 'made' and ai['shot_type'] == 'set_shot'


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
    assert meta['source_fps']==60 and 45<=len(frames)<=47
    assert meta['source_frame_indices']==list(range(min(frames),max(frames)+1))
    assert meta['anchor_time_ms']==2000 and meta['plant_reference_time_ms']==1533 and not meta['native_crop_resized']
    assert all(f.shape==(480,640,3) for f in frames.values())
    assert len(meta['sent_frame_indices'])>=18


def test_visible_line_alone_cannot_grant_permission_to_use_an_unverified_floor():
    seq, meta, c = sample()
    meta.pop('verified_contact_frames')
    result = evaluate_contact_sequence(seq, meta, c)
    assert result['status']=='needs_review' and not result['measured_frames']


@pytest.mark.parametrize('locator_y,prepared', [(450, True), (479, False)])
def test_contact_preparation_keeps_visible_shoes_when_only_padding_is_clipped(tmp_path, monkeypatch, locator_y, prepared):
    import app.services.sole_contact as module
    video = tmp_path / 'edge.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 30, (640, 480))
    for _ in range(90):
        writer.write(np.full((480, 640, 3), 80, dtype=np.uint8))
    writer.release()
    candidate = {'time_ms': 2000, 'calibration_view_valid': True, 'validated_court_transform': np.eye(3).tolist(),
        'foot_track': [{'time_ms': t, 'foot_point_px': [320, locator_y], 'ankle_points_px': [[310, locator_y-10], [330, locator_y-10]],
                        'person_box_px': [280, 100, 360, locator_y]} for t in (1000, 1133)]}
    monkeypatch.setattr(module, 'verify_fixed_floor', lambda *a: {'verified': True})
    monkeypatch.setattr(module, 'observe_free_throw_line', lambda *a, **k: {'status': 'unavailable'})
    images, meta, _ = prepare_contact_sequence(video, candidate)
    assert (meta['status'] == 'prepared') is prepared
    if prepared:
        assert images and meta['crop_padding_clipped'] and meta['roi_px'][3] == 480
    else:
        assert not images and meta['reason'] == 'sole_crop_clipped'


def test_measured_points_must_still_fit_inside_a_padding_clipped_crop():
    seq, meta, candidate = sample()
    meta['crop_padding_clipped'] = True
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'resolved'
    for frame in seq.frames[:3]:
        frame.contacts[1].y = meta['roi_px'][3]
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'needs_review'


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


@pytest.mark.parametrize('background,line_color,polarity', [(190, 30, 'dark'), (60, 235, 'bright')])
def test_observed_free_throw_line_supports_both_dark_and_bright_paint(background, line_color, polarity):
    candidate = sample()[2]
    frame = np.full((480, 640, 3), background, dtype=np.uint8)
    cv2.line(frame, (170, 190), (330, 190), (line_color,)*3, 4)
    result = observe_free_throw_line(frame, candidate['validated_court_transform'])
    assert result['status'] == 'observed' and result['line_polarity'] == polarity
    assert max(abs(y-19) for y in result['observed_y_ft']) < .1


def test_dark_floor_noise_shadow_or_wrong_line_cannot_supply_a_measured_line():
    candidate = sample()[2]
    for y in (None, 181):
        frame = np.full((480, 640, 3), 60, dtype=np.uint8)
        if y is not None:
            cv2.line(frame, (170, y), (330, y), (10, 10, 10), 3)
        assert observe_free_throw_line(frame, candidate['validated_court_transform'])['status'] == 'unavailable'
    frame = np.full((480, 640, 3), 190, dtype=np.uint8)
    cv2.rectangle(frame, (170, 178), (330, 202), (30, 30, 30), -1)
    assert observe_free_throw_line(frame, candidate['validated_court_transform'])['status'] == 'unavailable'


def test_thin_floorboard_seams_and_flat_bright_floor_are_not_painted_lines():
    candidate = sample()[2]
    frame = np.full((480, 640, 3), 190, dtype=np.uint8)
    cv2.line(frame, (170, 190), (330, 190), (40, 40, 40), 1)
    assert observe_free_throw_line(frame, candidate['validated_court_transform'])['status'] == 'unavailable'


def grounded_sample():
    seq, meta, candidate = sample()
    seq.takeoff_frame_index = None
    seq.frames[-1] = seq.frames[0].model_copy(deep=True, update={'frame_index': 16})
    seq.frames.append(seq.frames[0].model_copy(deep=True, update={'frame_index': 18}))
    meta['sent_frame_indices'].append(18)
    meta['verified_contact_frames']['18'] = {'verified': True, 'landmark_displacement_px': 0}
    meta['line_observations'].extend([{**meta['line_observations'][0], 'frame_index': i} for i in (16, 18)])
    meta['reviewed_shot_type'] = 'set_shot'
    candidate.update(time_ms=533, shot_gate={'status': 'complete', 'verdict': 'shot', 'confidence': .99})
    return seq, meta, candidate


def test_grounded_set_shot_uses_visible_contacts_through_verified_release_without_inventing_takeoff():
    seq, meta, candidate = grounded_sample()
    result = evaluate_contact_sequence(seq, meta, candidate)
    assert result['status'] == 'resolved' and result['contact_mode'] == 'grounded_through_release'
    assert seq.takeoff_frame_index is None and result['court_zone'] == 'mid_center'
    assert result['court_x'] == 25 and result['court_y'] == 21
    assert result['post_release_frames'] and len(result['measured_frames']) >= 3


def test_post_release_contact_checks_never_move_the_saved_pre_release_position():
    seq, meta, candidate = grounded_sample()
    seq.frames.append(seq.frames[-1].model_copy(deep=True, update={'frame_index': 20}))
    meta['sent_frame_indices'].append(20)
    meta['verified_contact_frames']['20'] = {'verified': True, 'landmark_displacement_px': 0}
    meta['line_observations'].append({**meta['line_observations'][0], 'frame_index': 20})
    for frame in seq.frames:
        if frame.frame_index >= 16:
            for contact in frame.contacts: contact.x += 3
    result = evaluate_contact_sequence(seq, meta, candidate)
    assert result['status'] == 'resolved' and result['court_x'] == 25


@pytest.mark.parametrize('weakness', ['no_gate', 'low_gate', 'uncertain_gate', 'wrong_type', 'post_unclear',
                                    'post_missing', 'post_clipped', 'post_low_confidence', 'post_unverified',
                                    'post_airborne', 'post_moving', 'post_boundary'])
def test_grounded_release_cannot_bypass_missing_or_conflicting_contact_evidence(weakness):
    seq, meta, candidate = grounded_sample()
    if weakness == 'no_gate': candidate['shot_gate'] = {}
    if weakness == 'low_gate': candidate['shot_gate']['confidence'] = .89
    if weakness == 'uncertain_gate': candidate['shot_gate']['verdict'] = 'unclear'
    if weakness == 'wrong_type': meta['reviewed_shot_type'] = 'dribble_pull_up'
    if weakness == 'post_unclear': seq.frames[-1].state = 'unclear'
    if weakness == 'post_missing': seq.frames.pop()
    if weakness == 'post_clipped': seq.frames[-1].contacts[0].x = 0
    if weakness == 'post_low_confidence': seq.frames[-1].contacts[0].confidence = .69
    if weakness == 'post_unverified': meta['verified_contact_frames']['18']['verified'] = False
    if weakness == 'post_airborne': seq.frames[-1].state = 'airborne'
    if weakness == 'post_moving':
        for contact in seq.frames[-1].contacts: contact.x += 20
    if weakness == 'post_boundary':
        for contact in seq.frames[-1].contacts: contact.y = 190
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'needs_review'


def test_stationary_shoe_motion_can_preserve_a_reliable_stance_before_heel_raise():
    seq, meta, candidate = grounded_sample()
    seq.stance_motion, seq.stance_motion_confidence = 'stationary', .95
    for frame in seq.frames:
        if frame.frame_index >= 16:
            for contact in frame.contacts: contact.confidence = .6
    result = evaluate_contact_sequence(seq, meta, candidate)
    assert result['status'] == 'resolved' and result['court_x'] == 25
    assert result['contact_mode'] == 'verified_stationary_pre_release_stance'
    assert result['confidence'] == .8
    seq.stance_motion_confidence = .69
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'needs_review'
    seq.stance_motion_confidence, seq.stance_motion = .95, 'stepping'
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'needs_review'


def test_stationary_selection_cannot_choose_away_a_conflicting_boundary_measurement():
    seq, meta, candidate = grounded_sample()
    seq.stance_motion, seq.stance_motion_confidence = 'stationary', .8
    for contact in seq.frames[-1].contacts: contact.confidence = .6
    for contact in seq.frames[1].contacts: contact.y = 170
    result = evaluate_contact_sequence(seq, meta, candidate)
    assert result['status'] == 'needs_review' and result['reason'] == 'stationary_measurements_disagree'
    for contact in seq.frames[1].contacts: contact.y = 190
    result = evaluate_contact_sequence(seq, meta, candidate)
    assert result['status'] == 'needs_review'


def test_stationary_reference_selection_uses_the_same_600ms_window_as_input_preparation():
    seq, meta, candidate = grounded_sample()
    reference = seq.frames[0]
    seq.frames = [reference.model_copy(deep=True, update={'frame_index': i}) for i in (13, 14, 15, 29, 31, 33)]
    for frame in seq.frames[3:]:
        for contact in frame.contacts: contact.confidence = .6
    seq.stance_motion, seq.stance_motion_confidence = 'stationary', .8
    candidate['time_ms'] = 1033
    meta['sent_frame_indices'] = [f.frame_index for f in seq.frames]
    meta['verified_contact_frames'] = {str(i): {'verified': True, 'landmark_displacement_px': 0} for i in meta['sent_frame_indices']}
    line = meta['line_observations'][0]
    meta['line_observations'] = [{**line, 'frame_index': i} for i in meta['sent_frame_indices']]
    result = evaluate_contact_sequence(seq, meta, candidate)
    assert result['status'] == 'resolved' and result['court_y'] == 21
    assert result['measured_frames'][-1]['time_ms'] < candidate['time_ms']-500
    seq.frames[0].frame_index = 12
    meta['sent_frame_indices'][0] = 12
    meta['verified_contact_frames']['12'] = {'verified': True, 'landmark_displacement_px': 0}
    meta['line_observations'][0]['frame_index'] = 12
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'needs_review'


@pytest.mark.parametrize('weakness', ['missing', 'unclear', 'one_foot', 'duplicate_foot', 'unverified'])
def test_stationary_claim_alone_cannot_replace_visible_grounded_shoe_states(weakness):
    seq, meta, candidate = grounded_sample()
    seq.stance_motion, seq.stance_motion_confidence = 'stationary', .95
    for contact in seq.frames[-1].contacts: contact.confidence = .6
    if weakness == 'missing': seq.frames.pop()
    if weakness == 'unclear': seq.frames[-1].state = 'unclear'
    if weakness == 'one_foot': seq.frames[-1].state = 'one_grounded'
    if weakness == 'duplicate_foot': seq.frames[-1].contacts[0].foot_id = 'foot_2'
    if weakness == 'unverified': meta['verified_contact_frames']['18']['verified'] = False
    assert evaluate_contact_sequence(seq, meta, candidate)['status'] == 'needs_review'
