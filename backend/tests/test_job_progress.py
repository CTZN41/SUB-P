from types import SimpleNamespace

from app.models import AnalysisJob
from app.services.job_progress import JobProgressReporter


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
