"""Compare saved decisions and controlled primary prompts without DB writes."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services import openai_shot_review as review
from app.services.outcome_evidence_trial import scoring_evidence_client
from app.services.phase8_shots import _court_position
from app.services.review_frame_trial import evidence_rich_detail_inputs, dense_shoe_inputs


PREVIOUS_ID = 'dc3ec7ee-325d-4637-9b5b-94387183ad05'
CURRENT_ID = '6f39ab0d-eb7f-45c8-8fcd-67892e0c20ac'
BASE_REVISION = '0cd802a'


def previous_definitions():
    source = subprocess.check_output([
        'git', 'show', f'{BASE_REVISION}:backend/app/services/openai_shot_review.py',
    ]).decode('utf-8')
    names = {'ShotReviewDecision', '_review_once', '_camera_context_prompt',
             '_foot_projection_prompt', '_court_geometry_prompt'}
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    namespace = dict(vars(review))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<previous-primary-review>', 'exec'), namespace)
    storage = subprocess.check_output([
        'git', 'show', f'{BASE_REVISION}:backend/app/services/phase8_shots.py',
    ]).decode('utf-8')
    storage_nodes = [node for node in ast.parse(storage).body
                     if isinstance(node, ast.FunctionDef) and node.name in {'_court_position', '_append_reason'}]
    from app.services import phase8_shots
    storage_namespace = dict(vars(phase8_shots))
    exec(compile(ast.Module(body=storage_nodes, type_ignores=[]), '<previous-storage-policy>', 'exec'), storage_namespace)
    return namespace, storage_namespace['_court_position']


def accepted(decision, function=_court_position):
    return function(decision, [])[0] is not None


def decision_summary(decision):
    return {key: decision.get(key) for key in (
        'court_zone', 'court_x', 'court_y', 'position_confidence', 'coordinate_confidence',
        'zone_confidence', 'uncertainty_scope', 'position_reason',
    )}


def improved_pipeline(current, settings, output, *, retry_only=False, retry_number=4):
    """Run six saved candidates through the normal review entry point.

    Camera interpretation and independent sole measurements are held constant
    to isolate the revised location questions and application policy.
    """
    from unittest.mock import patch
    from contextlib import nullcontext
    from openai import OpenAI
    from app.services.analysis_reuse import AnalysisReuse

    numbers = (retry_number,) if retry_only else (4, 8, 3, 7, 18, 22)
    candidates = [deepcopy(current['candidates'][number - 1]) for number in numbers]
    contacts = {c['time_ms']: deepcopy(c['ai_review']['sole_contact'])
                for c in candidates if c['ai_review'].get('sole_contact')}
    contexts = {int(item['scene_id']): review.CameraContextDecision.model_validate(item['decision'])
                for item in current['ai_review']['camera_contexts']}
    transforms = {int(c.get('scene_id', 0)): c['validated_court_transform'] for c in candidates}
    def shared_context(*args):
        return contexts, [], 0
    def saved_primary(*args, **kwargs):
        return review.ShotReviewDecision.model_validate(candidates[0]['ai_review']['model_decisions']['primary']), {}
    def contact_result(client, model, video, candidate):
        if candidate['time_ms'] not in contacts:
            raise ValueError('No controlled contact measurement for this candidate')
        value = deepcopy(contacts[candidate['time_ms']])
        from app.services.sole_contact import ContactSequence, evaluate_contact_sequence
        wider = evaluate_contact_sequence(ContactSequence.model_validate(value['model_measurement']),
                                          value['preparation'], candidate, extra_pixel_error=4.)
        value['wider_error_check']['diagnostic_sides'] = [f['side'] for f in wider['diagnostic_frames']]
        value.update(api_called=False, reused_measurement=True)
        return value, {}
    code = sha256(review._position_reliability_prompt().encode()).hexdigest()
    reuse = AnalysisReuse(settings.work_path / 'verification-2026-11-04' / 'position-refinement',
                          {'experiment': 'six-shot-location-questions', 'prompt': code})
    options = settings.model_copy(update={'openai_vision_fallback_model': None,
                                          'openai_max_candidate_reviews': len(candidates)})
    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
    started = perf_counter()
    primary_patch = patch.object(review, '_review_once', saved_primary) if retry_only else nullcontext()
    try:
        with patch.object(review, '_build_shared_camera_contexts', shared_context), primary_patch, patch(
                'app.services.sole_contact.review_contact_pixels', contact_result):
            result = review.review_candidates_with_openai(
                settings.upload_path / CURRENT_ID / 'original.mp4', {'candidates': candidates}, options,
                client=reuse.client(client), detail_workers=2, scoring_rim_evidence=True,
                camera_view_calibrations=transforms, coordinate_precision_trial=retry_only)
    finally:
        client.close()
    rows = []
    for number, candidate in zip(numbers, result['candidates']):
        ai = candidate['ai_review']
        before = current['candidates'][number - 1]['ai_review']
        rows.append({'number': number, 'time_ms': candidate['time_ms'],
                     'before': decision_summary(before), 'after': decision_summary(ai),
                     'before_accepted': accepted(before), 'after_accepted': accepted(ai),
                     'before_outcome': before.get('outcome'), 'after_outcome': ai.get('outcome'),
                     'attempts': ai.get('attempts', []), 'sole_contact': ai.get('sole_contact'),
                     'status': ai.get('status', 'complete')})
    try:
        previous_report = json.loads(output.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous_report = {}
    paid_runs = previous_report.get('paid_runs', [])
    if not paid_runs and previous_report.get('runtime', {}).get('api_calls', 0):
        paid_runs.append({key: previous_report[key] for key in ('seconds', 'api_usage', 'runtime')})
    report = {'scope': 'Actual candidates through review_candidates_with_openai, no DB writes',
              'retry_only': retry_only,
              'fixed_evidence': ['saved camera interpretation', 'saved independent sole measurements']
                                + (['saved low-confidence primary response'] if retry_only else []),
              'analysis_id': CURRENT_ID, 'seconds': round(perf_counter() - started, 3),
              'rows': rows, 'api_usage': {k: reuse.stats[k] for k in ('input_tokens', 'output_tokens')},
              'runtime': reuse.stats, 'api_usage_complete': reuse.stats['api_errors'] == 0,
              'review_summary': result['ai_review']}
    if reuse.stats['api_calls']:
        paid_runs.append({key: report[key] for key in ('seconds', 'api_usage', 'runtime')})
    report['paid_runs'] = paid_runs
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), 'seconds': report['seconds'],
                      'positions': [(r['number'], r['before_accepted'], r['after_accepted']) for r in rows],
                      'api_usage': report['api_usage'], 'calls': reuse.stats['api_calls']}, ensure_ascii=True))


def saved_audit(result, old_storage):
    rows = []
    counts = {'primary': 0, 'before_contact': 0, 'final': 0}
    buckets = {}
    for index, candidate in enumerate(result['candidates'], 1):
        final = candidate.get('ai_review') or {}
        models = final.get('model_decisions') or {}
        primary = models.get('primary', final)
        before_contact = models.get('pre_contact_policy', final)
        stages = {'primary': primary, 'before_contact': before_contact, 'final': final}
        stage_pass = {name: accepted(value) for name, value in stages.items()}
        for name, value in stage_pass.items():
            counts[name] += value
        stored = candidate.get('phase8') or {}
        outcome = stored.get('result')
        bucket = 'outcome_review' if outcome not in ('made', 'missed') else 'position_review' if stored.get('court_x') is None else outcome
        buckets[bucket] = buckets.get(bucket, 0) + 1
        rows.append({'number': index, 'time_ms': candidate['time_ms'], 'stages': stage_pass,
                     'old_storage_accepts_final': accepted(final, old_storage),
                     'primary': decision_summary(primary), 'final': decision_summary(final),
                     'contact_reason': (final.get('sole_contact') or {}).get('reason'),
                     'attempts': [item['task'] for item in final.get('attempts', [])]})
    return {'screen_buckets': buckets, 'accepted_positions': counts, 'rows': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ai', action='store_true', help='Run four paid, controlled Terra primary calls')
    parser.add_argument('--improved', action='store_true', help='Run six actual candidates with revised location policy')
    parser.add_argument('--retry-only', action='store_true', help='Exercise the location-only retry on a saved weak primary')
    parser.add_argument('--retry-number', type=int, choices=(4, 8), default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    old, old_storage = previous_definitions()
    previous = json.loads((settings.work_path / PREVIOUS_ID / 'phase7-candidates.json').read_text(encoding='utf-8'))
    current = json.loads((settings.work_path / CURRENT_ID / 'phase7-candidates.json').read_text(encoding='utf-8'))
    if args.improved or args.retry_only:
        improved_pipeline(current, settings, args.output, retry_only=args.retry_only, retry_number=args.retry_number)
        return
    report = {'previous_analysis': PREVIOUS_ID, 'current_analysis': CURRENT_ID,
              'comparison_revision': BASE_REVISION,
              'previous': saved_audit(previous, old_storage), 'current': saved_audit(current, old_storage),
              'controlled_primary_calls': [], 'api_usage': {'input_tokens': 0, 'output_tokens': 0},
              'scope': 'Saved-response ablation and optional controlled prompts, not ground-truth accuracy'}
    previous_times = {c['time_ms']: c for c in previous['candidates']}
    matches = []
    for index, candidate in enumerate(current['candidates'], 1):
        nearest = min(previous_times, key=lambda time: abs(time-candidate['time_ms']))
        if abs(nearest-candidate['time_ms']) <= 1500:
            old_candidate = previous_times[nearest]
            matches.append({'current_number': index, 'current_time_ms': candidate['time_ms'],
                            'previous_time_ms': nearest,
                            'previous_stored_position': (old_candidate.get('phase8') or {}).get('court_x') is not None,
                            'current_stored_position': (candidate.get('phase8') or {}).get('court_x') is not None})
    report['matched_shots'] = matches
    if args.ai:
        from openai import OpenAI
        video = settings.upload_path / CURRENT_ID / 'original.mp4'
        contexts = {int(item['scene_id']): review.CameraContextDecision.model_validate(item['decision'])
                    for item in current['ai_review']['camera_contexts']}
        trials = []
        # Two interior/baseline examples fell below .70 without a position retry.
        # Hold pixels, model, camera context and detector projections constant.
        for number in (4, 8):
            candidate = current['candidates'][number-1]
            ai = candidate['ai_review']
            images = evidence_rich_detail_inputs(video, candidate['time_ms'], candidate.get('foot_point_px'), candidate.get('tracked_hoop_px'))
            shoes, _ = dense_shoe_inputs(video, candidate)
            images = [*images, *shoes]
            signature = sha256(json.dumps(images, sort_keys=True).encode()).hexdigest()
            for variant, function in (('previous_prompt_schema', old['_review_once']), ('current_prompt_schema', review._review_once)):
                trials.append((number, variant, function, images, signature,
                               contexts[int(candidate.get('scene_id', 0))], ai))
        client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
        started = perf_counter()
        def run_trial(trial):
            number, variant, function, images, signature, context, ai = trial
            start = perf_counter()
            try:
                decision, usage = function(scoring_evidence_client(client), settings.openai_vision_model,
                    deepcopy(images), camera_context=context,
                    projected_foot=ai.get('calibrated_foot_projection'),
                    projected_plant=ai.get('calibrated_plant_projection'))
                raw = decision.model_dump()
                return {'number': number, 'variant': variant, 'image_fingerprint': signature,
                        'seconds': round(perf_counter()-start, 3), 'decision': decision_summary(raw),
                        'accepted': accepted(raw), 'usage': usage}
            except Exception as error:
                return {'number': number, 'variant': variant, 'image_fingerprint': signature,
                        'seconds': round(perf_counter()-start, 3), 'error_type': type(error).__name__}
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                report['controlled_primary_calls'] = list(pool.map(run_trial, trials))
        finally:
            client.close()
        report['controlled_seconds'] = round(perf_counter()-started, 3)
        for item in report['controlled_primary_calls']:
            for key in report['api_usage']:
                report['api_usage'][key] += int((item.get('usage') or {}).get(key, 0))
        report['api_usage_complete'] = not any('error_type' in item for item in report['controlled_primary_calls'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'previous': report['previous']['accepted_positions'],
                      'current': report['current']['accepted_positions'],
                      'matched_shots': len(matches),
                      'controlled_call_count': len(report['controlled_primary_calls']),
                      'output': str(args.output),
                      'api_usage': report['api_usage']}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
