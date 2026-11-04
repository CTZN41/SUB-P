"""Throttle remote status I/O by wall time without changing detector inputs."""
from datetime import datetime, timezone
from time import perf_counter, sleep
from sqlalchemy.exc import DBAPIError

from app.models import Analysis, AnalysisJob


class JobProgressReporter:
    def __init__(self, session_factory, analysis_id: str, job_id: str, stage: str,
                 *, poll_seconds: float = 2, progress_seconds: float = 5,
                 clock=perf_counter, retry_sleep=sleep):
        self.session_factory = session_factory
        self.analysis_id, self.job_id, self.stage = analysis_id, job_id, stage
        self.poll_seconds, self.progress_seconds = poll_seconds, progress_seconds
        self.clock = clock
        self.retry_sleep = retry_sleep
        self.last_poll = self.last_progress = float("-inf")
        self.stopped = False
        self.stats = {"cancel_queries": 0, "progress_writes": 0, "db_seconds": 0.0, "db_retries": 0}

    def _retry_connection(self, operation):
        # Each operation opens its own session. Retry only disconnected DB
        # connections, never constraints/programming errors or AI requests.
        for attempt in range(3):
            try:
                return operation()
            except DBAPIError as error:
                if not error.connection_invalidated or attempt == 2:
                    raise
                self.stats['db_retries'] += 1
                self.retry_sleep(.2 * (attempt+1))

    def cancelled(self) -> bool:
        if self.stopped or self.clock() - self.last_poll < self.poll_seconds:
            return self.stopped
        started = self.clock()
        try:
            def read():
                with self.session_factory() as session:
                    self.stats["cancel_queries"] += 1
                    job = session.get(AnalysisJob, self.job_id)
                    self.stopped = job is None or job.cancel_requested
            self._retry_connection(read)
            return self.stopped
        finally:
            self.last_poll = self.clock()
            self.stats["db_seconds"] += self.last_poll - started

    def progress(self, time_ms: int) -> None:
        if self.stopped or self.clock() - self.last_progress < self.progress_seconds:
            return
        started = self.clock()
        try:
            def write():
                with self.session_factory() as session:
                    job = session.get(AnalysisJob, self.job_id)
                    analysis = session.get(Analysis, self.analysis_id)
                    if job is None or analysis is None or job.cancel_requested:
                        self.stopped = True
                        return
                    job.processed_ms = analysis.processed_ms = time_ms
                    job.stage = analysis.current_stage = self.stage
                    job.heartbeat_at = datetime.now(timezone.utc)
                    session.commit()
                    self.stats["progress_writes"] += 1
            self._retry_connection(write)
        finally:
            self.last_poll = self.last_progress = self.clock()
            self.stats["db_seconds"] += self.last_progress - started
