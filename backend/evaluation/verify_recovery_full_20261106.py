"""User-authorized two-video full AI verification after model recovery.

Only new test analyses are written. Existing analyses are fingerprinted and
preserved. Paid analysis/report calls are explicit; no email is sent.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter, sleep

from fastapi import UploadFile
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models import Analysis, AnalysisJob, CourtCalibration, CalibrationSource, PlayType
from app.services.video_registration import register_video
from app.services.court_calibration import calculate_transform, validate_points
from app.services.analysis_jobs import start_phase7, run_phase7_job, ACTIVE_JOB_STATUSES
from app.services.practice_report import comparison_context, generate_report, ReportError
from app.services.practice_report_pdf import build_practice_pdf
from app.services.shot_statistics import calculate_statistics


ROOT = Path(os.environ['LOCALAPPDATA']) / 'ShotLog/model-recovery/run-20261106/verification'
MANIFEST = ROOT / 'full-analysis-state.json'
OUTPUT = Path('C:/Users/aicam/Desktop/사이드 프로젝트/모델 복구 검증 결과')


def save(state):
    temporary = MANIFEST.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(MANIFEST)


def snapshot(analysis):
    return {'id': analysis.id, 'title': analysis.title, 'original_name': analysis.original_name,
            'status': analysis.status.value,
            'shots': sorted([{'id': shot.id, 'time_ms': shot.candidate_time_ms,
                              'result': shot.result.value, 'type': shot.shot_type.value,
                              'position': [shot.court_x, shot.court_y], 'zone': shot.zone_code,
                              'status': shot.status.value} for shot in analysis.shots], key=lambda s: s['id'])}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    factory = create_session_factory(create_database_engine(settings.database_url))
    calibrations = json.loads((ROOT / 'calibration-inputs.json').read_text(encoding='utf-8'))
    sources = [
        ('outdoor', Path('C:/Users/aicam/Desktop/사이드 프로젝트/발표 영상/original-made-and-missed.mp4'),
         '복구 검증 · 성공·실패 10초', 1000),
        ('indoor', Path('C:/Users/aicam/Downloads/ShotLog-test-60s-indoor-layup-midrange-1080p.mp4'),
         '복구 검증 · 실내 60초', 20000),
    ]
    state = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'tests': {}}
    with factory() as session:
        if 'baseline' not in state:
            existing = list(session.scalars(select(Analysis).order_by(Analysis.created_at)))
            owners = {a.owner_id for a in existing}
            if len(owners) != 1 or None in owners:
                raise RuntimeError('A unique existing test owner must be selected explicitly')
            active = session.scalar(select(AnalysisJob.id).where(AnalysisJob.status.in_(ACTIVE_JOB_STATUSES)))
            if active:
                raise RuntimeError('Another analysis is active; do not compete with it')
            state['owner_id'] = owners.pop()
            state['baseline'] = [snapshot(a) for a in existing]
            state['smtp_sends'] = 0
            state['started_at'] = datetime.now(timezone.utc).isoformat()
            save(state)
        session.info.update(auth_user_id=state['owner_id'], auth_required=True)
        for name, path, title, frame_ms in sources:
            if name in state['tests']:
                continue
            with path.open('rb') as stream:
                upload = UploadFile(filename=path.name, file=stream)
                analysis = asyncio.run(register_video(
                    session, settings, title=title, play_type=PlayType.SOLO, recorded_date=date(2026, 11, 6),
                    player_name=None, contains_others=False, consent_confirmed=False,
                    ai_notice_confirmed=True, upload=upload))
            points = calibrations[name]
            validate_points(points['image_points'], points['court_points'], points['hoop_point'],
                            analysis.width, analysis.height)
            calibration = CourtCalibration(analysis_id=analysis.id, frame_time_ms=frame_ms,
                image_points=points['image_points'], court_points=points['court_points'],
                hoop_point=points['hoop_point'], three_point_line=None,
                transform=calculate_transform(points['image_points'], points['court_points']),
                source=CalibrationSource.USER, confirmed_at=datetime.now(timezone.utc))
            # Existing user-approved points were recovered and visually overlaid
            # on this exact framing; this is not an automatic court suggestion.
            session.add(calibration)
            analysis.warnings_accepted = True
            session.commit()
            state['tests'][name] = {'analysis_id': analysis.id, 'title': title,
                'source': str(path), 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'calibration_provenance': points['source_analysis_id'], 'status': 'registered'}
            save(state)
            print(json.dumps({'test': name, 'registered': analysis.id}, ensure_ascii=False), flush=True)
    for name, _, _, _ in sources:
        test = state['tests'][name]
        if test.get('status') == 'verified':
            continue
        analysis_id = test['analysis_id']
        with factory() as session:
            analysis = session.get(Analysis, analysis_id)
            if 'job_id' not in test:
                job, created = start_phase7(session, analysis, settings)
                if not created:
                    raise RuntimeError('Unexpected existing active job')
                test.update(job_id=job.id, status='running')
                save(state)
            else:
                job = session.get(AnalysisJob, test['job_id'])
                if job.status.value != 'succeeded':
                    raise RuntimeError('Existing job needs explicit review; refusing duplicate paid run')
                created = False
        started = perf_counter()
        if created:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_phase7_job, factory, settings, analysis_id, test['job_id'])
                last = None
                while not future.done():
                    with factory() as session:
                        job = session.get(AnalysisJob, test['job_id'])
                        current = (job.status.value, job.stage, job.processed_ms)
                    if current != last:
                        test['progress'] = {'status': current[0], 'stage': current[1], 'processed_ms': current[2],
                                            'elapsed_seconds': round(perf_counter() - started, 2)}
                        save(state)
                        print(json.dumps({'test': name, **test['progress']}, ensure_ascii=False), flush=True)
                        last = current
                    sleep(10)
                future.result()
        with factory() as session:
            analysis = session.get(Analysis, analysis_id)
            job = session.get(AnalysisJob, test['job_id'])
            test.update(status=job.status.value, wall_seconds=round(perf_counter() - started, 3))
            if job.finished_at and job.started_at:
                test['job_seconds'] = round((job.finished_at - job.started_at).total_seconds(), 3)
            test['saved'] = snapshot(analysis)
            test['statistics'] = calculate_statistics(analysis.shots)
            if job.status.value != 'succeeded':
                test['failure_code'] = analysis.failure_code
                save(state)
                raise RuntimeError(f'{name} analysis did not succeed; inspect safe failure code')
            result_path = settings.work_path / analysis_id / 'phase7-candidates.json'
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding='utf-8'))
                test['api_usage'] = result.get('api_usage')
                test['timing_seconds'] = result.get('timing_seconds')
                test['performance'] = result.get('performance')
                test['review_status'] = result.get('ai_review', {}).get('status')
            context = comparison_context(session, analysis)
            test['report_ready'] = context['ready']
            if context['ready']:
                try:
                    view = generate_report(session, analysis, settings)
                    pdf = build_practice_pdf(analysis, context, view['report'], settings)
                    destination = OUTPUT / f'{name}-practice-report.pdf'
                    destination.write_bytes(pdf)
                    test['pdf_path'] = str(destination)
                    test['pdf_bytes'] = len(pdf)
                    test['report_api_usage'] = view['report'].get('api_usage')
                except ReportError as error:
                    test['report_error'] = error.code
            else:
                try:
                    generate_report(session, analysis, settings)
                except ReportError as error:
                    test['blocked_report_code'] = error.code
                else:
                    raise RuntimeError('Unresolved analysis unexpectedly allowed a report')
            test['status'] = 'verified'
            save(state)
            print(json.dumps({'test': name, 'analysis_id': analysis_id, 'status': test['status'],
                'job_seconds': test.get('job_seconds'), 'report_ready': test['report_ready'],
                'pdf_path': test.get('pdf_path'), 'shots': len(analysis.shots)}, ensure_ascii=False), flush=True)
    with factory() as session:
        unchanged = all(snapshot(session.get(Analysis, old['id'])) == old for old in state['baseline'])
        state['baseline_preserved'] = unchanged
        save(state)
        if not unchanged:
            raise RuntimeError('Existing records changed during verification; review user activity')


if __name__ == '__main__':
    main()
