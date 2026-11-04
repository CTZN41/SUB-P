"""Offline timing comparison against an existing source. No AI or DB writes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analysis.phase7_pipeline import analyze_video, Phase7Error
from app.analysis.model_reuse_trial import ModelReuseTrial
from app.analysis.candidate_runtime import candidate_models
from app.core.config import get_settings
from app.db.session import create_database_engine
from app.models import CourtCalibration
from sqlalchemy import select
from sqlalchemy.orm import Session


def clean(value):
    return {key: clean(item) for key, item in value.items() if key != 'performance'} if isinstance(value, dict) else [clean(v) for v in value] if isinstance(value, list) else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('analysis_id')
    parser.add_argument('--seconds', type=int, default=8)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    with Session(engine.execution_options(postgresql_readonly=True)) as db:
        calibration = db.scalar(select(CourtCalibration).where(CourtCalibration.analysis_id == args.analysis_id).order_by(CourtCalibration.frame_time_ms))
        hoop = list(calibration.hoop_point)
    engine.dispose()
    video = settings.upload_path / args.analysis_id / 'original.mp4'
    reference = json.loads((settings.work_path / args.analysis_id / 'phase7-candidates.json').read_text(encoding='utf-8'))
    target_reference = min(reference['candidates'], key=lambda c: abs(c['time_ms'] - (args.start*1000+5000)))
    model, pose, device = candidate_models(settings)
    runs, evidence = [], []
    # Warm model initialization separately from the measured repeated work.
    models = ModelReuseTrial()
    try:
        analyze_video(video, model, hoop, pose_model_path=pose,
                      start_ms=args.start*1000, end_ms=args.start*1000+200,
                      inference_device=device, model_reuse=models)
    except Phase7Error:
        pass  # A warmup frame need not contain a complete shot or ball track.
    for enabled in (False, True):
        models.predictions.clear(); models.prediction_bytes = 0
        models.cache_stats = {'hits': 0, 'misses': 0, 'evictions': 0}
        models.max_prediction_bytes = 64*1024**2 if enabled else 0
        outputs, stages = [], []
        for name, start, end, fps in (
            ('base', 0, args.seconds*1000, 15),
            ('rescan', 3000, args.seconds*1000, 30),
            ('foot_refresh', 3000, 6000, 15)):
            targets = [{'time_ms': target_reference['time_ms'], 'foot_point_px': target_reference['foot_point_px'], 'scene_id': 0}]
            started = perf_counter()
            result = analyze_video(video, model, hoop, pose_model_path=pose,
                start_ms=args.start*1000+start, end_ms=args.start*1000+end,
                sample_fps=fps, foot_track_targets=targets,
                inference_device=device, prefetch_frames=True, model_reuse=models)
            stages.append({'stage': name, 'seconds': round(perf_counter()-started, 4),
                           'sampled_frames': result['sampled_frames'],
                           'foot_track_samples': len(targets[0].get('foot_track', []))})
            outputs.append({'result': clean(result), 'targets': deepcopy(targets)})
        evidence.append(outputs)
        runs.append({'enabled': enabled, 'stages': stages,
                     'seconds': round(sum(s['seconds'] for s in stages), 4),
                     'cache': dict(models.cache_stats), 'cache_bytes': models.prediction_bytes})
        print(json.dumps(runs[-1]), flush=True)
    report = {'analysis_id': args.analysis_id, 'source_seconds': args.seconds,
              'source_start_seconds': args.start,
              'runs': runs, 'exact_output_match': evidence[0] == evidence[1],
              'speedup_percent': round((1-runs[1]['seconds']/runs[0]['seconds'])*100, 2),
              'paid_api_calls': 0, 'scope': 'same native frames, full candidate and foot-track outputs; not independent accuracy'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'runs'}), flush=True)
    if not report['exact_output_match']:
        raise RuntimeError('Cached observations changed analysis output')


if __name__ == '__main__':
    main()
