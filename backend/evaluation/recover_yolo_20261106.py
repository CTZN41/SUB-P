"""Replay the recovered pilot training recipe in an isolated ASCII workspace.

This runner never accesses the application database, OpenAI, or email. The
recovered checkpoint is not promoted to the running service by this script.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ['LOCALAPPDATA']) / 'ShotLog' / 'model-recovery' / 'run-20261106'
START = time.perf_counter()
STATE_FILE = ROOT / 'recovery-state.json'


def write_state(state: dict) -> None:
    state['elapsed_seconds'] = round(time.perf_counter() - START, 3)
    temporary = STATE_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(STATE_FILE)


def preserve_input(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size != source.stat().st_size:
            raise RuntimeError(f'Existing recovery input differs: {destination.name}')
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def run_stage(state: dict, name: str, module: str, arguments: list[str], version: str = '191') -> None:
    if state['stages'].get(name, {}).get('status') == 'completed':
        return
    environment = os.environ.copy()
    vendor = BACKEND / 'work' / 'model-recovery' / ('vendor' + version)
    if not (vendor / 'ultralytics').is_dir() or not (vendor / ('ultralytics-8.4.' + version + '.dist-info')).is_dir():
        raise RuntimeError('Install ultralytics==8.4.' + version + ' with --no-deps --target ' + str(vendor)
                           + ' before replaying this historical recipe')
    environment['PYTHONPATH'] = os.pathsep.join((str(vendor), str(BACKEND)))
    environment['YOLO_CONFIG_DIR'] = str(ROOT / ('ultralytics-' + version))
    environment['PYTHONUNBUFFERED'] = '1'
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['WANDB_DISABLED'] = 'true'
    command = [sys.executable, '-u', '-m', module, *arguments]
    log = ROOT / 'logs' / (name + '.log')
    stage = {'status': 'running', 'ultralytics': '8.4.' + version,
             'command': command, 'log': str(log)}
    state['current_stage'] = name
    state['stages'][name] = stage
    write_state(state)
    print(json.dumps({'stage': name, 'status': 'running'}), flush=True)
    started = time.perf_counter()
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT)
    stage.update(status='completed' if result.returncode == 0 else 'failed',
                 seconds=round(time.perf_counter() - started, 3), exit_code=result.returncode)
    write_state(state)
    print(json.dumps({'stage': name, **{k: stage[k] for k in ('status', 'seconds', 'exit_code')}}), flush=True)
    if result.returncode:
        raise RuntimeError(f'{name} failed; inspect its local log')


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / 'logs').mkdir(exist_ok=True)
    state = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {
        'status': 'running', 'stages': {}, 'recipe_source': '2026-10-28 and 2026-10-30 actual training commands',
        'application_modified': False, 'paid_ai_calls': 0,
        'original_v4_sha256': 'B777F7D133D2B50E00B2914DE05D160B14D95639668B20618F74D78EE0CD66D2'}
    for name in ('sample-b.mp4', 'pexels-6249631.mp4', 'pexels-6249343.mp4', 'pexels-35599326.mp4'):
        preserve_input(BACKEND / 'uploads' / 'training-sources' / name, ROOT / 'sources' / name)
    preserve_input(BACKEND / 'models' / 'yolo26n.pt', ROOT / 'models' / 'yolo26n.pt')
    write_state(state)
    datasets = ROOT / 'datasets'
    run_stage(state, 'dataset_v1', 'app.analysis.build_yolo_dataset', [
        'sources/sample-b.mp4', '--start', '575', '--end', '625', '--every', '0.25', '--val-start', '615',
        '--output', str(datasets / 'v1'), '--preview', str(ROOT / 'previews/v1')])
    run_stage(state, 'dataset_v2', 'app.analysis.expand_yolo_dataset', [
        'sources/pexels-6249631.mp4', 'sources/pexels-35599326.mp4',
        '--base', str(datasets / 'v1'), '--output', str(datasets / 'v2'),
        '--every', '0.25', '--imgsz', '960', '--preview', str(ROOT / 'previews/v2')])
    run_stage(state, 'dataset_v3', 'app.analysis.add_hard_negatives', [
        'sources/pexels-6249343.mp4', '--timestamps', '0', '0.5', '1', '1.5', '2', '2.5', '3', '3.5', '4',
        '--base', str(datasets / 'v2'), '--output', str(datasets / 'v3'), '--preview', str(ROOT / 'previews/v3')])
    counts = {}
    for name in ('v1', 'v2', 'v3'):
        data = datasets / name
        counts[name] = {part: len(list((data / 'images' / part).glob('*.jpg'))) for part in ('train', 'val')}
        if not all(counts[name].values()):
            raise RuntimeError(f'No images in recovery dataset {name}')
    state['dataset_image_counts'] = counts
    write_state(state)
    print(json.dumps({'dataset_image_counts': counts}), flush=True)
    previous = ROOT / 'models/yolo26n.pt'
    for name, data_name, epochs, vendor in (
        ('v1', 'v1', 5, '191'), ('v2', 'v2', 5, '191'),
        ('v3', 'v3', 3, '191'), ('v4', 'v3', 3, '192')):
        args = ['--data', str(datasets / data_name / 'data.yaml'), '--model', str(previous),
                '--epochs', str(epochs), '--imgsz', '640', '--batch', '4', '--name', 'recovered-' + name]
        if name == 'v4':
            args += ['--degrees', '4', '--translate', '0.18', '--scale', '0.6', '--perspective', '0.0008',
                     '--fliplr', '0.5', '--lr0', '0.001', '--lrf', '0.1', '--mosaic', '0.5', '--optimizer', 'SGD']
        run_stage(state, 'train_' + name, 'app.analysis.train_yolo', args, vendor)
        previous = ROOT / 'work/training' / ('recovered-' + name) / 'weights/best.pt'
        if not previous.is_file():
            raise RuntimeError('Training did not produce a checkpoint')
    state.update(status='trained_awaiting_validation', current_stage='validation_required', checkpoint=str(previous))
    write_state(state)
    print(json.dumps({'status': state['status'], 'checkpoint': str(previous)}), flush=True)


if __name__ == '__main__':
    main()
