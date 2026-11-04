"""실제 분석기를 붙이기 전 작업 상태 흐름을 검증하는 개발용 작업."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from time import perf_counter
from threading import Lock

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Analysis, AnalysisJob, AnalysisStatus, CourtCalibration, JobStatus, Shot
from app.analysis.phase7_pipeline import Phase7Canceled, Phase7Error, analyze_video, save_result
from app.core.config import Settings
from app.services.openai_shot_review import review_candidates_with_openai
from app.services.candidate_enrichment import enrich_candidates
from app.services.phase8_shots import materialize_shots_from_candidates
from app.services.job_progress import JobProgressReporter
from app.analysis.candidate_runtime import candidate_models
from app.analysis.model_reuse_trial import ModelReuseTrial
from app.services.async_progress_trial import AsyncProgressTrial
from app.services.view_alignment_trial import group_camera_views
from app.services.non_shot_recovery import separate_recovery_motion
from app.services.analysis_reuse import AnalysisReuse
from app.services.non_shot_policy import exclude_confirmed_non_shots


STAGES = [
    "영상 준비",
    "코트와 골대 확인",
    "선수·공 추적",
    "슛 후보 찾기",
    "성공·실패와 슛 종류 판단",
    "결과 계산",
    "완료",
]
ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED)
SHOT_DETECTION_MODEL = "models/basketball-yolo26n-pilot-v4.pt"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def current_job(session: Session, analysis_id: str) -> AnalysisJob | None:
    return session.scalar(
        select(AnalysisJob)
        .where(AnalysisJob.analysis_id == analysis_id)
        .order_by(AnalysisJob.started_at.desc(), AnalysisJob.id.desc())
    )


def active_job(session: Session, analysis_id: str) -> AnalysisJob | None:
    return session.scalar(
        select(AnalysisJob).where(
            AnalysisJob.analysis_id == analysis_id,
            AnalysisJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )


def start_simulation(session: Session, analysis: Analysis) -> tuple[AnalysisJob, bool]:
    existing = active_job(session, analysis.id)
    if existing:
        return existing, False
    now = utc_now()
    job = AnalysisJob(
        analysis_id=analysis.id,
        status=JobStatus.QUEUED,
        stage=STAGES[0],
        processed_ms=0,
        is_simulation=True,
        started_at=now,
        heartbeat_at=now,
    )
    analysis.status = AnalysisStatus.QUEUED
    analysis.current_stage = STAGES[0]
    analysis.processed_ms = 0
    analysis.failure_code = None
    analysis.failure_message = None
    session.add(job); session.commit(); session.refresh(job)
    return job, True


def start_phase7(session: Session, analysis: Analysis, settings: Settings) -> tuple[AnalysisJob, bool]:
    """Create one durable real-analysis job; worker runs after the HTTP response."""
    existing = active_job(session, analysis.id)
    if existing:
        return existing, False
    if analysis.play_type.value != "solo":
        raise Phase7Error("Phase 7 실제 후보 분석은 개인 연습 영상만 지원합니다.")
    model_path, _, _ = candidate_models(settings)
    if not model_path.exists():
        raise Phase7Error("학습된 탐지 모델을 찾을 수 없습니다.")
    now = utc_now()
    job = AnalysisJob(analysis_id=analysis.id, status=JobStatus.QUEUED, stage=STAGES[0],
                      processed_ms=0, is_simulation=False, started_at=now, heartbeat_at=now)
    analysis.status = AnalysisStatus.QUEUED
    analysis.current_stage = STAGES[0]
    analysis.processed_ms = 0
    analysis.failure_code = analysis.failure_message = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job, True


def phase7_result_path(settings: Settings, analysis_id: str) -> Path:
    path = (settings.work_path / analysis_id / "phase7-candidates.json").resolve()
    if settings.work_path.resolve() not in path.parents:
        raise Phase7Error("결과 파일 경로가 올바르지 않습니다.")
    return path


def read_phase7_result(settings: Settings, analysis_id: str) -> dict | None:
    path = phase7_result_path(settings, analysis_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def run_phase7_job(session_factory, settings: Settings, analysis_id: str, job_id: str) -> None:
    """Process locally with short DB sessions so cancellation can be observed."""
    result_path = phase7_result_path(settings, analysis_id)
    result_path.unlink(missing_ok=True)
    with session_factory() as session:
        job = session.get(AnalysisJob, job_id)
        analysis = session.get(Analysis, analysis_id)
        calibrations = list(session.scalars(select(CourtCalibration).where(CourtCalibration.analysis_id == analysis_id).order_by(CourtCalibration.frame_time_ms)))
        calibration = calibrations[0] if calibrations else None
        if not job or not analysis or not calibration:
            return
        job.status = JobStatus.RUNNING
        job.stage = STAGES[2]
        job.heartbeat_at = utc_now()
        analysis.status = AnalysisStatus.ANALYZING
        analysis.current_stage = job.stage
        hoop_point = list(calibration.hoop_point)
        calibration_transform = [list(row) for row in calibration.transform]
        # Legacy arc metadata is retained in storage; live analysis uses only
        # user-confirmed paint corners and hoop, plus the validated view guard.
        views = [{"frame_time_ms":c.frame_time_ms,"transform":c.transform,"image_points":c.image_points} for c in calibrations]
        calibration_payload = {**views[0], "views":views}
        duration_ms = int(analysis.duration_ms)
        session.commit()

    reporter = JobProgressReporter(session_factory, analysis_id, job_id, STAGES[3])
    cancelled, progress = reporter.cancelled, reporter.progress

    api_client = None
    try:
        video_path = settings.upload_path / analysis_id / "original.mp4"
        model_path, pose_model_path, inference_device = candidate_models(settings)
        models = ModelReuseTrial()
        review_settings = settings.model_copy(update={"openai_vision_fallback_model":None})
        reuse = AnalysisReuse.for_job(settings.work_path / analysis_id, video_path,
                                     [model_path, pose_model_path], calibration_payload, review_settings)
        if settings.openai_api_key and settings.openai_shot_review_enabled:
            from openai import OpenAI
            api_client = OpenAI(api_key=settings.openai_api_key,
                                timeout=settings.openai_timeout_seconds,max_retries=0)
        review_client = reuse.client(api_client) if api_client is not None else None
        started = perf_counter()
        with AsyncProgressTrial(reporter) as monitor:
            result = reuse.load_stage('local_candidates')
            if result is None:
                result = analyze_video(video_path, model_path, hoop_point,
                                       pose_model_path=pose_model_path,
                                       progress=monitor.progress, cancelled=monitor.cancelled,
                                       collect_motion_hints=True, inference_device=inference_device,
                                       prefetch_frames=True, model_reuse=models)
                reuse.save_stage('local_candidates', result)
            # Preserve completed local evidence before a later DB/API failure.
            # This is not published as a completed Phase 7/8 result.
            save_result(settings.work_path, analysis_id, {'job_id':job_id,'stage':'local_candidates','result':result},
                        filename=f'local-candidates-{job_id}.json')
        local_seconds = perf_counter() - started
        reporter.stage = STAGES[4] + " · 슛 여부 확인·추가 재탐지"
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            analysis = session.get(Analysis, analysis_id)
            if job is None or analysis is None or job.cancel_requested:
                raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            job.stage = analysis.current_stage = reporter.stage
            job.processed_ms = analysis.processed_ms = duration_ms
            job.heartbeat_at = utc_now()
            session.commit()
        started = perf_counter()
        with AsyncProgressTrial(reporter) as monitor:
            monitor.progress(duration_ms)
            def gate_progress(done,total):
                reporter.stage = STAGES[4] + f" · 슛 여부 확인 {done}/{total}"
                if monitor.cancelled():
                    raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            def candidate_activity(label, done, total):
                count = f'{done}/{total}' if total is not None else str(done)
                reporter.stage = f'후보 판단 · {label} {count}'
            enriched = reuse.load_stage('enriched_candidates')
            if enriched is None:
                result = enrich_candidates(video_path, model_path, pose_model_path,
                                           hoop_point, result, review_settings, cancelled=monitor.cancelled,
                                           inference_device=inference_device,prefetch_frames=True,
                                           model_reuse=models,gate_workers=2,gate_client=review_client,
                                           gate_progress=gate_progress,activity_progress=candidate_activity)
                reuse.save_stage('enriched_candidates', result)
            else:
                result = enriched
        enrichment_seconds = perf_counter() - started
        started = perf_counter()
        view_calibrations = None
        reporter.stage = STAGES[4] + " · 카메라 구도 확인"
        if settings.openai_shot_review_enabled and settings.openai_api_key:
            with AsyncProgressTrial(reporter) as monitor:
                monitor.progress(duration_ms)
                camera_stage = reuse.load_stage('camera_views')
                if camera_stage is None:
                    view_calibrations,view_reports = group_camera_views(video_path,result["candidates"],calibration_payload)
                    reuse.save_stage('camera_views', {'transforms': view_calibrations,
                        'reports': view_reports, 'candidates': result['candidates']})
                else:
                    view_calibrations = {int(k): v for k, v in camera_stage['transforms'].items()}
                    view_reports = camera_stage['reports']
                    result['candidates'] = camera_stage['candidates']
                result["camera_view_validation"] = view_reports
                if monitor.cancelled():
                    raise Phase7Canceled("사용자가 분석을 취소했습니다.")
        camera_seconds = perf_counter()-started
        started = perf_counter()
        reporter.stage = STAGES[4] + " · 상세 AI 판정"
        with AsyncProgressTrial(reporter) as monitor:
            monitor.progress(duration_ms)
            detail_lock = Lock()
            detail_counts = {'done': 0, 'total': min(len(result['candidates']), review_settings.openai_max_candidate_reviews)}
            active_detail = {}
            activity_labels = {
                'prepare_images': '장면 준비', 'prepare_shoes': '장면 준비',
                'prepare_outcome_images': '장면 준비', 'shot_review': '기본 판정',
                'camera_context': '위치 재검토', 'visual_arc': '위치 재검토',
                'position_review': '위치 재검토', 'sole_contact_pixels': '위치 재검토',
                'outcome_review': '득점 재검토',
            }
            def publish_detail_status():
                labels = list(dict.fromkeys(active_detail.values()))
                reporter.stage = f"심층 분석 {detail_counts['done']}/{detail_counts['total']}"
                if labels:
                    reporter.stage += ' · ' + '·'.join(labels)
            def detail_activity(candidate_key, task):
                with detail_lock:
                    if task is None:
                        active_detail.pop(candidate_key, None)
                    else:
                        active_detail[candidate_key] = activity_labels.get(task, '기본 판정')
                    publish_detail_status()
            def detail_progress(done,total):
                with detail_lock:
                    detail_counts.update(done=done, total=total)
                    publish_detail_status()
                if monitor.cancelled():
                    raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            result = review_candidates_with_openai(
                video_path,result,review_settings,client=review_client,calibration_transform=calibration_transform,
                camera_view_calibrations=view_calibrations,detail_workers=2,
                fast_image_sampling=True,require_outcome_evidence=True,scoring_rim_evidence=True,
                review_progress=detail_progress,
                review_activity=detail_activity,
            )
        result["timing_seconds"] = {
            "local_candidates":round(local_seconds,2),
            "focused_rescan_and_shot_gate":round(enrichment_seconds,2),
            "camera_view_validation":round(camera_seconds,2),
            "detailed_ai_review":round(perf_counter()-started,2),
        }
        result["runtime_options"] = {"gate_workers":2,"detail_workers":2,"prefetch":True,
                                     "model_reuse":True,"async_status":True,"scoring_rim_evidence":True,
                                     "camera_view_guard":True}
        result['reuse'] = {**reuse.stats, 'inference': dict(models.cache_stats),
                           'inference_cache_bytes': models.prediction_bytes}
        result["status_io"] = dict(reporter.stats)
        result["api_usage"] = {
            key: int(reuse.stats[key])
            for key in ("input_tokens", "output_tokens")
        }
        result['api_usage_scope'] = 'new_calls_in_this_run; reused responses are not billed again; timeout usage may be unavailable'
        result['api_usage_complete'] = reuse.stats['api_errors'] == 0
        result = separate_recovery_motion(result)
        result = exclude_confirmed_non_shots(result)
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            if job is None or job.cancel_requested:
                raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            analysis = session.get(Analysis, analysis_id)
            calibration = session.scalar(
                select(CourtCalibration).where(CourtCalibration.analysis_id == analysis_id)
            )
            if analysis is None or calibration is None:
                raise Phase7Error("분석 또는 코트 위치 정보를 찾을 수 없습니다.")
            job.stage = analysis.current_stage = STAGES[4]
            # Replace the previous run atomically with this run's shots. A failed
            # retry keeps the old data until a complete result can be committed.
            session.execute(delete(Shot).where(Shot.analysis_id == analysis_id))
            session.flush()
            materialize_shots_from_candidates(
                session, analysis, calibration, result
            )
            ai_state = result.get("ai_review", {}).get("status")
            if ai_state in ("partial", "not_configured"):
                analysis.failure_code = "OPENAI_PARTIAL"
                analysis.failure_message = "외부 AI 판정을 완료하지 못한 슛은 확인 필요로 보존했습니다."
            else:
                analysis.failure_code = analysis.failure_message = None
            save_result(settings.work_path, analysis_id, result)
            job.status = JobStatus.SUCCEEDED
            job.stage = STAGES[-1]
            job.processed_ms = analysis.duration_ms
            job.finished_at = utc_now()
            analysis.completed_at = job.finished_at
            analysis.processed_ms = analysis.duration_ms
            session.commit()
        if ai_state not in ('partial', 'not_configured'):
            try:
                reuse.save_stage('completed', {'job_id': job_id})
            except OSError:
                pass  # A checkpoint failure cannot undo an already committed result.
    except Exception as error:
        # Preserve safe exception types/locations without SQL, tokens, or raw
        # request bodies. Earlier generic errors discarded this evidence.
        import traceback
        failure = {'job_id':job_id,'stage':reporter.stage,'exception_chain':[]}
        cause = error
        while cause is not None:
            failure['exception_chain'].append({'type':type(cause).__name__,
                'locations':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                             for f in traceback.extract_tb(cause.__traceback__)[-8:]]})
            cause = cause.__cause__
        try:
            save_result(settings.work_path,analysis_id,failure,filename=f'failure-{job_id}.json')
        except OSError:
            pass
        result_path.unlink(missing_ok=True)
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            analysis = session.get(Analysis, analysis_id)
            if not job or not analysis:
                return
            canceled = isinstance(error, Phase7Canceled) or job.cancel_requested
            job.status = JobStatus.CANCELED if canceled else JobStatus.FAILED
            job.stage = analysis.current_stage = "취소됨" if canceled else "분석 실패"
            job.error_code = None if canceled else "PHASE7_ANALYSIS_FAILED"
            job.error_message = None if canceled else str(error) if isinstance(error, Phase7Error) else "분석 처리 중 오류가 발생했습니다. 원본 영상과 설정은 보존되어 있습니다."
            job.finished_at = utc_now()
            analysis.status = AnalysisStatus.CANCELED if canceled else AnalysisStatus.FAILED
            analysis.failure_code = job.error_code
            analysis.failure_message = job.error_message
            session.commit()
    finally:
        if api_client is not None:
            api_client.close()


def refresh_simulation(session: Session, analysis: Analysis, job: AnalysisJob) -> AnalysisJob:
    if not job.is_simulation or job.status not in ACTIVE_JOB_STATUSES:
        return job
    now = utc_now()
    if job.cancel_requested or job.status is JobStatus.CANCEL_REQUESTED:
        session.query(Shot).filter(Shot.analysis_id == analysis.id).delete()
        job.status = JobStatus.CANCELED; job.stage = "취소됨"; job.finished_at = now; job.processed_ms = 0
        analysis.status = AnalysisStatus.CANCELED; analysis.current_stage = "취소됨"; analysis.processed_ms = 0
        session.commit(); session.refresh(job); return job
    started = as_utc(job.started_at) if job.started_at else now
    elapsed = max(0.0, (now - started).total_seconds())
    if elapsed < 1:
        return job
    stage_index = min(6, int((elapsed - 1) // 2))
    job.status = JobStatus.RUNNING
    job.stage = STAGES[stage_index]
    job.processed_ms = min(analysis.duration_ms, int(analysis.duration_ms * (stage_index + 1) / 7))
    job.heartbeat_at = now
    analysis.status = AnalysisStatus.ANALYZING
    analysis.current_stage = job.stage
    analysis.processed_ms = job.processed_ms
    if elapsed >= 13:
        job.status = JobStatus.SUCCEEDED; job.finished_at = now; job.processed_ms = analysis.duration_ms
        analysis.status = AnalysisStatus.SETUP_REQUIRED
        analysis.current_stage = "개발용 흐름 점검 완료"
        analysis.processed_ms = analysis.duration_ms
    session.commit(); session.refresh(job)
    return job


def cancel_job(session: Session, analysis: Analysis, job: AnalysisJob) -> AnalysisJob:
    if job.status not in ACTIVE_JOB_STATUSES:
        return job
    job.cancel_requested = True
    job.status = JobStatus.CANCEL_REQUESTED
    session.commit()
    return refresh_simulation(session, analysis, job)


def recover_stale_jobs(session: Session, stale_after: timedelta = timedelta(minutes=15)) -> int:
    threshold = utc_now() - stale_after
    jobs = list(session.scalars(select(AnalysisJob).where(AnalysisJob.status.in_(ACTIVE_JOB_STATUSES))))
    recovered = 0
    for job in jobs:
        heartbeat = job.heartbeat_at or job.started_at
        # A real worker cannot survive a backend restart. Mark it failed at once.
        if (not job.is_simulation) or (heartbeat and as_utc(heartbeat) < threshold):
            job.status = JobStatus.FAILED; job.error_code = "PROCESS_INTERRUPTED"; job.error_message = "프로그램이 종료되어 작업이 중단되었습니다."; job.finished_at = utc_now()
            analysis = session.get(Analysis, job.analysis_id)
            if analysis:
                analysis.status = AnalysisStatus.FAILED; analysis.failure_code = job.error_code; analysis.failure_message = job.error_message; analysis.current_stage = "중단됨"
            recovered += 1
    if recovered: session.commit()
    return recovered
