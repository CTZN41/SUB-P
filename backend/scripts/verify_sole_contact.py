"""Read-only, two-shot native-pixel/line test. Explicit --paid is required."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from app.core.config import get_settings
from app.services.sole_contact import prepare_contact_sequence, locate_soles_once, evaluate_contact_sequence


def annotate(frame, measured, metadata):
    out = frame.copy()
    line = next((l for l in metadata['line_observations'] if l['frame_index'] == measured.get('line_reference_frame_index')), None)
    if line:
        for key, color in (('predicted_points_px', (255, 0, 255)), ('points_px', (255, 255, 0))):
            a, b = [tuple(round(v) for v in p) for p in line[key]]
            cv2.line(out, a, b, color, 2, cv2.LINE_AA)
    for point in measured['contacts']:
        center = tuple(round(v) for v in point['pixel'])
        cv2.circle(out, center, 4, (0, 255, 0) if point['confidence'] >= .7 else (0, 165, 255), -1)
        cv2.circle(out, center, round(point['error_px']), (0, 190, 255), 1)
    for text, y in ((f"frame {measured['frame_index']} t={measured['time_ms']/1000:.3f}s: {measured['side']}", 35),
                    ('cyan: observed FT | magenta: calibration | green: confident AI sole | amber: low confidence/error', 65),
                    (f"projected soles y={measured['y_interval_ft'][0]:.3f}..{measured['y_interval_ft'][1]:.3f}ft | FT=19ft", 95)):
        cv2.putText(out, text, (25, y), cv2.FONT_HERSHEY_SIMPLEX, .75, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(out, text, (25, y), cv2.FONT_HERSHEY_SIMPLEX, .75, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def temporal_match(first, second):
    pairs = []
    a = {i: cv2.cvtColor(f[580:830, 850:1400], cv2.COLOR_BGR2GRAY).astype('float32') for i, f in first.items()}
    b = {i: cv2.cvtColor(f[580:830, 850:1400], cv2.COLOR_BGR2GRAY).astype('float32') for i, f in second.items()}
    for i, frame in a.items():
        j, score = min(((j, float(np.mean(abs(frame-other)))) for j, other in b.items()), key=lambda p: p[1])
        if j not in (min(b), max(b)):
            pairs.append({'shot3_frame': i, 'shot18_frame': j, 'frame_offset': j-i, 'gray_mae': score})
    return {'kind': 'image_comparison_not_independent_shot_truth', 'pairs': pairs,
            'median_frame_offset': float(np.median([p['frame_offset'] for p in pairs])) if pairs else None,
            'median_gray_mae': float(np.median([p['gray_mae'] for p in pairs])) if pairs else None}


def main(args):
    from contextlib import nullcontext
    settings = get_settings()
    assert settings.openai_vision_model == 'gpt-6.1-terra', 'Terra-only trial'
    source_bytes = args.source.read_bytes()
    source = json.loads(source_bytes)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'source_analysis_id': args.source.parent.name, 'source_sha256': sha256(source_bytes).hexdigest(),
              'model': settings.openai_vision_model, 'live_records_modified': False, 'rows': []}
    path = args.output/'report.json'
    save = lambda: path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    started = perf_counter()
    frame_sets = []
    cached = json.loads(args.responses.read_text(encoding='utf-8')) if args.responses else None
    report['inference_source'] = 'cached_paid_response_replay' if cached else 'fresh_video_inference'
    if cached:
        manager = nullcontext(None)
        from app.services.sole_contact import ContactSequence
    else:
        from openai import OpenAI
        manager = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
    with manager as client:
        for number in (3, 18):
            c = deepcopy(source['candidates'][number-1])
            row = {'shot_number': number, 'release_ms': c['time_ms'], 'status': 'preparing'}
            report['rows'].append(row); save()
            ts = perf_counter()
            try:
                inputs, metadata, frames = prepare_contact_sequence(args.video, c)
                frame_sets.append(frames)
                row['metadata'] = metadata
                if not inputs:
                    row['status'] = 'unavailable'; save(); continue
                directory = args.output/f'shot{number:02d}'
                directory.mkdir()
                x1, y1, x2, y2 = metadata['roi_px']
                for i, f in frames.items():
                    cv2.imwrite(str(directory/f'crop-{i}.png'), f[y1:y2, x1:x2])
                row['status'] = 'requesting'; save()
                api_start = perf_counter()
                if cached:
                    cached_row = next(r for r in cached['rows'] if r['shot_number'] == number)
                    sequence = ContactSequence.model_validate(cached_row['response'])
                    usage = {'input_tokens': 0, 'output_tokens': 0}
                else:
                    sequence, usage = locate_soles_once(client, settings.openai_vision_model, inputs)
                row.update(status='received', response=sequence.model_dump(), usage=usage,
                           api_seconds=round(perf_counter()-api_start, 4)); save()
                evaluations = [evaluate_contact_sequence(sequence, metadata, c, extra_pixel_error=error) for error in (2., 4.)]
                row.update(status='complete', evaluations=evaluations)
                for sample in evaluations[0]['diagnostic_frames']:
                    f = annotate(frames[sample['frame_index']], sample, metadata)
                    cv2.imwrite(str(directory/f'annotated-{sample["frame_index"]}.png'), f)
                row['seconds'] = round(perf_counter()-ts, 4); save()
                print(json.dumps({'shot': number, 'seconds': row['seconds'], 'evaluations': [{k:v for k,v in e.items() if k not in ('measured_frames', 'diagnostic_frames', 'rejected_frames')} for e in evaluations]}), flush=True)
            except Exception as error:
                row.update(status='failed', error_type=type(error).__name__); save()
                print(json.dumps({'shot': number, 'error_type': type(error).__name__}), flush=True)
    if len(frame_sets) == 2 and all(frame_sets):
        report['temporal_match'] = temporal_match(*frame_sets)
    report['usage'] = {k: sum(r.get('usage', {}).get(k, 0) for r in report['rows']) for k in ('input_tokens', 'output_tokens')}
    report['seconds'] = round(perf_counter()-started, 4)
    report['source_unchanged'] = report['source_sha256'] == sha256(args.source.read_bytes()).hexdigest()
    save()
    print(json.dumps({'seconds': report['seconds'], 'usage': report['usage'], 'source_unchanged': report['source_unchanged'],
                     'temporal_offset_frames': report.get('temporal_match', {}).get('median_frame_offset')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--paid', action='store_true')
    mode.add_argument('--responses', type=Path)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    main(p.parse_args())
