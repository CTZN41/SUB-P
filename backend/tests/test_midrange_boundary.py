from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.zone_boundary_policy import normalize_zone_boundary as normalize_midrange_boundary, position_conflict_prompt, coordinate_confidence
from app.services import openai_shot_review as review
from test_detail_parallel_trial import decision
from test_openai_shot_review import camera_context,position_decision


def evidence():
    candidate={'time_ms':2000,'calibration_view_valid':True,
        'validated_court_transform':[[1,0,0],[0,1,0],[0,0,1]],
        'foot_track':[{'time_ms':t,'foot_point_px':[18.6,20.3],
                       'ankle_points_px':[[18.6,18.3]],'person_box_px':[10,-80,30,20.3]}
                      for t in (1100,1233)]}
    shoes={'status':'available','offsets_ms':[-900,-767,-550,-400], 'image_edge_offsets_ms':[]}
    d=decision(court_x=18.6,court_y=20.3,court_zone='mid_center',position_confidence=.75)
    d.visual_arc_relation='inside';d.grounded_frame_offsets_ms=[-900,-767]
    d.arc_evidence='Two planted frames show ground contact inside the outer arc.'
    return d,candidate,shoes


@pytest.mark.parametrize('retry_confidence', [.78, .65])
@pytest.mark.parametrize('grounded', [True, False])
@pytest.mark.parametrize('trial', [True, False])
def test_low_coordinate_confidence_gets_one_location_only_retry(tmp_path, monkeypatch, retry_confidence, grounded, trial):
    d, candidate, shoes = evidence()
    d.court_x, d.court_y, d.court_zone = 25, 25, 'mid_center'
    d.position_confidence = d.coordinate_confidence = .63
    d.uncertainty_scope = 'coordinate'
    candidate['foot_track'] = []
    calls = []
    monkeypatch.setattr(review, '_build_shared_camera_contexts', lambda *a: ({0: camera_context()}, [], 0))
    monkeypatch.setattr(review, 'candidate_frame_inputs', lambda *a: [{'type': 'input_text', 'text': 'scene'}])
    monkeypatch.setattr('app.services.review_frame_trial.dense_shoe_inputs',
                        lambda *a: ([{'type': 'input_text', 'text': 'grounded shoes'}], shoes))
    monkeypatch.setattr(review, '_review_once', lambda *a, **k: (d, {'input_tokens': 10, 'output_tokens': 2}))
    def position_retry(*args, **kwargs):
        calls.append(kwargs)
        return review.PositionReviewDecision(
            court_x=25, court_y=25, court_zone='mid_center', confidence=retry_confidence,
            coordinate_confidence=retry_confidence, zone_confidence=.8, uncertainty_scope='coordinate',
            camera_view='left_sideline', overhead_lateral_relation='aligned_with_lane', distance_band='midrange',
            projection_evidence=['grounded shoes and lane'], reason='Visible shoes beyond the free throw line',
            visual_arc_relation='inside', grounded_frame_offsets_ms=[-900, -767] if grounded else [], arc_evidence='Inside the arc'), {
                'input_tokens': 4, 'output_tokens': 1}
    monkeypatch.setattr(review, '_review_position_once', position_retry)
    result = review.review_candidates_with_openai(tmp_path / 'unused.mp4', {'candidates': [candidate]},
        Settings(_env_file=None, openai_api_key='test', openai_vision_fallback_model=None), client=object(),
        coordinate_precision_trial=trial)
    ai = result['candidates'][0]['ai_review']
    assert len(calls) == int(trial)
    assert [a['task'] for a in ai['attempts']] == (['shot_review', 'coordinate_precision'] if trial else ['shot_review'])
    assert ai['position_confidence'] == (retry_confidence if trial and grounded else .63)
    assert result['ai_review']['usage'] == {'input_tokens': 14 if trial else 10, 'output_tokens': 3 if trial else 2}
    assert ai['outcome'] == 'made' and ai['shot_type'] == 'set_shot'


@pytest.mark.parametrize('weakness', ['physical_boundary', 'view', 'missing_images', 'prior_review', 'clipped'])
def test_coordinate_retry_requires_new_supported_evidence_and_no_prior_position_retry(weakness):
    d, candidate, shoes = evidence()
    d.court_x, d.court_y, d.court_zone = 25, 25, 'mid_center'
    d.position_confidence = d.coordinate_confidence = .63
    d.uncertainty_scope = 'coordinate'
    attempts = [{'task': 'shot_review'}]
    if weakness in ('physical_boundary', 'view'): d.uncertainty_scope = weakness
    if weakness == 'missing_images': shoes['status'] = 'unavailable'
    if weakness == 'prior_review': attempts.append({'task': 'position_consistency'})
    if weakness == 'clipped': shoes['image_edge_offsets_ms'] = shoes['offsets_ms']
    assert not review._needs_coordinate_review(d, candidate, shoes, attempts)


@pytest.mark.parametrize('width',[15,30])
def test_supported_adjacent_label_keeps_xy_confidence_outcome_and_type(width):
    d,c,shoes=evidence();before=d.model_dump()
    audit=normalize_midrange_boundary(d,c,(18.583,20.2609),shoes,tolerance_cm=width)
    assert audit['normalized'] and audit['near_boundary']
    assert audit['original_zone']=='mid_center' and audit['coordinate_zone']=='mid_wing_left'
    assert audit['distance_cm']==pytest.approx(12.192)
    expected={**before,'court_zone':'mid_wing_left'}
    assert d.model_dump()==expected
    assert not audit['coordinates_moved']


def test_matching_center_point_on_boundary_keeps_name_and_values():
    d,c,shoes=evidence();d.court_x=19.;d.court_y=20.2;before=d.model_dump()
    audit=normalize_midrange_boundary(d,c,(18.8425,20.2393),shoes)
    assert audit['near_boundary'] and not audit['normalized']
    assert audit['reason']=='already_consistent' and d.model_dump()==before


def test_wider_window_is_not_silently_used_by_default():
    d,c,shoes=evidence();d.court_x=18.3;d.court_y=22
    assert not normalize_midrange_boundary(d,c,(18.3,22),shoes)['normalized']
    assert normalize_midrange_boundary(d,c,(18.3,22),shoes,tolerance_cm=30)['normalized']


@pytest.mark.parametrize('xy,named',[
    ((18.6,18.9),'mid_center'), # inside paint
    ((18.6,19.1),'mid_center'), # tolerance touches the free-throw line
    ((25,8),'mid_center'),     # restricted/paint
    ((18.6,28),'mid_center'),  # close to real 3pt boundary
    ((18.6,29),'mid_center'),  # outside 3pt boundary
    ((3,10),'mid_baseline_left'), # corner 3pt
    ((18.6,22),'mid_wing_right'), # opposite/nonadjacent label
])
@pytest.mark.parametrize('width',[15,30])
def test_physical_boundaries_or_opposite_side_are_never_renamed(xy,named,width):
    d,c,shoes=evidence();d.court_x,d.court_y=xy;d.court_zone=named;before=d.model_dump()
    audit=normalize_midrange_boundary(d,c,xy,shoes,tolerance_cm=width)
    assert not audit['normalized'] and d.model_dump()==before


@pytest.mark.parametrize('weakness',['confidence','view','transform','pose','single_frame','clipped','arc','plant'])
def test_weak_ground_or_calibration_evidence_cannot_bypass_checks(weakness):
    d,c,shoes=evidence();projection=(18.6,20.3)
    if weakness=='confidence':d.position_confidence=.64
    if weakness=='view':c['calibration_view_valid']=False
    if weakness=='transform':c.pop('validated_court_transform')
    if weakness=='pose':c['foot_track']=[]
    if weakness=='single_frame':d.grounded_frame_offsets_ms=[-900]
    if weakness=='clipped':shoes['image_edge_offsets_ms']=shoes['offsets_ms']
    if weakness=='arc':d.visual_arc_relation='touching'
    if weakness=='plant':projection=(35,9)
    before=d.model_dump()
    assert not normalize_midrange_boundary(d,c,projection,shoes)['normalized']
    assert d.model_dump()==before


def test_retry_reports_separate_confidences_without_raising_low_coordinate_score():
    d=position_decision(.75,zone='mid_center',court_x=18.6,court_y=20.3)
    d.coordinate_confidence=.75;d.zone_confidence=.53;d.uncertainty_scope='internal_midrange_boundary'
    assert coordinate_confidence(d)==.75
    d.coordinate_confidence=.53
    assert coordinate_confidence(d)==.53
    first=decision(position_confidence=.9)
    review._apply_position_review(first,d,initial_zone_mismatch=True)
    assert first.position_confidence==.53 and first.review_required
    prompt=position_conflict_prompt(d)
    assert 'x=19ft' in prompt and 'ONLY the virtual mid' in prompt
    d.court_y=18
    assert 'physical paint/restricted/three-point boundaries strictly' in position_conflict_prompt(d)


def test_valid_primary_boundary_is_kept_through_production_review_without_extra_api(monkeypatch):
    d,c,shoes=evidence()
    monkeypatch.setattr(review,'_build_shared_camera_contexts',lambda *a:({0:camera_context()},[],0))
    monkeypatch.setattr(review,'candidate_frame_inputs',lambda *a:[])
    monkeypatch.setattr('app.services.review_frame_trial.dense_shoe_inputs',lambda *a:([{'type':'input_image','image_url':'shoes'}],shoes))
    monkeypatch.setattr(review,'_review_once',lambda *a,**kw:(d,{'input_tokens':1,'output_tokens':1}))
    def no_retry(*a,**kw):raise AssertionError('A supported virtual-boundary label should not cause another request')
    monkeypatch.setattr(review,'_review_position_once',no_retry)
    result=review.review_candidates_with_openai(Path('unused'),{'candidates':[c]},
        Settings(_env_file=None,openai_api_key='test',openai_vision_fallback_model=None),client=object(),
        calibration_transform=c['validated_court_transform'])
    value=result['candidates'][0]['ai_review']
    assert value['court_zone']=='mid_wing_left' and value['court_x']==18.6 and value['position_confidence']==.75
    assert value['zone_boundary']['normalized']
    assert value['model_decisions']['primary']['court_zone']=='mid_center'
    assert [a['task'] for a in value['attempts']]==['shot_review']
    assert value['outcome']=='made' and value['shot_type']=='set_shot'
