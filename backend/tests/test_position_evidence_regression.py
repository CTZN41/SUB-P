from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import view_alignment_trial as views
from app.services import openai_shot_review as review
from app.services.review_frame_trial import dense_shoe_inputs, position_only_inputs, PLANT_OFFSETS_MS
from test_detail_parallel_trial import decision
from test_openai_shot_review import position_decision, camera_context


class Frames:
    def __init__(self, frames):
        self.frames = frames

    def set(self, _, value):
        self.time = value

    def read(self):
        frame = self.frames.get(self.time)
        return frame is not None, frame


def test_temporal_floor_recovers_occlusion_with_multiple_current_frames(monkeypatch):
    clean = np.random.default_rng(93).integers(0, 256, (360, 640, 3), dtype=np.uint8)
    reference, current = clean.copy(), clean.copy()
    reference[280:340, 70:130] = 0  # Reference player covers point 4.
    current[220:280, 510:570] = 0  # Current player covers point 2.
    points = [[100, 250], [540, 250], [540, 310], [100, 310]]
    with pytest.raises(ValueError):
        views.validate_floor_alignment(reference, current, np.eye(3), points)
    frames = {t: clean for t in (3800, 4400, 5600, 6200, 9400, 10600)}
    monkeypatch.setattr(views, 'align_view', lambda *a: (np.eye(3), {}))
    result = views.temporal_floor_alignment(Frames(frames), reference, current,
                                            np.eye(3), points, 5000, 10000)
    assert sum(n >= 2 for n in result['support_counts']) >= 3


def test_temporal_floor_rejects_background_only_and_single_current_support(monkeypatch):
    reference = np.random.default_rng(14).integers(0, 256, (360, 640, 3), dtype=np.uint8)
    wrong_floor = reference.copy()
    wrong_floor[200:] = 0
    points = [[100, 250], [540, 250], [540, 310], [100, 310]]
    monkeypatch.setattr(views, 'align_view', lambda *a: (np.eye(3), {}))
    frames = {t: wrong_floor for t in (3800, 4400, 5600, 6200, 9400, 10600)}
    with pytest.raises(ValueError, match='temporal floor'):
        views.temporal_floor_alignment(Frames(frames), reference, wrong_floor,
                                        np.eye(3), points, 5000, 10000)
    # One lucky frame cannot authorize a transform without temporal support.
    with pytest.raises(ValueError, match='temporal floor'):
        views.temporal_floor_alignment(Frames({}), reference, reference,
                                        np.eye(3), points, 5000, 10000)


def test_numeric_conflict_cannot_silently_turn_a_three_into_a_two():
    first = decision(court_zone='three_wing_left', court_x=12, court_y=25)
    retry = position_decision(.7, zone='mid_wing_left', court_x=12, court_y=25)
    review._apply_position_review(first, retry, initial_zone_mismatch=True)
    assert first.court_x is None and first.court_zone == 'needs_review'
    assert first.outcome == 'made' and first.shot_type == 'set_shot'


def test_arc_conflict_requires_visual_numeric_agreement_and_grounded_evidence():
    first = decision(court_zone='three_wing_left', court_x=12, court_y=25)
    retry = position_decision(.85, zone='three_wing_left', court_x=11, court_y=26)
    retry.visual_arc_relation = 'outside'
    retry.grounded_frame_offsets_ms = [-550, -400]
    retry.arc_evidence = 'Both grounded shoes visibly behind the arc in two frames.'
    review._apply_position_review(first, retry, initial_zone_mismatch=True)
    assert first.court_zone == 'three_wing_left' and first.court_x == 11
    inconsistent = retry.model_copy(update={'court_x': 12, 'court_y': 25})
    review._apply_position_review(first, inconsistent, initial_zone_mismatch=True)
    assert first.court_zone == 'needs_review'


def test_two_point_reversal_requires_two_distinct_pre_release_frames():
    for offsets, expected in [([-400, -400], 'needs_review'), ([-550, -400], 'mid_wing_left')]:
        first = decision(court_zone='three_wing_left', court_x=12, court_y=25)
        retry = position_decision(.8, zone='mid_wing_left', court_x=12, court_y=25)
        retry.visual_arc_relation = 'touching'
        retry.arc_evidence = 'Visible planted shoe contacts the arc.'
        retry.grounded_frame_offsets_ms = offsets
        review._apply_position_review(first, retry, initial_zone_mismatch=True)
        assert first.court_zone == expected


def test_dense_shoes_keep_original_pixels_and_do_not_fabricate_clipped_contact(tmp_path):
    video = tmp_path / 'shoes.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 20, (640, 360))
    assert writer.isOpened()
    for _ in range(60):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.line(frame, (0, 330), (639, 330), (255, 255, 255), 4)
        writer.write(frame)
    writer.release()
    candidate = {'time_ms': 1500, 'foot_point_px': [320, 360],
                 'foot_track': [{'time_ms': 1100, 'foot_point_px': [320, 360], 'foot_point_clipped': True}]}
    before = deepcopy(candidate)
    images, evidence = dense_shoe_inputs(video, candidate)
    assert evidence['offsets_ms'] == list(PLANT_OFFSETS_MS)
    assert len([x for x in images if x['type'] == 'input_image']) == 7
    assert evidence['image_edge_offsets_ms'] == list(PLANT_OFFSETS_MS)
    assert candidate == before and not evidence['numeric_proxy_replaced']
    overview = [{'type': 'input_text', 'text': 'full frame t=-600ms'},
                {'type': 'input_image', 'image_url': 'overview'}]
    compact = position_only_inputs(overview, images)
    assert compact[:2] == overview and len(compact) == len(images) + 2


def test_geometry_prompt_supplies_actual_numeric_boundary():
    prompt = review._court_geometry_prompt()
    assert '23.75' in prompt and 'FIRST report visual_arc_relation' in prompt


def test_crop_padding_at_image_border_is_not_marked_as_a_clipped_shoe(tmp_path):
    video = tmp_path / 'visible-shoes.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 20, (640, 360))
    for _ in range(60):
        writer.write(np.full((360, 640, 3), 100, dtype=np.uint8))
    writer.release()
    _, evidence = dense_shoe_inputs(video, {'time_ms': 1500, 'foot_point_px': [320, 335]})
    assert evidence['crop_padding_edge_offsets_ms'] == list(PLANT_OFFSETS_MS)
    assert evidence['image_edge_offsets_ms'] == []


@pytest.mark.parametrize('offsets,expected', [
    ([-1067, -1000], True), ([-1200, -1000], True),
    ([-1201, -1000], False), ([-1000, 0], False), ([-1000, -1000], False),
])
def test_grounded_arc_uses_the_same_reference_window_as_shoe_input(offsets, expected):
    value = review.VisualArcDecision(visual_arc_relation='outside', confidence=.96,
        grounded_frame_offsets_ms=offsets, arc_evidence='Two visible planted shoes behind arc')
    assert review._grounded_arc_evidence(value) is expected


def test_t3_reference_frame_is_accepted_through_the_normal_review_pipeline(monkeypatch):
    from app.core.config import Settings
    from app.services import review_frame_trial as frames
    monkeypatch.setattr(review, '_build_shared_camera_contexts', lambda *a: ({0: camera_context()}, [], 0))
    monkeypatch.setattr(review, 'candidate_frame_inputs', lambda *a: [])
    monkeypatch.setattr(frames, 'dense_shoe_inputs', lambda *a: (
        [{'type': 'input_image', 'image_url': 'shoes'}],
        {'status': 'available', 'offsets_ms': [-1067, -1000], 'image_edge_offsets_ms': []}))
    monkeypatch.setattr(review, '_review_once', lambda *a, **k: (
        decision(court_zone='three_wing_left', court_x=11, court_y=26, position_confidence=.75), {}))
    visual = review.VisualArcDecision(visual_arc_relation='outside', confidence=.96,
        grounded_frame_offsets_ms=[-1067, -1000], arc_evidence='Visible soles on the far side of arc')
    monkeypatch.setattr(review, '_review_visual_arc_once', lambda *a: (visual, {}))
    result = review.review_candidates_with_openai(Path('unused'), {'candidates': [{'time_ms': 4400}]},
        Settings(_env_file=None, openai_api_key='test', openai_vision_fallback_model=None), client=object())
    value = result['candidates'][0]['ai_review']
    assert value['court_x'] == 11 and value['court_zone'] == 'three_wing_left'
    assert [a['task'] for a in value['attempts']] == ['shot_review', 'visual_arc']


def test_floor_quality_distinguishes_missing_patches_from_conflicting_pixels():
    frame = np.random.default_rng(8).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    frame[200:300, 250:350] = 100
    points = [[100, 250], [300, 250], [540, 340], [540, 250]]
    quality = views.floor_landmark_quality(frame, points)
    assert [p['reason'] for p in quality] == ['usable', 'low_texture', 'image_edge', 'usable']
    with pytest.raises(views.FloorAlignmentError) as caught:
        views.validate_floor_alignment(frame, frame, np.eye(3), points)
    assert caught.value.evidence['reason'] == 'insufficient_usable_landmarks'
    wrong = np.zeros_like(frame)
    with pytest.raises(views.FloorAlignmentError) as caught:
        views.validate_floor_alignment(frame, wrong, np.eye(3), [[100, 200], [540, 200], [540, 300], [100, 300]])
    assert caught.value.evidence['reason'] == 'floor_landmark_mismatch'


def test_temporal_failure_keeps_the_point_support_counts(monkeypatch):
    frame = np.random.default_rng(8).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    frame[200:300, 250:350] = 100
    points = [[100, 250], [300, 250], [540, 340], [540, 250]]
    monkeypatch.setattr(views, 'align_view', lambda *a: (np.eye(3), {}))
    frames = {t: frame for t in (3800, 4400, 5600, 6200, 9400, 10600)}
    with pytest.raises(views.FloorAlignmentError) as caught:
        views.temporal_floor_alignment(Frames(frames), frame, frame, np.eye(3), points, 5000, 10000)
    assert caught.value.evidence['reason'] == 'insufficient_usable_landmarks'
    assert caught.value.evidence['support_counts'] == [3, 0, 0, 3]


def test_retimed_release_does_not_reuse_old_frame_pixels_as_release_feet():
    candidate = {'time_ms': 1500, 'foot_point_px': [25, 30],
                 'foot_point_time_ms': 1000, 'foot_track': [
                     {'time_ms': 1100, 'foot_point_px': [24, 28]}]}
    assert review._projected_foot_position(np.eye(3), candidate) is None
    assert review._projected_plant_position(np.eye(3), candidate) == (None, None)
    legacy = {'time_ms': 1500, 'foot_point_px': [25, 30],
              'shot_gate': {'detector_candidate_time_ms': 1000}}
    assert review._projected_foot_position(np.eye(3), legacy) is None


@pytest.mark.parametrize('visual_relation,retry_relation,expected', [
    ('unclear', 'outside', 'needs_review'),
    ('outside', 'touching', 'needs_review'),
    ('outside', 'outside', 'three_wing_left'),
])
@pytest.mark.parametrize('visual_confidence', [.69, .70, .9])
def test_independent_visual_gate_cannot_be_overridden_by_old_numbers(
        monkeypatch, visual_relation, retry_relation, expected, visual_confidence):
    from app.core.config import Settings
    from app.services import review_frame_trial as frames
    monkeypatch.setattr(review, '_build_shared_camera_contexts', lambda *a: ({0: camera_context()}, [], 0))
    monkeypatch.setattr(review, 'candidate_frame_inputs', lambda *a: [])
    monkeypatch.setattr(frames, 'dense_shoe_inputs', lambda *a: (
        [{'type': 'input_text', 'text': 'shoes -550ms -400ms'},
         {'type': 'input_image', 'image_url': 'image-only'}],
        {'offsets_ms': [-550, -400]}))
    monkeypatch.setattr(review, '_review_once', lambda *a, **kw: (
        decision(court_zone='three_wing_left', court_x=12, court_y=25, position_confidence=.75),
        {'input_tokens': 1, 'output_tokens': 1}))
    visual = review.VisualArcDecision(visual_arc_relation=visual_relation, confidence=visual_confidence,
        grounded_frame_offsets_ms=[-550, -400], arc_evidence='Shoes behind arc')
    monkeypatch.setattr(review, '_review_visual_arc_once', lambda *a: (visual, {'input_tokens': 1, 'output_tokens': 1}))
    def retry(*a, **kw):
        assert visual_relation != 'unclear'  # Unresolved visual gate skips paid coordinate retry.
        p = position_decision(.85, zone='three_wing_left', court_x=11, court_y=26)
        p.visual_arc_relation = retry_relation
        p.grounded_frame_offsets_ms = [-550, -400]
        p.arc_evidence = 'Shoes behind arc'
        return p, {'input_tokens': 1, 'output_tokens': 1}
    monkeypatch.setattr(review, '_review_position_once', retry)
    result = review.review_candidates_with_openai(Path('unused'), {'candidates': [{'time_ms': 2000}]},
        Settings(_env_file=None, openai_api_key='test', openai_vision_fallback_model=None), client=object())
    a = result['candidates'][0]['ai_review']
    assert a['court_zone'] == ('needs_review' if visual_confidence < .70 else expected), a
    assert a['outcome'] == 'made' and a['shot_type'] == 'set_shot'


@pytest.mark.parametrize('visual_relation,expected', [('outside','three_wing_left'),('unclear','needs_review')])
def test_coordinate_missing_primary_actually_enters_image_only_review(monkeypatch,visual_relation,expected):
    from app.core.config import Settings
    from app.services import review_frame_trial as frames
    monkeypatch.setattr(review,'_build_shared_camera_contexts',lambda *a:({0:camera_context()},[],0))
    monkeypatch.setattr(review,'candidate_frame_inputs',lambda *a:[])
    monkeypatch.setattr(frames,'dense_shoe_inputs',lambda *a:(
        [{'type':'input_image','image_url':'visible-shoes'}],{'offsets_ms':[-700,-550]}))
    monkeypatch.setattr(review,'_review_once',lambda *a,**kw:(
        decision(court_zone='needs_review',court_x=None,court_y=None,position_confidence=.38),{}))
    calls=[]
    def visual(*a):
        calls.append('visual')
        return review.VisualArcDecision(visual_arc_relation=visual_relation,confidence=.9,
            grounded_frame_offsets_ms=[-700,-550],arc_evidence='Two grounded frames show shoes beyond arc'),{}
    def coordinate(*a,**kw):
        calls.append('position')
        p=position_decision(.85,zone='three_wing_left',court_x=11,court_y=26)
        p.visual_arc_relation='outside';p.grounded_frame_offsets_ms=[-700,-550];p.arc_evidence='Grounded shoes beyond arc'
        return p,{}
    monkeypatch.setattr(review,'_review_visual_arc_once',visual)
    monkeypatch.setattr(review,'_review_position_once',coordinate)
    result=review.review_candidates_with_openai(Path('unused'),{'candidates':[{'time_ms':2000}]},
        Settings(_env_file=None,openai_api_key='test',openai_vision_fallback_model=None),client=object())
    assert result['candidates'][0]['ai_review']['court_zone']==expected
    assert calls == (['visual','position'] if visual_relation=='outside' else ['visual'])
