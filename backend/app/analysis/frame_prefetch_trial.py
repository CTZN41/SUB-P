"""Bounded, ordered frame prefetch. No frame dropping or image changes."""
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import perf_counter


class FramePrefetchTrial:
    def __init__(self, capture, first_frame: int, last_frame: int, step: int, *, capacity: int = 2,
                 end_tolerance_frames: int = 0):
        self.capture = capture
        self.first, self.last, self.step = first_frame, last_frame, step
        self.queue = Queue(maxsize=capacity)
        self.stop = Event()
        self.end_tolerance_frames = end_tolerance_frames
        self.early_end = None
        self.last_valid_index = None
        self.thread = Thread(target=self._run, daemon=True, name="candidate-frame-trial")
        self.thread.start()

    def _put(self, value):
        while not self.stop.is_set():
            try:
                self.queue.put(value, timeout=.1)
                return True
            except Full:
                pass
        return False

    def _run(self):
        next_index = self.first
        try:
            for index in range(self.first, self.last, self.step):
                if self.stop.is_set():
                    break
                started = perf_counter()
                while next_index < index:
                    if not self.capture.grab():
                        if self._near_end(next_index):
                            return
                        raise RuntimeError("Could not skip source frame")
                    next_index += 1
                ok, frame = self.capture.read()
                next_index += 1
                if not ok and self._near_end(index):
                    return
                if ok:
                    self.last_valid_index = index
                if not self._put((index, ok, frame, perf_counter() - started)):
                    return
            self._put(None)
        except Exception as error:
            self._put(error)

    def _near_end(self, index):
        if (self.last_valid_index is None or self.end_tolerance_frames <= 0
                or not 0 < self.last-index <= self.end_tolerance_frames):
            return False
        self.early_end = {'first_unread_frame': index, 'last_sampled_frame': self.last_valid_index,
                          'reported_end_frame': self.last, 'reason': 'short_unreadable_video_tail'}
        self._put(None)
        return True

    def next(self):
        while True:
            try:
                value = self.queue.get(timeout=.1)
                if isinstance(value, Exception):
                    raise RuntimeError("Frame prefetch failed") from value
                return value
            except Empty:
                if not self.thread.is_alive():
                    raise RuntimeError("Frame reader ended without the expected frame")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("Frame reader did not stop; capture must remain owned by reader")
