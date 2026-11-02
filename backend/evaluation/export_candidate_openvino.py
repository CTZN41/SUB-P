"""Export the existing candidate weights, without training or API calls."""
from pathlib import Path

from ultralytics import YOLO


def main() -> None:
    backend = Path(__file__).resolve().parents[1]
    for filename in ("basketball-yolo26n-pilot-v4.pt", "yolo26n-pose.pt"):
        model = YOLO(str(backend / "models" / filename))
        exported = model.export(format="openvino", imgsz=640, dynamic=True, quantize=32, device="cpu")
        print(f"exported={exported}", flush=True)


if __name__ == "__main__":
    main()
