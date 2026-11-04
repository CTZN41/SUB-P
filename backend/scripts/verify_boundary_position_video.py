"""Explicit two-request Terra position test on existing shots 3 and 18.

Reads existing video/evidence without editing source records. This is NOT a
whole-video rerun. Each paid result is written immediately to a new directory.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services import openai_shot_review as review
from app.services.review_frame_trial import fast_candidate_frame_inputs, dense_shoe_inputs, position_only_inputs
from app.services.zone_boundary_policy import normalize_zone_boundary, position_conflict_prompt, coordinate_confidence


def run(output, source_path, video):
    from openai import OpenAI
    settings = get_settings()
    if settings.openai_vision_model != 'gpt-6.1-terra':
        raise RuntimeError('This trial requires the configured Terra model; no escalation is permitted')
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    contexts = {c['scene_id']: review.CameraContextDecision.model_validate(c['decision'])
                for c in source['ai_review']['camera_contexts'] if c.get('decision')}
    output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'fresh_original_video_position_only', 'model': settings.openai_vision_model,
              'source_analysis_id': source_path.parent.name, 'source_sha256': sha256(source_bytes).hexdigest(),
              'live_database_modified': False, 'requested_shots': [3, 18], 'rows': []}
    save = lambda: (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
    started = perf_counter()
    for number in (3, 18):
        candidate = deepcopy(source['candidates'][number-1])
        primary = review.ShotReviewDecision.model_validate(candidate['ai_review']['model_decisions']['primary'])
        row = {'shot_number': number, 'time_ms': candidate['time_ms'], 'status': 'preparing'}
        report['rows'].append(row)
        save()
        task_start = perf_counter()
        try:
            images = fast_candidate_frame_inputs(video, candidate['time_ms'], candidate.get('foot_point_px'))
            shoes, evidence = dense_shoe_inputs(video, candidate)
            position_images = position_only_inputs(images, shoes)
            plant, _ = review._projected_plant_position(candidate['validated_court_transform'], candidate)
            row['image_count'] = sum(i.get('type') == 'input_image' for i in position_images)
            row['shoe_evidence'] = evidence
            row['status'] = 'requesting'
            save()
            api_start = perf_counter()
            decision, usage = review._review_position_once(client, settings.openai_vision_model, position_images,
                camera_context=contexts.get(candidate['scene_id']), projected_plant=plant,
                inconsistency=position_conflict_prompt(primary))
            row.update(status='received', usage=usage, api_seconds=round(perf_counter()-api_start, 4),
                       raw_decision=decision.model_dump())
            save()  # Persist paid evidence before further policy checks.
            normalized = decision.model_copy(deep=True)
            audit = normalize_zone_boundary(normalized, candidate, plant, evidence)
            final = primary.model_copy(deep=True)
            if not review._position_zone_mismatch(normalized):
                review._apply_position_review(final, normalized, initial_zone_mismatch=True)
            unresolved = review._position_zone_mismatch(normalized) or normalized.court_zone == 'needs_review' or coordinate_confidence(normalized) < .7
            row.update(status='complete', policy_decision=normalized.model_dump(), audit=audit,
                       location_resolved=not unresolved, applied_zone=final.court_zone,
                       applied_xy=[final.court_x, final.court_y], applied_confidence=final.position_confidence)
        except Exception as error:
            row.update(status='failed', error_type=type(error).__name__)
        row['seconds'] = round(perf_counter()-task_start, 4)
        save()
        print(json.dumps({k: row[k] for k in ('shot_number', 'status', 'location_resolved', 'applied_zone', 'applied_xy', 'applied_confidence') if k in row}), flush=True)
    client.close()
    report['seconds'] = round(perf_counter()-started, 4)
    report['usage'] = {key: sum(r.get('usage', {}).get(key, 0) for r in report['rows'])
                       for key in ('input_tokens', 'output_tokens')}
    report['source_unchanged'] = sha256(source_path.read_bytes()).hexdigest() == report['source_sha256']
    save()
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--confirm-paid-two', action='store_true', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--video', type=Path, required=True)
    args = p.parse_args()
    result = run(args.output, args.source, args.video)
    print(json.dumps({'seconds': result['seconds'], 'usage': result['usage'], 'source_unchanged': result['source_unchanged']}))
