"""Isolated, deterministic 14-zone boundary/persistence verification.

No live service DB, video analysis, or paid API request is used by this script.
Existing AI responses may be replayed, clearly distinguished from new inference.
Run from backend: python scripts/verify_zone_boundaries.py --output work/zone-boundary-verification
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Analysis, AnalysisStatus, CalibrationSource, CourtCalibration, Player, PlayType, Shot
from app.services.court_zones import classify_zone
from app.services.openai_shot_review import ShotReviewDecision
from app.services.phase8_shots import materialize_shots_from_candidates
from app.services.shot_statistics import calculate_statistics
from app.services.zone_boundaries import zone_boundaries, zone_family
from app.services.zone_boundary_policy import normalize_zone_boundary


def synthetic_evidence(x: float, y: float, index: int):
    release = 2000+index*10000
    candidate = {'time_ms': release, 'calibration_view_valid': True,
                 'validated_court_transform': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                 'foot_track': [{'time_ms': release+offset, 'foot_point_px': [x, y],
                                 'ankle_points_px': [[x, y-2]], 'person_box_px': [x-10, y-100, x+10, y]}
                                for offset in (-900, -767)]}
    shoes = {'status': 'available', 'offsets_ms': [-900, -767], 'image_edge_offsets_ms': []}
    return candidate, shoes


def make_decision(x, y, named_zone, index=0):
    return ShotReviewDecision(is_shot=True, confidence=.9, is_shot_confidence=.9,
        outcome='made' if index % 2 else 'missed', outcome_confidence=.9,
        shot_type='set_shot', shot_type_confidence=.9, court_zone=named_zone,
        court_x=x, court_y=y, position_confidence=.8, position_reason='SYNTHETIC fixture, not video evidence',
        visible_evidence=['rim_passage'], sequence_start_offset_ms=-2000, sequence_end_offset_ms=2000,
        review_required=False, reason='SYNTHETIC fixture',
        visual_arc_relation='outside' if zone_family(classify_zone(x, y).value) == 'three' else 'inside',
        grounded_frame_offsets_ms=[-900, -767], arc_evidence='SYNTHETIC two grounded frames')


def dummy_cases():
    """Both sides, exact line, near/far offsets, segment ends and intersections."""
    cases = []
    for boundary_index, boundary in enumerate(zone_boundaries()):
        for fraction in (0., .2, .5, .8, 1.):
            px, py = boundary.point(fraction)
            nx, ny = boundary.normal(fraction)
            for offset in (-31., -29., -16., -14., -5., 0., 5., 14., 16., 29., 31.):
                x, y = px+nx*offset/30.48, py+ny*offset/30.48
                if not (0 <= x <= 50 and 0 <= y <= 47):
                    continue
                zone = classify_zone(x, y).value
                alternatives = [z for z in boundary.zones if z != zone]
                if boundary.physical:
                    alternatives = [z for z in alternatives if zone_family(z) != zone_family(zone)]
                if not alternatives:
                    continue
                cases.append({'id': f'b{boundary_index}-{fraction}-{offset}', 'xy': [x, y],
                              'named_zone': alternatives[0], 'coordinate_zone': zone,
                              'pair': list(boundary.zones), 'physical_source': boundary.physical,
                              'fraction': fraction, 'offset_cm': offset})
    # Interior correctly named shots ensure every zone can still be stored.
    centers = {'restricted': (25, 5.25), 'paint_left': (19, 10), 'paint_center': (25, 15),
               'paint_right': (31, 10), 'mid_baseline_left': (10, 8), 'mid_baseline_right': (40, 8),
               'mid_wing_left': (12, 20), 'mid_wing_right': (38, 20), 'mid_center': (25, 22),
               'three_corner_left': (1.5, 8), 'three_corner_right': (48.5, 8),
               'three_wing_left': (10, 30), 'three_wing_right': (40, 30), 'three_center': (25, 35)}
    for zone, xy in centers.items():
        assert classify_zone(*xy).value == zone
        cases.append({'id': f'interior-{zone}', 'xy': list(xy), 'named_zone': zone,
                      'coordinate_zone': zone, 'pair': [zone], 'physical_source': False})
    return cases


def analysis_setup(session, title, duration_ms):
    analysis = Analysis(title=title, play_type=PlayType.SOLO, original_name='SYNTHETIC-no-video.mp4',
        stored_path='SYNTHETIC-no-video.mp4', file_size=0, duration_ms=duration_ms,
        status=AnalysisStatus.ANALYZING)
    session.add(analysis)
    session.flush()
    session.add(Player(analysis_id=analysis.id, display_name='SYNTHETIC'))
    calibration = CourtCalibration(analysis_id=analysis.id, frame_time_ms=0,
        image_points=[[0, 0], [50, 0], [50, 47], [0, 47]], court_points=[[0, 0], [50, 0], [50, 47], [0, 47]],
        hoop_point=[25, 5.25], transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]], source=CalibrationSource.USER)
    session.add(calibration)
    session.flush()
    return analysis, calibration


def run_dummy_trial(session, tolerance_cm):
    cases = dummy_cases()
    analysis, calibration = analysis_setup(session, f'SYNTHETIC boundary {tolerance_cm}cm', len(cases)*10000+10000)
    candidates, rows = [], []
    for index, case in enumerate(cases):
        x, y = case['xy']
        decision = make_decision(x, y, case['named_zone'], index)
        before = decision.model_dump()
        candidate, shoes = synthetic_evidence(x, y, index)
        audit = normalize_zone_boundary(decision, candidate, (x, y), shoes, tolerance_cm=tolerance_cm)
        after = decision.model_dump()
        assert {**after, 'court_zone': before['court_zone']} == before, case['id']
        if case['physical_source']:
            assert not audit['normalized'], case['id']
        if audit['normalized']:
            assert decision.court_zone == classify_zone(x, y).value
            assert zone_family(case['named_zone']) == zone_family(decision.court_zone)
        candidate['ai_review'] = {**after, 'status': 'complete', 'zone_boundary': audit}
        candidates.append(candidate)
        rows.append({**case, 'audit': audit, 'expected_saved': decision.court_zone == case['coordinate_zone']})
    materialize_shots_from_candidates(session, analysis, calibration, {'candidates': candidates})
    session.commit()
    session.expire_all()
    shots = list(session.scalars(select(Shot).where(Shot.analysis_id == analysis.id).order_by(Shot.candidate_time_ms)))
    assert len(shots) == len(rows)
    for row, shot, candidate in zip(rows, shots, candidates, strict=True):
        assert (shot.court_x is not None) == row['expected_saved'], row['id']
        if row['expected_saved']:
            assert (shot.court_x, shot.court_y) == tuple(row['xy'])
            assert shot.zone_code == row['coordinate_zone']
            assert classify_zone(shot.court_x, shot.court_y).value == shot.zone_code
        assert shot.result.value == candidate['ai_review']['outcome']
        row['saved_zone'] = shot.zone_code
        row['saved_xy'] = [shot.court_x, shot.court_y]
        row['saved_status'] = shot.status.value
    stats = calculate_statistics(shots)
    resolved = sum(s.court_x is not None for s in shots)
    assert sum(v['attempts'] for v in stats['by_zone'].values()) == resolved
    assert sum(v['attempts'] > 0 for v in stats['by_zone'].values()) == 14
    normalized_pairs = sorted({tuple(r['pair']) for r in rows if r['audit']['normalized']})
    expected_virtual = {b.zones for b in zone_boundaries() if not b.physical}
    assert set(normalized_pairs) == expected_virtual
    return {'tolerance_cm': tolerance_cm, 'cases': len(rows), 'normalized': sum(r['audit']['normalized'] for r in rows),
            'saved_positions': resolved, 'review_positions': len(shots)-resolved,
            'physical_conflict_cases': sum(r['physical_source'] for r in rows),
            'physical_conflicts_auto_renamed': 0, 'all_14_zones_aggregated': True,
            'normalized_virtual_pairs': normalized_pairs, 'reasons': dict(Counter(r['audit']['reason'] for r in rows)),
            'statistics': stats, 'rows': rows}


def replay_real_primary(session, source_path):
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    trials = []
    for width in (15, 30):
        analysis, calibration = analysis_setup(session, f'REPLAY real 3/18 and physical controls {width}cm', 600000)
        candidates, rows = [], []
        for number in (3, 18, 15, 30):
            candidate = deepcopy(source['candidates'][number-1])
            old_review = candidate['ai_review']
            primary = ShotReviewDecision.model_validate(old_review['model_decisions']['primary'])
            before = primary.model_dump()
            audit = normalize_zone_boundary(primary, candidate, old_review.get('calibrated_plant_projection'),
                                            old_review['shoe_evidence'], tolerance_cm=width)
            after = primary.model_dump()
            assert {**after, 'court_zone': before['court_zone']} == before
            candidate['ai_review'] = {**after, 'status': 'complete', 'zone_boundary': audit}
            candidate.pop('phase8', None)
            candidates.append(candidate)
            rows.append({'shot_number': number, 'time_ms': candidate['time_ms'], 'original_primary_zone': before['court_zone'],
                'original_final_zone': old_review['court_zone'], 'original_final_confidence': old_review['position_confidence'],
                'original_primary_confidence': before['position_confidence'], 'policy_zone': after['court_zone'],
                'xy': [after['court_x'], after['court_y']], 'audit': audit})
        shots = materialize_shots_from_candidates(session, analysis, calibration, {'candidates': candidates})
        session.commit()
        for row, shot in zip(rows, shots, strict=True):
            row.update(saved_xy=[shot.court_x, shot.court_y], saved_zone=shot.zone_code, saved_status=shot.status.value)
            if row['shot_number'] in (3, 18):
                assert shot.court_x is not None and shot.zone_code == classify_zone(*row['xy']).value
            else:
                assert not row['audit']['normalized'] and shot.court_x is None
        trials.append({'tolerance_cm': width, 'rows': rows})
    assert sha256(source_path.read_bytes()).hexdigest() == sha256(source_bytes).hexdigest()
    return {'source_analysis_id': source_path.parent.name, 'source_sha256': sha256(source_bytes).hexdigest(),
            'new_api_calls': 0, 'kind': 'frozen_first_response_replay_not_new_video_inference', 'trials': trials}


def run_verification(output: Path, replay_source: Path | None = None):
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    engine = create_engine(f'sqlite:///{(output / "synthetic-test.sqlite").resolve().as_posix()}')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        report = {'kind': 'isolated_synthetic_boundary_test', 'live_database_modified': False,
                  'paid_api_calls': 0, 'boundary_pieces': len(zone_boundaries()),
                  'adjacent_pairs': len({b.zones for b in zone_boundaries()}),
                  'virtual_pairs': len({b.zones for b in zone_boundaries() if not b.physical}),
                  'physical_pairs': len({b.zones for b in zone_boundaries() if b.physical}),
                  'dummy_trials': [run_dummy_trial(session, width) for width in (15, 30)]}
        if replay_source:
            report['real_primary_replay'] = replay_real_primary(session, replay_source)
    engine.dispose()
    report['seconds'] = round(perf_counter()-started, 4)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay-source', type=Path)
    args = parser.parse_args()
    result = run_verification(args.output, args.replay_source)
    print(json.dumps({k: v for k, v in result.items() if k not in ('dummy_trials', 'real_primary_replay')}))
    for trial in result['dummy_trials']:
        print(json.dumps({k: v for k, v in trial.items() if k not in ('rows', 'statistics', 'normalized_virtual_pairs', 'reasons')}))
