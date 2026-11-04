from threading import Event, Thread
from types import SimpleNamespace
import sys

import pytest

from app.analysis.frame_prefetch_trial import FramePrefetchTrial
from app.services.async_progress_trial import AsyncProgressTrial
from app.analysis.model_reuse_trial import ModelReuseTrial
from app.services.ordered_review_trial import bounded_ordered_map


class Capture:
    def __init__(self, position=0):
        self.position = position

    def grab(self):
        self.position += 1
        return True

    def read(self):
        frame = self.position
        self.position += 1
        return True, frame


def test_prefetch_keeps_exact_selected_frames_and_stops_with_full_queue():
    reader = FramePrefetchTrial(Capture(10), 10, 31, 7, capacity=1)
    try:
        frames = [reader.next() for _ in range(3)]
        assert [(x[0], x[2]) for x in frames] == [(10, 10), (17, 17), (24, 24)]
    finally:
        reader.close()
    assert not reader.thread.is_alive()


def test_prefetch_propagates_decode_failure():
    class Broken(Capture):
        def grab(self):
            return False
    reader = FramePrefetchTrial(Broken(), 0, 10, 7)
    try:
        assert reader.next()[0] == 0
        with pytest.raises(RuntimeError, match="prefetch failed"):
            reader.next()
    finally:
        reader.close()


@pytest.mark.parametrize('available', [94, 96])
def test_short_physical_tail_ends_cleanly_without_fabricating_frames(available):
    class Short(Capture):
        def grab(self):
            if self.position >= available: return False
            return super().grab()
        def read(self):
            if self.position >= available: return False, None
            return super().read()
    reader=FramePrefetchTrial(Short(),0,100,4,end_tolerance_frames=10)
    frames=[]
    try:
        while True:
            item=reader.next()
            if item is None:break
            frames.append(item)
    finally:reader.close()
    assert all(item[1] and item[0] < available for item in frames)
    assert reader.early_end['first_unread_frame']==available
    assert reader.early_end['last_sampled_frame']==frames[-1][0]


def test_tail_tolerance_does_not_hide_a_damaged_middle():
    class Middle(Capture):
        def grab(self):
            if self.position >= 50:return False
            return super().grab()
        def read(self):
            if self.position >= 50:return False,None
            return super().read()
    reader=FramePrefetchTrial(Middle(),0,100,4,end_tolerance_frames=10)
    try:
        with pytest.raises(RuntimeError,match='prefetch failed'):
            while reader.next() is not None:pass
    finally:reader.close()
    assert reader.early_end is None


def test_status_writes_do_not_block_detector_and_latest_value_is_kept():
    entered, release = Event(), Event()
    class Sink:
        def cancelled(self):
            return False
        def progress(self, value):
            entered.set()
            release.wait(2)
    trial = AsyncProgressTrial(Sink(), interval=.001).__enter__()
    try:
        trial.progress(100)
        assert entered.wait(1)
        trial.progress(200)
        assert not trial.cancelled()
        assert trial._latest == 200
    finally:
        release.set()
        trial.close()


def test_background_cancellation_is_visible_to_detector():
    stop = Event()
    class Sink:
        def cancelled(self):
            return stop.is_set()
        def progress(self, value):
            pass
    trial = AsyncProgressTrial(Sink(), interval=.001).__enter__()
    try:
        stop.set()
        assert trial._cancelled.wait(1)
        assert trial.cancelled()
    finally:
        trial.close()


def test_background_db_failure_is_not_silently_ignored():
    class Sink:
        calls = 0
        def cancelled(self):
            self.calls += 1
            if self.calls > 1:
                raise OSError("test DB failure")
            return False
        def progress(self, value):
            pass
    trial = AsyncProgressTrial(Sink(), interval=.001).__enter__()
    assert trial._cancelled.wait(1)
    with pytest.raises(RuntimeError, match="status I/O failed"):
        trial.cancelled()
    with pytest.raises(RuntimeError, match="status I/O failed"):
        trial.close()


def test_reader_closes_when_producer_is_blocked_by_full_queue():
    second_read = Event()
    class Signalled(Capture):
        def read(self):
            result = super().read()
            if self.position > 1:
                second_read.set()
            return result
    reader = FramePrefetchTrial(Signalled(), 0, 100, 1, capacity=1)
    assert second_read.wait(1)
    reader.close()
    assert not reader.thread.is_alive()


def test_model_reuse_is_job_local_and_never_shared_across_threads(tmp_path, monkeypatch):
    path = tmp_path / "model.pt"
    path.write_bytes(b"test")
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda *args, **kwargs: object()))
    reuse = ModelReuseTrial()
    assert reuse.get(path, "detect", "cpu") is reuse.get(path, "detect", "cpu")
    assert reuse.loads == 1
    failures = []
    def other_thread():
        try:
            reuse.get(path, "detect", "cpu")
        except RuntimeError as error:
            failures.append(str(error))
    thread = Thread(target=other_thread)
    thread.start()
    thread.join()
    assert failures == ["Do not share YOLO predictors across threads"]


def test_parallel_detail_mapping_preserves_candidate_order():
    second_done = Event()
    def task(value):
        if value == 0:
            assert second_done.wait(1)
        if value == 1:
            second_done.set()
        return {"candidate": value, "decision": "saved result"}
    assert bounded_ordered_map(task, [0, 1, 2], workers=2) == [
        {"candidate": i, "decision": "saved result"} for i in range(3)
    ]
