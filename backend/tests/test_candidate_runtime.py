import pytest

from app.analysis.candidate_runtime import candidate_models
from app.analysis.phase7_pipeline import Phase7Error
from app.core.config import Settings


def test_default_keeps_original_trained_weights():
    model, pose, device = candidate_models(Settings(_env_file=None))
    assert model.name == "basketball-yolo26n-pilot-v4.pt"
    assert pose.name == "yolo26n-pose.pt"
    assert device == "cpu"


def test_openvino_missing_dependency_has_actionable_error(monkeypatch):
    monkeypatch.setattr("app.analysis.candidate_runtime.find_spec", lambda _: None)
    with pytest.raises(Phase7Error, match="requirements-openvino"):
        candidate_models(Settings(_env_file=None, candidate_runtime="openvino_cpu"))


def test_openvino_missing_export_does_not_silently_choose_other_weights(tmp_path, monkeypatch):
    monkeypatch.setattr("app.analysis.candidate_runtime.find_spec", lambda _: object())
    monkeypatch.setattr(Settings, "resolve_path", lambda self, value: tmp_path / value)
    with pytest.raises(Phase7Error, match="모델이 없습니다"):
        candidate_models(Settings(_env_file=None, candidate_runtime="openvino_cpu"))
