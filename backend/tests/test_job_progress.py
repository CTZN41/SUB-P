from types import SimpleNamespace

from app.models import AnalysisJob
from app.services.job_progress import JobProgressReporter
from sqlalchemy.exc import OperationalError, IntegrityError
import pytest


class Store:
    def __init__(self):
        self.job = SimpleNamespace(cancel_requested=False)
        self.analysis = SimpleNamespace()
        self.reads = self.commits = 0

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, model, identifier):
        self.reads += 1
        return self.job if model is AnalysisJob else self.analysis

    def commit(self):
        self.commits += 1


def test_fast_frames_do_not_repeat_db_calls_but_cancel_is_seen_on_next_poll():
    store, time = Store(), [0.0]
    reporter = JobProgressReporter(store, "analysis", "job", "슛 후보 찾기", clock=lambda: time[0])
    for _ in range(100):
        assert not reporter.cancelled()
    assert store.reads == 1
    store.job.cancel_requested = True
    time[0] = 2.01
    assert reporter.cancelled()
    reporter.progress(8000)
    assert store.commits == 0


def test_progress_throttles_writes_and_reuses_fresh_cancel_state():
    store, time = Store(), [0.0]
    reporter = JobProgressReporter(store, "analysis", "job", "슛 후보 찾기", clock=lambda: time[0])
    for value in range(100):
        reporter.progress(value)
    assert store.commits == 1
    assert not reporter.cancelled()
    assert store.reads == 2
    time[0] = 5.01
    reporter.progress(9000)
    assert store.commits == 2
    assert store.analysis.processed_ms == store.job.processed_ms == 9000
    assert store.job.heartbeat_at is not None


def test_removed_job_stops_analysis_instead_of_continuing():
    store = Store()
    store.job = None
    reporter = JobProgressReporter(store, "analysis", "job", "슛 후보 찾기")
    assert reporter.cancelled()


def test_disconnected_status_read_recovers_with_a_fresh_session():
    class Flaky(Store):
        attempts=0
        def get(self,*args):
            self.attempts+=1
            if self.attempts==1:
                raise OperationalError('test',{},Exception('disconnected'),connection_invalidated=True)
            return super().get(*args)
    store=Flaky();delays=[]
    reporter=JobProgressReporter(store,'analysis','job','stage',retry_sleep=delays.append)
    assert not reporter.cancelled()
    assert store.attempts==2 and reporter.stats['db_retries']==1 and delays==[.2]
    store.job.cancel_requested=True
    reporter.last_poll=float('-inf')
    assert reporter.cancelled()


def test_disconnected_progress_write_is_retried_but_constraints_are_not():
    class Flaky(Store):
        attempts=0
        def commit(self):
            self.attempts+=1
            if self.attempts==1:
                raise OperationalError('test',{},Exception('disconnected'),connection_invalidated=True)
            super().commit()
    store=Flaky();reporter=JobProgressReporter(store,'analysis','job','stage',retry_sleep=lambda _:None)
    reporter.progress(100)
    assert store.attempts==2 and store.commits==1 and reporter.stats['db_retries']==1
    class Invalid(Store):
        def commit(self):raise IntegrityError('test',{},Exception('constraint'))
    reporter=JobProgressReporter(Invalid(),'analysis','job','stage',retry_sleep=lambda _:None)
    with pytest.raises(IntegrityError):reporter.progress(100)
    assert reporter.stats['db_retries']==0


def test_persistent_connection_failure_is_bounded_and_not_silently_ignored():
    class Broken(Store):
        def get(self,*a):raise OperationalError('test',{},Exception('disconnected'),connection_invalidated=True)
    delays=[];reporter=JobProgressReporter(Broken(),'analysis','job','stage',retry_sleep=delays.append)
    with pytest.raises(OperationalError):reporter.cancelled()
    assert delays==[.2,.4] and reporter.stats['db_retries']==2
