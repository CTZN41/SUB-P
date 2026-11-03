from pathlib import Path
import cv2
import numpy as np
import pytest
from app.services import view_alignment_trial as views
from app.services import openai_shot_review as review
from app.core.config import Settings
from test_quality_recovery_trials import decision
from app.services.court_calibration import arc_has_crossing, three_point_trace_side

def test_arc_allows_straight_corner_sections_but_rejects_crossings():
    assert not arc_has_crossing([[0,0],[0,10],[0,20],[20,30],[40,20],[40,10],[40,0]])
    assert arc_has_crossing([[0,0],[40,20],[0,20],[40,0],[20,-10]])


def test_partial_arc_never_joins_ends_or_extrapolates():
    trace = [[5,25],[15,29],[25,30],[35,29],[45,25]]
    matrix = np.eye(3).tolist()
    assert three_point_trace_side(trace, [25,20], matrix) == 'inside'
    assert three_point_trace_side(trace, [25,40], matrix) == 'outside'
    assert three_point_trace_side(trace, [25,30], matrix) == 'boundary'
    assert three_point_trace_side(trace, [50,5.25], matrix) is None
    # Artificial closing chord would classify this point incorrectly.
    assert three_point_trace_side(trace, [25,27], matrix, boundary_px=1) == 'inside'
    assert three_point_trace_side(trace[::-1], [25,40], matrix) == 'outside'


def test_partial_arc_is_projective_and_ambiguous_trace_is_skipped():
    from app.services.court_calibration import transform_point
    image_h = [[10,1,100],[0,8,200],[.002,.001,1]]
    trace = [[5,25],[15,29],[25,30],[35,29],[45,25]]
    image_trace = [transform_point(image_h, p) for p in trace]
    court_h = np.linalg.inv(np.asarray(image_h)).tolist()
    assert three_point_trace_side(image_trace, transform_point(image_h,[25,40]),court_h) == 'outside'
    assert three_point_trace_side(image_trace, transform_point(image_h,[50,5.25]),court_h) is None
    assert three_point_trace_side([[5,25],[45,25],[45,35],[5,35]], [25,40], np.eye(3)) is None

def test_floor_check_rejects_background_match():
    a=np.random.default_rng(23).integers(0,256,(360,640,3),dtype=np.uint8)
    points=[[100,250],[540,250],[540,310],[100,310]]
    assert len(views.validate_floor_alignment(a,a,np.eye(3),points)) == 4
    b=a.copy();b[200:]=0
    with pytest.raises(ValueError,match='floor landmarks'):
        views.validate_floor_alignment(a,b,np.eye(3),points)

def test_group_applies_transform_and_chooses_manual_profile(monkeypatch):
    class Capture:
        def isOpened(self):return True
        def set(self,key,value):self.time=value
        def read(self):return True,np.full((80,80,3), int(self.time),np.uint8)
        def release(self):pass
    monkeypatch.setattr(cv2,'VideoCapture',lambda _:Capture())
    h=np.array([[1.,0,5],[0,1,0],[0,0,1]])
    def align(a,b):
        if int(a[0,0,0]) != int(b[0,0,0]):raise ValueError('different')
        return h,{}
    monkeypatch.setattr(views,'align_view',align)
    monkeypatch.setattr(views,'validate_floor_alignment',lambda *args:None)
    identity=np.eye(3).tolist()
    profiles=[{'frame_time_ms':0,'transform':identity,'image_points':[[10,10]]*4}, {'frame_time_ms':20,'transform':identity,'image_points':[[10,10]]*4}]
    candidates=[{'time_ms':320},{'time_ms':340}]
    transforms,reports=views.group_camera_views(Path('unused'), candidates,dict(profiles[0],views=profiles))
    assert reports[0]['view_id']==1 and reports[0]['old_calibration_usable']
    assert candidates[0]['validated_court_transform'][0][2] == -5
    assert not candidates[1]['calibration_view_valid']

@pytest.mark.parametrize('candidate,expected_reason',[
    ({'calibration_view_valid':False},'기준점'),
    ({'validated_three_point_line':[[5,25],[15,29],[25,30],[35,29],[45,25]],'foot_point_px':[25,30]},'3점선'),
])
def test_no_confidence_bypass_of_missing_view_or_arc(monkeypatch,candidate,expected_reason):
    context=review.CameraContextDecision(camera_view='right_sideline',confidence=.9,court_left_direction_on_screen='left',toward_half_court_direction_on_screen='down',orientation_summary='fixed',visible_landmarks=['paint'],reason='test')
    monkeypatch.setattr(review,'_build_shared_camera_contexts',lambda *args:({0:context},[],0))
    monkeypatch.setattr(review,'candidate_frame_inputs',lambda *args:[])
    monkeypatch.setattr(review,'_review_once',lambda *args,**kw:(decision(position_confidence=.99),{'input_tokens':1,'output_tokens':1}))
    result=review.review_candidates_with_openai(Path('unused'),{'candidates':[dict(time_ms=1000,**candidate)]},Settings(_env_file=None,openai_api_key='test',openai_vision_fallback_model=None),client=object(),calibration_transform=np.eye(3).tolist())
    a=result['candidates'][0]['ai_review']
    assert a['court_x'] is None and expected_reason in a['position_reason']
    assert a['outcome']=='made' and a['shot_type']=='set_shot'


def test_clipped_plant_cannot_silently_validate_the_manual_arc(monkeypatch):
    context=review.CameraContextDecision(camera_view='right_sideline',confidence=.9,court_left_direction_on_screen='left',toward_half_court_direction_on_screen='down',orientation_summary='fixed',visible_landmarks=['paint'],reason='test')
    monkeypatch.setattr(review,'_build_shared_camera_contexts',lambda *args:({0:context},[],0))
    monkeypatch.setattr(review,'candidate_frame_inputs',lambda *args:[])
    monkeypatch.setattr(review,'_review_once',lambda *args,**kw:(decision(position_confidence=.99),{'input_tokens':1,'output_tokens':1}))
    c={'time_ms':1000,'frame_size_px':[100,100], 'foot_point_px':[25,30],
       'validated_three_point_line':[[5,25],[15,29],[25,30],[35,29],[45,25]],
       'foot_track':[{'time_ms':600,'foot_point_px':[25,100]}]}
    result=review.review_candidates_with_openai(Path('unused'),{'candidates':[c]},Settings(_env_file=None,openai_api_key='test',openai_vision_fallback_model=None),client=object(),calibration_transform=np.eye(3).tolist())
    a=result['candidates'][0]['ai_review']
    assert a['plant_sample_time_ms'] is None
    assert a['three_point_line_check']['status']=='insufficient_foot_evidence'
    assert a['input_window_ms'][0] == -4000
    assert a['outcome']=='made' and a['shot_type']=='set_shot'
