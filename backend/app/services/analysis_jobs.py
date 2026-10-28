"""실제 분석기를 붙이기 전 작업 상태 흐름을 검증하는 개발용 작업."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, AnalysisJob, AnalysisStatus, JobStatus, Shot


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


def refresh_simulation(session: Session, analysis: Analysis, job: AnalysisJob) -> AnalysisJob:
    if not job.is_simulation or job.status not in ACTIVE_JOB_STATUSES:
        return job
    now = utc_now()
    if job.cancel_requested or job.status is JobStatus.CANCEL_REQUESTED:
        session.query(Shot).filter(Shot.analysis_id == analysis.id).delete()
        job.status = JobStatus.CANCELED; job.stage = "취소됨"; job.finished_at = now
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
        if heartbeat and as_utc(heartbeat) < threshold:
            job.status = JobStatus.FAILED; job.error_code = "PROCESS_INTERRUPTED"; job.error_message = "프로그램이 종료되어 작업이 중단되었습니다."; job.finished_at = utc_now()
            analysis = session.get(Analysis, job.analysis_id)
            if analysis:
                analysis.status = AnalysisStatus.FAILED; analysis.failure_code = job.error_code; analysis.failure_message = job.error_message; analysis.current_stage = "중단됨"
            recovered += 1
    if recovered: session.commit()
    return recovered
