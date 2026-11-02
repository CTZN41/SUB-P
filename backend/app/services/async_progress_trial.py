"""Experiment: keep remote status I/O outside the detector thread.

The sink owns its DB sessions. Call close() before publishing final job state.
This wrapper is scoped to one job and must not be shared between jobs.
"""
from threading import Event, Lock, Thread


class AsyncProgressTrial:
    def __init__(self, sink, *, interval: float = 2):
        self.sink = sink
        self.interval = interval
        self._stop, self._cancelled = Event(), Event()
        self._lock = Lock()
        self._latest = None
        self._error = None
        self._thread = Thread(target=self._run, daemon=True, name="candidate-status-trial")

    def __enter__(self):
        # Confirm initial cancellation before starting any expensive work.
        if self.sink.cancelled():
            self._cancelled.set()
        self._thread.start()
        return self

    def _run(self):
        try:
            while not self._stop.wait(self.interval):
                if self.sink.cancelled():
                    self._cancelled.set()
                    return
                with self._lock:
                    latest = self._latest
                if latest is not None:
                    self.sink.progress(latest)
        except Exception as error:
            with self._lock:
                self._error = error
            self._cancelled.set()

    def _raise_error(self):
        with self._lock:
            error = self._error
        if error is not None:
            raise RuntimeError("Background status I/O failed") from error

    def cancelled(self):
        self._raise_error()
        return self._cancelled.is_set()

    def progress(self, time_ms: int):
        self._raise_error()
        with self._lock:
            self._latest = time_ms

    def close(self):
        self._stop.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise RuntimeError("Status thread did not stop; do not publish a final job state")
        self._raise_error()

    def __exit__(self, *args):
        self.close()
