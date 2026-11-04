"""Job-local, single-thread inference objects. Never a global shared cache."""
from threading import get_ident
from collections import OrderedDict
from hashlib import sha256
import pickle


class ModelReuseTrial:
    def __init__(self):
        self.owner = get_ident()
        self.models = {}
        self.loads = 0
        self.predictions = OrderedDict()
        self.prediction_bytes = 0
        self.max_prediction_bytes = 64 * 1024 * 1024
        self.max_prediction_entries = 75000
        self.cache_stats = {'hits': 0, 'misses': 0, 'evictions': 0}

    @staticmethod
    def model_identity(path):
        stamp = tuple((str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in sorted(path.rglob('*')) if p.is_file()) if path.is_dir() else (path.stat().st_size, path.stat().st_mtime_ns)
        return str(path.resolve()), stamp

    @staticmethod
    def source_identity(source):
        stat = source.stat()
        return str(source.resolve()), stat.st_size, stat.st_mtime_ns

    @staticmethod
    def frame_identity(source_identity, frame_index, frame):
        pixels = memoryview(frame) if frame.flags.c_contiguous else frame.tobytes()
        return source_identity, frame_index, frame.shape, str(frame.dtype), sha256(pixels).digest()

    def prediction(self, source, frame_index, frame, path, task, device, options, compute,
                   *, frame_identity=None, model_identity=None):
        """Cache raw observations only; player/hoop tracking is always replayed.

        Pixel identity also protects against decoder seek differences. Values are
        compact observations, never Results objects retaining full video frames.
        """
        if get_ident() != self.owner:
            raise RuntimeError('Do not share inference caches across threads')
        if self.max_prediction_bytes <= 0:
            self.cache_stats['misses'] += 1
            return compute()
        frame_identity = frame_identity or self.frame_identity(self.source_identity(source), frame_index, frame)
        key = (frame_identity, model_identity or self.model_identity(path), task, device, options)
        if key in self.predictions:
            self.cache_stats['hits'] += 1
            payload = self.predictions.pop(key)
            self.predictions[key] = payload
            return pickle.loads(payload)
        self.cache_stats['misses'] += 1
        value = compute()
        payload = pickle.dumps(value, protocol=5)
        if len(payload) <= self.max_prediction_bytes:
            while self.predictions and (self.prediction_bytes + len(payload) > self.max_prediction_bytes
                                        or len(self.predictions) >= self.max_prediction_entries):
                _, old = self.predictions.popitem(last=False)
                self.prediction_bytes -= len(old)
                self.cache_stats['evictions'] += 1
            self.predictions[key] = payload
            self.prediction_bytes += len(payload)
        return value

    def get(self, path, task, device):
        if get_ident() != self.owner:
            raise RuntimeError("Do not share YOLO predictors across threads")
        if path is None or not path.exists():
            return None
        key = (str(path.resolve()), task, device)
        if key not in self.models:
            from ultralytics import YOLO
            self.models[key] = YOLO(str(path), task=task)
            self.loads += 1
        return self.models[key]
