"""현재 PC의 CPU 환경에서 YOLO26n 농구 탐지 모델을 1차 학습한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YOLO26n 농구 모델 학습")
    parser.add_argument("--data", type=Path, default=Path("datasets/basketball-v1/data.yaml"))
    parser.add_argument("--model", default="models/yolo26n.pt")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--name", default="yolo26n-basketball-v1")
    parser.add_argument("--degrees", type=float, default=0.0)
    parser.add_argument("--translate", type=float, default=0.1)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--perspective", type=float, default=0.0)
    parser.add_argument("--fliplr", type=float, default=0.5)
    parser.add_argument("--lr0", type=float, default=0.01)
    parser.add_argument("--lrf", type=float, default=0.01)
    parser.add_argument("--freeze", type=int, default=0)
    parser.add_argument("--mosaic", type=float, default=1.0)
    parser.add_argument("--close-mosaic", type=int, default=10)
    parser.add_argument("--optimizer", default="auto")
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    model = YOLO(args.model)
    model.train(
        data=str(args.data.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device="cpu",
        workers=0,
        cache=False,
        project=str(Path("work/training").resolve()),
        name=args.name,
        exist_ok=True,
        patience=max(3, args.epochs),
        seed=41,
        deterministic=True,
        verbose=True,
        degrees=args.degrees,
        translate=args.translate,
        scale=args.scale,
        perspective=args.perspective,
        fliplr=args.fliplr,
        lr0=args.lr0,
        lrf=args.lrf,
        freeze=args.freeze or None,
        mosaic=args.mosaic,
        close_mosaic=args.close_mosaic,
        optimizer=args.optimizer,
    )


if __name__ == "__main__":
    run(parse_args())
