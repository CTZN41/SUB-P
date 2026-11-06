"""Local recovery checks: no application database, external AI, or email.

Historical frame counts used the earlier candidate sampling scheme. Counts
below at 4 fps are observational diagnostics, not object detection accuracy.
Only the CSV's human release labels are used for candidate recall checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from ultralytics import YOLO  # Load Torch before OpenCV on Windows.
import cv2
import numpy as np

from app.analysis.phase7_pipeline import analyze_video
from app.analysis.adaptive_trial import add_focused_candidates


BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ['LOCALAPPDATA']) / 'ShotLog/model-recovery/run-20261106'


def cases():
    temporary = Path(os.environ['TEMP'])
    return [
        ('solo22', ROOT / 'validation-inputs/solo22.mp4', [1669.95, 149.09], [4.4, 18.933]),
        ('mixkit44448', temporary / 'subp-candidate-44448.mp4', [250, 150], [4.4]),
        ('mixkit2278', temporary / 'subp-candidate-2278.mp4', [640, 20], [3.6, 11.2]),
        ('pexels5191594', temporary / 'subp-candidate-5191594.mp4', [600, 140], [14.3]),
        ('pexels33972086', temporary / 'subp-candidate-33972086.mp4', [950, 350], None),
    ]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def objects(model, source, directory, device):
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError('Cannot decode held-out clip')
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps / 4))
    names = {int(k): v for k, v in model.names.items()}
    counts = {name: 0 for name in names.values()}
    records = []
    preview = []
    start = perf_counter()
    try:
        for frame_index in range(0, frame_count, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok:
                break
            result = model.predict(frame, imgsz=960, conf=.15, device=device, verbose=False)[0]
            present = {names[int(box.cls.item())] for box in result.boxes}
            for name in present:
                counts[name] += 1
            records.append({'seconds': round(frame_index / fps, 3), 'classes': sorted(present)})
            # Three real predictions spread over the clip, without altering boxes.
            if len(preview) < 3 and frame_index >= len(preview) * frame_count / 3:
                plotted = cv2.resize(result.plot(), (640, 360))
                cv2.putText(plotted, f'{frame_index / fps:.2f}s', (12, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
                preview.append(plotted)
    finally:
        cap.release()
    directory.mkdir(parents=True, exist_ok=True)
    if preview:
        cv2.imwrite(str(directory / 'preview.jpg'), np.vstack(preview))
    return {'sampled_frames': len(records), 'frames_with_class': counts,
            'sample_fps': 4, 'imgsz': 960, 'confidence': .15,
            'seconds': round(perf_counter() - start, 3), 'records': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=['torch', 'openvino'], default='torch')
    parser.add_argument('--internal', action='store_true')
    parser.add_argument('--objects', action='store_true')
    parser.add_argument('--candidates', action='store_true')
    args = parser.parse_args()
    device = 'cpu' if args.runtime == 'torch' else 'intel:cpu'
    checkpoint = ROOT / 'work/training/recovered-v4/weights/best.pt'
    model_path = checkpoint if args.runtime == 'torch' else checkpoint.parent / 'best_openvino_model'
    pose = BACKEND / ('models/yolo26n-pose.pt' if args.runtime == 'torch'
                      else 'models/yolo26n-pose_openvino_model')
    output = ROOT / 'validation' / args.runtime
    output.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(model_path), task='detect')
    names = {int(k): v for k, v in model.names.items()}
    if names != {0: 'person', 1: 'basketball', 2: 'rim'}:
        raise RuntimeError(f'Unexpected checkpoint classes: {names}')
    report = {'runtime': args.runtime, 'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              'classes': names, 'paid_ai_calls': 0, 'database_writes': 0, 'cases': [],
              'limitations': ['Small, previously used held-out clips; not general accuracy.',
                              'Internal validation shares source with training.',
                              'Object visibility counts are not precision/recall.',
                              'No made/missed, shot type, or court location AI is called.']}
    if args.internal:
        result = model.val(data=str(ROOT / 'datasets/v3/data.yaml'), imgsz=640, batch=4,
                           device='cpu', workers=0, plots=False, verbose=False,
                           project=str(output), name='internal', exist_ok=True)
        box = result.box
        report['internal'] = {'map50': float(box.map50), 'map50_95': float(box.map),
                              'per_class': {names[int(c)]: {'map50': float(box.ap50[i]),
                                                          'precision': float(box.p[i]),
                                                          'recall': float(box.r[i])}
                                            for i, c in enumerate(box.ap_class_index)}}
        print(json.dumps({'internal': report['internal']}), flush=True)
        write_json(output / 'report.json', report)
    for name, source, hoop, releases in cases():
        case = {'clip': name, 'source': str(source), 'human_release_seconds': releases}
        if not source.is_file():
            case['missing_input'] = True
        else:
            if args.objects:
                case['objects'] = objects(model, source, output / name, device)
            if args.candidates:
                started = perf_counter()
                try:
                    base = analyze_video(source, model_path, hoop, pose_model_path=pose,
                                         collect_motion_hints=True, inference_device=device)
                    case['base_times_seconds'] = [round(c['time_ms'] / 1000, 3) for c in base['candidates']]
                    final = add_focused_candidates(source, model_path, hoop, pose, base,
                                                   inference_device=device)
                    times = [round(c['time_ms'] / 1000, 3) for c in final['candidates']]
                    case['candidate_times_seconds'] = times
                    case['candidates_seconds'] = round(perf_counter() - started, 3)
                    # Greedy one-to-one nearest pairing; each candidate counted once.
                    available = list(times)
                    matches = []
                    for release in releases or []:
                        near = min(available, key=lambda value: abs(value - release)) if available else None
                        matched = near is not None and abs(near - release) <= 1.5
                        matches.append({'release': release, 'candidate': near, 'matched_1_5s': matched})
                        if matched:
                            available.remove(near)
                    case['matches'] = matches
                    write_json(output / name / 'pipeline.json', final)
                except Exception as exc:
                    case['candidate_error'] = {'type': type(exc).__name__, 'message': str(exc)[:220]}
        report['cases'].append(case)
        write_json(output / 'report.json', report)
        printable = {k: v for k, v in case.items() if k not in {'source', 'objects'}}
        if 'objects' in case:
            printable['objects'] = {k: v for k, v in case['objects'].items() if k != 'records'}
        print(json.dumps(printable, ensure_ascii=False), flush=True)
    print(json.dumps({'report': str(output / 'report.json')}), flush=True)


if __name__ == '__main__':
    main()
