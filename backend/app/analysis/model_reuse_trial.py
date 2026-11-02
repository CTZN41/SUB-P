"""Job-local, single-thread inference objects. Never a global shared cache."""
from threading import get_ident


class ModelReuseTrial:
    def __init__(self):
        self.owner = get_ident()
        self.models = {}
        self.loads = 0

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
