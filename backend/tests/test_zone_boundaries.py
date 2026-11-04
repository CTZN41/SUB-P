"""All-zone topology, policy guards, and durable dummy-data checks."""
from itertools import combinations
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services import openai_shot_review as review
from app.services.court_zones import classify_zone, ZoneCode
from app.services.zone_boundaries import zone_boundaries, physical_clearance
from app.services.zone_boundary_policy import normalize_zone_boundary, position_conflict_prompt
from scripts.verify_zone_boundaries import dummy_cases, make_decision, synthetic_evidence, run_verification
from test_openai_shot_review import camera_context


def virtual_examples():
    examples = {}
    for case in dummy_cases():
        if case['physical_source']:
            continue
        x, y = case['xy']
        decision = make_decision(x, y, case['named_zone'])
        candidate, shoes = synthetic_evidence(x, y, 0)
        if normalize_zone_boundary(decision, candidate, (x, y), shoes)['normalized']:
            examples.setdefault(tuple(case['pair']), case)
    return list(examples.values())


EXAMPLES = virtual_examples()


def test_every_shared_drawing_edge_is_in_analytic_boundary_catalogue():
    shapely = pytest.importorskip('shapely')
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from scripts.generate_court_geometry import build_geometry
    zones = {z: unary_union([Polygon(rings[0], rings[1:]) for rings in polygons])
             for z, polygons in build_geometry()['zones'].items()}
    drawing_pairs = {tuple(sorted((a, b))) for a, b in combinations(zones, 2)
                     if zones[a].boundary.intersection(zones[b].boundary).length > 1e-6}
    analytic_pairs = {b.zones for b in zone_boundaries()}
    assert drawing_pairs == analytic_pairs
    assert len(analytic_pairs) == 29
    assert {z for b in zone_boundaries() for z in b.zones} == {z.value for z in ZoneCode}
    assert len({b.zones for b in zone_boundaries() if not b.physical}) == len(EXAMPLES) == 10


@pytest.mark.parametrize('boundary', zone_boundaries())
def test_exact_analytic_piece_has_expected_zones_on_both_sides(boundary):
    for fraction in (.01, .37, .5, .73, .99):
        x, y = boundary.point(fraction)
        nx, ny = boundary.normal(fraction)
        sides = {classify_zone(x+sign*nx*1e-5, y+sign*ny*1e-5).value for sign in (-1, 1)}
        assert sides == set(boundary.zones)
        assert boundary.distance(x, y) < 1e-12


@pytest.mark.parametrize('case', EXAMPLES, ids=lambda c: '/'.join(c['pair']))
@pytest.mark.parametrize('weakness', ['confidence', 'calibration', 'pose', 'single_ground', 'clipped', 'arc', 'projection', 'not_shot', 'physical_scope', 'coordinate_scope', 'view_scope'])
def test_every_virtual_boundary_requires_the_same_evidence(case, weakness):
    x, y = case['xy']
    d = make_decision(x, y, case['named_zone'])
    c, shoes = synthetic_evidence(x, y, 0)
    projection = (x, y)
    if weakness == 'confidence': d.position_confidence = .69
    if weakness == 'calibration': c['calibration_view_valid'] = False
    if weakness == 'pose': c['foot_track'] = []
    if weakness == 'single_ground': d.grounded_frame_offsets_ms = [-900]
    if weakness == 'clipped': shoes['image_edge_offsets_ms'] = [-900, -767]
    if weakness == 'arc': d.visual_arc_relation = 'touching'
    if weakness == 'projection': projection = (25, 5.25)
    if weakness == 'not_shot': d.is_shot = False
    if weakness.endswith('_scope'):
        d.uncertainty_scope = {'physical_scope': 'physical_boundary', 'coordinate_scope': 'coordinate', 'view_scope': 'view'}[weakness]
    before = d.model_dump()
    audit = normalize_zone_boundary(d, c, projection, shoes)
    assert not audit['normalized'] and d.model_dump() == before


@pytest.mark.parametrize('case', EXAMPLES, ids=lambda c: '/'.join(c['pair']))
def test_production_review_keeps_supported_virtual_xy_without_retry(monkeypatch, case):
    x, y = case['xy']
    d = make_decision(x, y, case['named_zone'])
    c, shoes = synthetic_evidence(x, y, 0)
    monkeypatch.setattr(review, '_build_shared_camera_contexts', lambda *a: ({0: camera_context()}, [], 0))
    monkeypatch.setattr(review, 'candidate_frame_inputs', lambda *a: [])
    monkeypatch.setattr('app.services.review_frame_trial.dense_shoe_inputs', lambda *a: ([{'type': 'input_image'}], shoes))
    monkeypatch.setattr(review, '_review_once', lambda *a, **kw: (d, {'input_tokens': 1, 'output_tokens': 1}))
    def no_retry(*a, **kw):
        raise AssertionError('Supported virtual sector must not require retry')
    monkeypatch.setattr(review, '_review_position_once', no_retry)
    result = review.review_candidates_with_openai(Path('unused'), {'candidates': [c]},
        Settings(_env_file=None, openai_api_key='synthetic', openai_vision_fallback_model=None),
        client=object(), calibration_transform=c['validated_court_transform'])
    final = result['candidates'][0]['ai_review']
    assert [a['task'] for a in final['attempts']] == ['shot_review']
    assert final['zone_boundary']['normalized']
    assert final['court_zone'] == classify_zone(x, y).value
    assert (final['court_x'], final['court_y'], final['position_confidence']) == (x, y, .8)
    assert final['model_decisions']['primary']['court_zone'] == case['named_zone']
    assert 'ONLY the virtual' in position_conflict_prompt(make_decision(x, y, case['named_zone']))


def test_dummy_data_runs_through_save_reload_and_statistics(tmp_path):
    result = run_verification(tmp_path/'dummy')
    assert result['paid_api_calls'] == 0
    for trial in result['dummy_trials']:
        assert trial['cases'] >= 2700
        assert trial['physical_conflict_cases'] >= 1800
        assert trial['physical_conflicts_auto_renamed'] == 0
        assert trial['all_14_zones_aggregated']
        assert len(trial['normalized_virtual_pairs']) == 10
    saved = json.loads((tmp_path/'dummy/report.json').read_text(encoding='utf-8'))
    assert saved['dummy_trials'][0]['cases'] == result['dummy_trials'][0]['cases']


@pytest.mark.parametrize('xy', [(float('nan'), 20), (18, float('inf')), (-1, 20), (51, 20), (18, -1), (18, 48)])
def test_invalid_coordinates_never_get_normalized(xy):
    from types import SimpleNamespace
    d = SimpleNamespace(court_x=xy[0], court_y=xy[1], court_zone='mid_center', position_confidence=.9)
    audit = normalize_zone_boundary(d, {}, None, {})
    assert not audit['normalized']


def test_explicit_low_xy_confidence_cannot_be_hidden_by_high_position_score():
    from app.services.phase8_shots import _court_position
    d = make_decision(18.6, 22, 'mid_center')
    d.coordinate_confidence = .53
    d.zone_confidence = .95
    c, shoes = synthetic_evidence(18.6, 22, 0)
    before = d.model_dump()
    assert not normalize_zone_boundary(d, c, (18.6, 22), shoes)['normalized']
    assert d.model_dump() == before
    d.court_zone = 'mid_wing_left'
    assert _court_position(d.model_dump(), []) == (None, None, None)
