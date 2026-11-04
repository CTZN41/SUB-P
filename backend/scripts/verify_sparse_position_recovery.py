"""Bounded, read-only replay. --paid explicitly enables at most five Terra calls.

Run from backend. Never write analysis records, invoke jobs or replace saved JSON.
Local scan artifacts are prepared separately in work/verification-2026-11-04.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.candidate_windows import window_policy
from app.core.config import get_settings
from app.services import openai_shot_review as review
from app.services.court_zones import classify_zone
from app.services.non_shot_recovery import separate_recovery_motion
from app.services.review_frame_trial import dense_shoe_inputs, fast_candidate_frame_inputs, position_only_inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--paid', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    analysis_id = 'cb49b783-591d-4999-a131-286d6ee80b54'
    original = root / 'work' / analysis_id / 'phase7-candidates.json'
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    data = json.loads(original.read_text(encoding='utf-8'))
    work = root / 'work/verification-2026-11-04/position-recovery'
    fresh = json.loads((work / 'fresh-foot-tracks.json').read_text(encoding='utf-8'))
    fresh_by_number = {c['audit_number']: c for c in fresh}
    report = {'analysis_id': analysis_id, 'mode': 'bounded_paid_position_replay' if args.paid else 'offline_only',
              'window_policy': window_policy(), 'local_window_comparison': {}, 'ground_references': [],
              'paid_calls': [], 'api_usage': {'input_tokens': 0, 'output_tokens': 0}}
    destination = root / 'evaluation/results' / ('position-recovery-2026-11-04.json' if args.paid else 'position-recovery-offline-2026-11-04.json')

    def save():
        report['saved_result_unchanged'] = hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if args.paid:
            (work / 'paid-replay-snapshot.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for name in ('trace_old', 'trace_extended_same_start', 'trace_fixed_policy'):
        value = json.loads((work / (name+'.json')).read_text(encoding='utf-8'))
        report['local_window_comparison'][name] = {
            'requested_ms': [value['range_start_ms'], value['range_end_ms']],
            'decoder_context_start_ms': value['context_start_ms'], 'rule_trace': value['candidate_rule_trace'],
            'seconds': value['performance']['total_seconds']}
    for number, candidate in fresh_by_number.items():
        projected, time_ms = review._projected_plant_position(candidate.get('validated_court_transform'), candidate)
        report['ground_references'].append({'number': number, 'release_ms': candidate['time_ms'],
            'reference_ms': time_ms, 'projection': projected, 'measured_contact': False,
            'source': 'pose_stable_time_box_lower_midpoint', 'local_seconds': candidate['refresh_local_seconds']})
    recovered = separate_recovery_motion(deepcopy(data))
    report['non_shot_replay'] = {'before': len(data['candidates']), 'after': len(recovered['candidates']),
        'excluded': [{'time_ms': c['time_ms'], **c['exclusion']} for c in recovered['excluded_non_shot_candidates']]}
    save()
    if not args.paid:
        print('offline_report_saved; no API calls')
        return

    from openai import OpenAI
    from app.analysis.shot_gate_trial import review_shot_existence
    settings = get_settings().model_copy(update={'openai_vision_fallback_model': None})
    if not settings.openai_api_key or 'terra' not in settings.openai_vision_model.lower():
        raise RuntimeError('Configured Terra model required for this bounded test')
    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
    video = root / 'uploads' / analysis_id / 'original.mp4'
    contexts = {item['scene_id']: review.CameraContextDecision.model_validate(item['decision'])
                for item in data['ai_review']['camera_contexts'] if item.get('decision')}

    def paid(task, number, function):
        if len(report['paid_calls']) >= 5:
            raise RuntimeError('Test call limit reached')
        started = perf_counter()
        value, usage = function()
        record = {'task': task, 'number': number, 'model': settings.openai_vision_model,
                  'seconds': round(perf_counter()-started, 3), 'usage': usage,
                  'decision': value.model_dump() if hasattr(value, 'model_dump') else value}
        for key in report['api_usage']:
            report['api_usage'][key] += int(usage.get(key, 0) or 0)
        report['paid_calls'].append(record)
        save()
        print(json.dumps({'task': task, 'number': number, 'seconds': record['seconds'], 'usage': usage}), flush=True)
        return value

    # Recheck only position, retaining saved shot/type/outcome for this experiment.
    for number in (14, 18, 29):
        candidate = fresh_by_number.get(number, data['candidates'][number-1])
        images = fast_candidate_frame_inputs(video, candidate['time_ms'], candidate.get('foot_point_px'))
        shoes, evidence = dense_shoe_inputs(video, candidate)
        images = position_only_inputs(images, shoes)
        projected, _ = review._projected_plant_position(candidate.get('validated_court_transform'), candidate)
        visual = None
        if number == 14:
            visual = paid('missing_xy_visual_arc', number,
                lambda: review._review_visual_arc_once(client, settings.openai_vision_model, images))
            visual.grounded_frame_offsets_ms = [t for t in visual.grounded_frame_offsets_ms if t in evidence['offsets_ms']]
            if visual.confidence < .70 or not review._grounded_arc_evidence(visual):
                report['missing_xy_still_held'] = True
                save()
                continue
        position = paid('position_only', number, lambda: review._review_position_once(client,
            settings.openai_vision_model, images, camera_context=contexts.get(candidate.get('scene_id',0)),
            projected_foot=None, projected_plant=projected,
            inconsistency='Recheck actual grounded shoes, not an airborne release frame. '
            'The stable-time detector projection is approximate, not measured contact. '
            'Return needs_review if shoe contact or the court mapping cannot be verified. '
            + (f'Independent visual arc: {visual.visual_arc_relation}; {visual.arc_evidence}' if visual else '')))
        valid = position.confidence >= .70 and not review._position_zone_mismatch(position) and not review._visual_arc_conflict(position)
        if visual:
            position.grounded_frame_offsets_ms = [t for t in position.grounded_frame_offsets_ms if t in evidence['offsets_ms']]
            valid = valid and review._grounded_arc_evidence(position) and position.visual_arc_relation == visual.visual_arc_relation
        report['paid_calls'][-1]['passes_position_checks'] = bool(valid and position.court_x is not None and position.court_y is not None)
        report['paid_calls'][-1]['numeric_zone'] = classify_zone(position.court_x,position.court_y).value if position.court_x is not None and position.court_y is not None else None
        save()
    def gate_call():
        gate = review_shot_existence(video, 205633, settings, client=client)
        return gate, gate['usage']
    paid('recovered_candidate_shot_gate', '03:26', gate_call)
    report['api_usage']['total_tokens'] = sum(report['api_usage'].values())
    save()
    print('saved_result_unchanged:', report['saved_result_unchanged'])


if __name__ == '__main__':
    main()
