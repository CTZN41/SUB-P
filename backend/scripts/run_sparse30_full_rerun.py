"""One explicitly authorized full rerun, using the same worker as localhost.

Copies only the selected source analysis' video/settings into a new owned
record. No auth setting, token, old shot or old work result is changed.
Launch once; the manifest/lock prevents accidental duplicate paid reruns.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic, perf_counter
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import UploadFile
from sqlalchemy import select
from app.api.analyses import ensure_analysis_ready
from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models import Analysis, AnalysisJob, CourtCalibration, Player, Shot
from app.services.analysis_jobs import ACTIVE_JOB_STATUSES, start_phase7, run_phase7_job
from app.services.video_registration import register_video


def shot_fingerprint(session, analysis_id):
    shots = session.scalars(select(Shot).where(Shot.analysis_id == analysis_id).order_by(Shot.id)).all()
    values = [[s.id,s.candidate_time_ms,s.court_x,s.court_y,s.zone_code,s.result.value,
               s.shot_type.value,s.status.value] for s in shots]
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def main():
    settings = get_settings()
    if not (settings.openai_shot_review_enabled and settings.openai_api_key
            and 'terra' in settings.openai_vision_model.lower()):
        raise RuntimeError('Configured Terra full review is required')
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    work = settings.work_path/'verification-2026-11-04/sparse-30-full-rerun'
    work.mkdir(parents=True,exist_ok=True)
    lock = work/'launch.lock'
    try:
        descriptor=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    except FileExistsError:
        print('Existing launch lock: no new analysis or paid job created.',flush=True)
        return
    os.close(descriptor)
    manifest_path=work/'manifest.json'
    manifest={'source_analysis_id':'cb49b783-591d-4999-a131-286d6ee80b54',
              'worker_pid':os.getpid(),'phase':'preparing',
              'execution_path':'trusted_local_launcher_same_localhost_worker',
              'model':settings.openai_vision_model,'runtime':settings.candidate_runtime}
    def save(): manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    try:
        registration_started=perf_counter()
        with factory() as session:
            if session.scalar(select(AnalysisJob.id).where(AnalysisJob.status.in_(ACTIVE_JOB_STATUSES)).limit(1)):
                raise RuntimeError('Another analysis is active; no extra worker started')
            source=session.get(Analysis,manifest['source_analysis_id'])
            if source is None or not source.owner_id:
                raise RuntimeError('Owned source analysis required')
            calibrations=list(session.scalars(select(CourtCalibration).where(CourtCalibration.analysis_id==source.id).order_by(CourtCalibration.frame_time_ms)))
            players=list(session.scalars(select(Player).where(Player.analysis_id==source.id)))
            if not calibrations or len(players)!=1 or not source.warnings_accepted:
                raise RuntimeError('Confirmed source setup required')
            source_result=settings.work_path/source.id/'phase7-candidates.json'
            manifest['source_shots_fingerprint']=shot_fingerprint(session,source.id)
            manifest['source_result_sha256']=hashlib.sha256(source_result.read_bytes()).hexdigest()
            session.info.update(auth_required=True,auth_user_id=source.owner_id)
            upload=UploadFile(filename=source.original_name,file=(settings.upload_path/source.id/'original.mp4').open('rb'))
            analysis=asyncio.run(register_video(session,settings,title='11/04 30슛 개선판 전체 재시험',
                play_type=source.play_type,recorded_date=source.recorded_date,player_name=None,
                contains_others=source.contains_others,consent_confirmed=source.consent_confirmed,
                ai_notice_confirmed=source.ai_notice_confirmed,upload=upload))
            manifest['analysis_id']=analysis.id
            analysis.warnings_accepted=source.warnings_accepted
            for old in calibrations:
                session.add(CourtCalibration(analysis_id=analysis.id,frame_time_ms=old.frame_time_ms,
                    image_points=deepcopy(old.image_points),court_points=deepcopy(old.court_points),
                    hoop_point=deepcopy(old.hoop_point),three_point_line=deepcopy(old.three_point_line),
                    transform=deepcopy(old.transform),source=old.source,confirmed_at=old.confirmed_at))
            new_player=session.scalar(select(Player).where(Player.analysis_id==analysis.id))
            for key in ('display_name','jersey_number','is_temporary_name','initial_time_ms','initial_box'):
                setattr(new_player,key,deepcopy(getattr(players[0],key)))
            session.commit()
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
            ensure_analysis_ready(request,session,analysis)
            manifest['registration_seconds']=round(perf_counter()-registration_started,3)
            manifest['calibration_count']=len(calibrations)
            job,created=start_phase7(session,analysis,settings)
            if not created:
                raise RuntimeError('Job already exists; refused duplicate worker')
            manifest.update(job_id=job.id,started_at=job.started_at.isoformat(),phase='running',
                            started_monotonic=monotonic(),result_url='http://127.0.0.1:5173/results/'+analysis.id)
            save()
            print(json.dumps({k:manifest[k] for k in ('analysis_id','job_id','result_url','calibration_count','registration_seconds')}),flush=True)
        started=perf_counter()
        run_phase7_job(factory,settings,manifest['analysis_id'],manifest['job_id'])
        manifest['worker_elapsed_seconds']=round(perf_counter()-started,3)
        with factory() as session:
            job=session.get(AnalysisJob,manifest['job_id'])
            manifest['phase']=job.status.value
            manifest['finished_at']=job.finished_at.isoformat() if job.finished_at else None
            manifest['source_shots_unchanged']=shot_fingerprint(session,manifest['source_analysis_id'])==manifest['source_shots_fingerprint']
            manifest['source_result_unchanged']=hashlib.sha256(source_result.read_bytes()).hexdigest()==manifest['source_result_sha256']
        save()
        print(json.dumps({k:manifest.get(k) for k in ('phase','worker_elapsed_seconds','source_shots_unchanged','source_result_unchanged')}),flush=True)
    except Exception as error:
        manifest.update(phase='launcher_failed',error_type=type(error).__name__)
        save()
        print('full_rerun_launcher_failed:',type(error).__name__,flush=True)
        sys.exit(1)
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
