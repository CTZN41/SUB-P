"""Audit or reapply location acceptance to one authorized completed analysis."""
import argparse
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models import Analysis, CourtCalibration, Shot
from app.services.analysis_jobs import active_job, phase7_result_path
from app.services.analysis_reuse import atomic_json
from app.services.phase8_shots import _court_position, materialize_shots_from_candidates
from app.services.shot_review_policy import POSITION_AUTO_CONFIRM_CONFIDENCE, BOUNDARY_EVIDENCE_CONFIDENCE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--analysis-id', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    result_path = phase7_result_path(settings, args.analysis_id)
    original = json.loads(result_path.read_text(encoding='utf-8'))
    result = deepcopy(original)
    audit = {'analysis_id': args.analysis_id, 'thresholds': [], 'api_calls': 0}
    for threshold in (.70, .68, .65, .60):
        with patch('app.services.phase8_shots.POSITION_AUTO_CONFIRM_CONFIDENCE', threshold):
            accepted = [i for i, c in enumerate(original['candidates'], 1)
                        if _court_position(c.get('ai_review', {}), [])[0] is not None]
        audit['thresholds'].append({'threshold': threshold, 'accepted': len(accepted),
                                    'total': len(original['candidates']), 'numbers': accepted})
    if args.apply:
        engine = create_database_engine(settings.database_url)
        backup = settings.work_path / args.analysis_id / 'phase7-before-location-65.json'
        try:
            with create_session_factory(engine)() as session:
                analysis = session.scalar(select(Analysis).where(Analysis.id == args.analysis_id).with_for_update())
                if analysis is None or not analysis.owner_id or active_job(session, analysis.id):
                    raise RuntimeError('An owned completed analysis without an active job is required')
                if original.get('ai_review', {}).get('status') != 'complete':
                    raise RuntimeError('Complete AI responses are required')
                session.info.update(auth_required=True, auth_user_id=analysis.owner_id)
                calibration = session.scalar(select(CourtCalibration).where(
                    CourtCalibration.analysis_id == analysis.id).order_by(CourtCalibration.frame_time_ms))
                if calibration is None:
                    raise RuntimeError('Saved court calibration is required')
                existing = list(session.scalars(select(Shot).where(Shot.analysis_id == analysis.id).with_for_update()))
                stable = {(s.id, s.candidate_time_ms, s.result.value, s.shot_type.value) for s in existing}
                if {s.candidate_time_ms for s in existing} != {c['time_ms'] for c in result['candidates']}:
                    raise RuntimeError('Candidate times differ from saved shots')
                if not backup.exists():
                    atomic_json(backup, original)
                shots = materialize_shots_from_candidates(session, analysis, calibration, result)
                if stable != {(s.id, s.candidate_time_ms, s.result.value, s.shot_type.value) for s in shots}:
                    raise RuntimeError('Policy reapplication changed shot identity, outcome or type')
                if [c['ai_review'] for c in result['candidates']] != [c['ai_review'] for c in original['candidates']]:
                    raise RuntimeError('Policy reapplication changed model responses')
                result['position_policy_application'] = {
                    'acceptance_confidence': POSITION_AUTO_CONFIRM_CONFIDENCE,
                    'boundary_evidence_confidence': BOUNDARY_EVIDENCE_CONFIDENCE,
                    'source': 'saved_ai_responses', 'api_calls': 0,
                    'preserved_model_confidence': True,
                }
                atomic_json(result_path, result)
                try:
                    session.commit()
                except Exception:
                    atomic_json(result_path, original)
                    raise
                audit['saved'] = {'total': len(shots),
                                  'position_confirmed': sum(s.court_x is not None for s in shots),
                                  'held_numbers': [i for i, s in enumerate(shots, 1) if s.court_x is None],
                                  'outcomes': dict(Counter(s.result.value for s in shots)),
                                  'identities_outcomes_types_preserved': True}
        finally:
            engine.dispose()
    destination = settings.work_path / 'verification-2026-11-04' / 'location-65' / f'{args.analysis_id}.json'
    atomic_json(destination, audit)
    print(json.dumps(audit, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
