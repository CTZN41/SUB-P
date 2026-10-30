"""실제 분석기를 붙이기 전 작업 상태 흐름을 검증하는 개발용 작업."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from time import perf_counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Analysis, AnalysisJob, AnalysisStatus, CourtCalibration, JobStatus, Shot
from app.analysis.phase7_pipeline import Phase7Canceled, Phase7Error, analyze_video, save_result
from app.core.config import Settings
from app.services.openai_shot_review import review_candidates_with_openai
from app.services.candidate_enrichment import enrich_candidates
from app.services.phase8_shots import materialize_shots_from_candidates


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
    model_path = settings.resolve_path(SHOT_DETECTION_MODEL)
    if not model_path.is_file():
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
        calibration = session.scalar(select(CourtCalibration).where(CourtCalibration.analysis_id == analysis_id))
        if not job or not analysis or not calibration:
            return
        job.status = JobStatus.RUNNING
        job.stage = STAGES[2]
        job.heartbeat_at = utc_now()
        analysis.status = AnalysisStatus.ANALYZING
        analysis.current_stage = job.stage
        hoop_point = list(calibration.hoop_point)
        session.commit()

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        if checks % 8:
            return False
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            return job is None or job.cancel_requested

    def progress(time_ms: int) -> None:
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            analysis = session.get(Analysis, analysis_id)
            if job and analysis and not job.cancel_requested:
                job.processed_ms = analysis.processed_ms = time_ms
                job.stage = analysis.current_stage = STAGES[3]
                job.heartbeat_at = utc_now()
                session.commit()

    try:
        video_path = settings.upload_path / analysis_id / "original.mp4"
        model_path = settings.resolve_path(SHOT_DETECTION_MODEL)
        pose_model_path = settings.resolve_path("models/yolo26n-pose.pt")
        started = perf_counter()
        result = analyze_video(video_path, model_path, hoop_point,
                               pose_model_path=pose_model_path,
                               progress=progress, cancelled=cancelled,
                               collect_motion_hints=True)
        local_seconds = perf_counter() - started
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            analysis = session.get(Analysis, analysis_id)
            if job is None or analysis is None or job.cancel_requested:
                raise Phase7Canceled("사용자가 분석을 취소했습니다.")
            job.stage = analysis.current_stage = STAGES[4]
            job.processed_ms = analysis.processed_ms = analysis.duration_ms
            job.heartbeat_at = utc_now()
            session.commit()
        started = perf_counter()
        result = enrich_candidates(video_path, model_path, pose_model_path,
                                   hoop_point, result, settings, cancelled=cancelled)
        enrichment_seconds = perf_counter() - started
        started = perf_counter()
        result = review_candidates_with_openai(video_path, result, settings)
        result["timing_seconds"] = {
            "local_candidates": round(local_seconds, 2),
            "focused_rescan_and_shot_gate": round(enrichment_seconds, 2),
            "detailed_ai_review": round(perf_counter() - started, 2),
        }
        gate_usage = result.get("shot_gate_summary", {}).get("usage", {})
        detail_usage = result.get("ai_review", {}).get("usage", {})
        result["api_usage"] = {
            key: int(gate_usage.get(key, 0) or 0) + int(detail_usage.get(key, 0) or 0)
            for key in ("input_tokens", "output_tokens")
        }
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
    except Exception as error:
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
            job.error_message = None if canceled else str(error) if isinstance(error, Phase7Error) else "영상 분석 중 문제가 생겼습니다. 원본 영상과 모델 파일을 확인해 주세요."
            job.finished_at = utc_now()
            analysis.status = AnalysisStatus.CANCELED if canceled else AnalysisStatus.FAILED
            analysis.failure_code = job.error_code
            analysis.failure_message = job.error_message
            session.commit()


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
