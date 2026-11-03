"""Read-only position regression. Paid Terra review requires --paid explicitly.

Does not start a web job, materialize shots, or modify the source result/DB.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.services.view_alignment_trial import group_camera_views
from app.services.openai_shot_review import review_candidates_with_openai
from app.services.review_frame_trial import dense_shoe_inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--analysis-id', default='73c65375-acb3-474f-8187-a0fa991cb98a')
    parser.add_argument('--paid', action='store_true')
    parser.add_argument('--numbers', default='1,14,28,41')
    parser.add_argument('--tag', default='')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = f'http://127.0.0.1:8000/api/analyses/{args.analysis_id}'
    def get(suffix):
        with urlopen(base+suffix, timeout=30) as response:
            return json.load(response)
    shots = get('/shots')['shots']
    source = get('/phase7-candidates')
    profiles = get('/calibrations')
    selected = [(n, s) for n, s in enumerate(shots, 1)
                if s['court_x'] is None or n in (1, 28, 41)]
    candidates = []
    for number, shot in selected:
        c = deepcopy(min(source['candidates'], key=lambda x: abs(x['time_ms']-shot['candidate_time_ms'])))
        c['audit_number'] = number
        c.pop('ai_review', None)
        c.pop('phase8', None)
        # Preserve the actual pre-group scene ID, not the old assigned view ID.
        c['scene_id'] = c.get('source_scene_id', c.get('scene_id', 0))
        candidates.append(c)
    video = root / 'uploads' / args.analysis_id / 'original.mp4'
    started = perf_counter()
    transforms, reports = group_camera_views(video, candidates, dict(profiles[0], views=profiles))
    summary = {'analysis_id': args.analysis_id, 'db_writes': 0,
               'local_seconds': round(perf_counter()-started, 3),
               'source_count': len(shots), 'local': []}
    for c, report in zip(candidates, reports):
        _, shoes = dense_shoe_inputs(video, c)
        summary['local'].append({'number': c['audit_number'], 'time_ms': c['time_ms'],
                                 'valid': c['calibration_view_valid'], 'checks': report['checks'],
                                 'shoe_evidence': shoes})
    print(json.dumps({'local_seconds': summary['local_seconds'],
                      'views': [(c['audit_number'], c['calibration_view_valid']) for c in candidates]}), flush=True)
    if args.paid:
        # Two user-confirmed three-pointers, one formerly blocked view, one
        # unchanged clear shot. No arbitrary high-cost full-video rerun.
        numbers = {int(n) for n in args.numbers.split(',')}
        if not numbers.issubset({1, 14, 28, 41}):
            raise ValueError('paid trial is limited to the four planned shots')
        subset = [c for c in candidates if c['audit_number'] in numbers]
        settings = Settings().model_copy(update={
            'openai_vision_model': 'gpt-6.1-terra', 'openai_vision_fallback_model': None,
            'openai_max_candidate_reviews': 4, 'openai_shot_review_enabled': True})
        if not settings.openai_api_key:
            raise RuntimeError('OpenAI key not configured')
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)
        started = perf_counter()
        result = review_candidates_with_openai(
            video, {'candidates': subset}, settings, client=client,
            camera_view_calibrations=transforms, detail_workers=2,
            fast_image_sampling=True, scoring_rim_evidence=True,
            review_progress=lambda done, total: print(f'AI reviewed {done}/{total}', flush=True))
        summary['paid_seconds'] = round(perf_counter()-started, 3)
        summary['usage'] = result['ai_review'].get('usage')
        summary['model'] = settings.openai_vision_model
        summary['reviewed'] = []
        for c in result['candidates']:
            a = c.get('ai_review', {})
            summary['reviewed'].append({'number': c['audit_number'], 'time_ms': c['time_ms'],
                'decision': a, 'valid': c['calibration_view_valid']})
            print(json.dumps({'number': c['audit_number'],
                              'zone': a.get('court_zone'), 'xy': [a.get('court_x'), a.get('court_y')],
                              'position_confidence': a.get('position_confidence'),
                              'visual_arc_relation': a.get('visual_arc_relation'),
                              'outcome': a.get('outcome'), 'status': a.get('status')}), flush=True)
    suffix = 'paid' if args.paid else 'local'
    tag = '-'+args.tag if args.tag else ''
    output = root / 'evaluation/results' / f'position-evidence-{suffix}{tag}-2026-11-03.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('saved', output.name, flush=True)


if __name__ == '__main__':
    main()
