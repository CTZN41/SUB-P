"""Select an explicitly configured backend for the same trained weights."""
from importlib.util import find_spec

from app.analysis.phase7_pipeline import Phase7Error
from app.core.config import Settings


def candidate_models(settings: Settings):
    if settings.candidate_runtime == "torch":
        return (settings.resolve_path("models/basketball-yolo26n-pilot-v4.pt"),
                settings.resolve_path("models/yolo26n-pose.pt"), "cpu")
    model = settings.resolve_path("models/basketball-yolo26n-pilot-v4_openvino_model")
    pose = settings.resolve_path("models/yolo26n-pose_openvino_model")
    if find_spec("openvino") is None:
        raise Phase7Error("OpenVINO 실행 패키지가 없습니다. requirements-openvino.txt를 설치하거나 CANDIDATE_RUNTIME=torch로 설정하세요.")
    if not all((path / "metadata.yaml").is_file() and list(path.glob("*.xml")) and list(path.glob("*.bin"))
               for path in (model, pose)):
        raise Phase7Error("OpenVINO 모델이 없습니다. evaluation/export_candidate_openvino.py로 기존 가중치를 변환하세요.")
    return model, pose, "intel:gpu" if settings.candidate_runtime == "openvino_gpu" else "intel:cpu"
