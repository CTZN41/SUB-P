"""영상 좌표와 표준 반코트 좌표 사이의 변환 도우미."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


STANDARD_COURT_POINTS = [[0.0, 0.0], [50.0, 0.0], [50.0, 47.0], [0.0, 47.0]]


class CalibrationError(ValueError):
    pass


def arc_has_crossing(points: list[list[float]]) -> bool:
    """Allow hand-drawn curvature and collinear corner lines, reject crossings."""
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on(a,b,p):
        return abs(cross(a,b,p)) < 1e-6 and min(a[0],b[0])-1e-6 <= p[0] <= max(a[0],b[0])+1e-6 and min(a[1],b[1])-1e-6 <= p[1] <= max(a[1],b[1])+1e-6
    # This is an open trace: unseen ends must never be joined artificially.
    for i in range(len(points)-1):
        a,b=points[i],points[i+1]
        for j in range(i+2,len(points)-1):
            c,d=points[j],points[j+1]
            if cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0:
                return True
            if on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b):return True
    return False


def three_point_trace_side(points, foot, transform, boundary_px=6):
    """Compare only rays crossing the observed open trace; no extrapolation."""
    if transform is None or len(points) < 2:
        return None
    try:
        origin = np.asarray(transform_point(np.linalg.inv(np.asarray(transform, dtype=float)), [25, 5.25]))
        direction = np.asarray(foot, dtype=float) - origin
        if not np.all(np.isfinite(origin)) or np.linalg.norm(direction) < 1e-6:
            return None
        def cross(a, b):
            return float(a[0]*b[1]-a[1]*b[0])
        hits = []
        for a, b in zip(points, points[1:]):
            offset = np.asarray(a, dtype=float)-origin
            segment = np.asarray(b, dtype=float)-np.asarray(a, dtype=float)
            denominator = cross(direction, segment)
            if abs(denominator) < 1e-9:
                continue
            ray = cross(offset, segment)/denominator
            along = cross(offset, direction)/denominator
            if ray > 0 and -1e-9 <= along <= 1+1e-9:
                hits.append(ray)
        hits = sorted(hits)
        unique = [r for i, r in enumerate(hits) if i == 0 or abs(r-hits[i-1]) > 1e-6]
        if len(unique) != 1:
            return None
        ray = unique[0]
        if abs(ray-1)*np.linalg.norm(direction) <= boundary_px:
            return 'boundary'
        return 'inside' if ray > 1 else 'outside'
    except (ValueError, np.linalg.LinAlgError):
        return None


def uses_known_inconsistent_lane_points(court_points: list[list[float]]) -> bool:
    """Recognize the old 12ft virtual lane that conflicts with the 16ft map."""
    return len(court_points) == 4 and {tuple(point) for point in court_points} == {
        (19, 0), (31, 0), (31, 19), (19, 19),
    }


def resolve_video_path(upload_path: Path, analysis_id: str) -> Path:
    path = (upload_path / analysis_id / "original.mp4").resolve()
    if upload_path.resolve() not in path.parents or not path.is_file():
        raise CalibrationError("등록된 원본 영상을 찾을 수 없습니다.")
    return path


def read_video_image(video_path: Path, time_ms: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise CalibrationError("영상 대표 장면을 열 수 없습니다.")
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0, time_ms))
        ok, frame = capture.read()
        if not ok:
            raise CalibrationError("선택한 시간의 영상 장면을 읽을 수 없습니다.")
        return frame
    finally:
        capture.release()


def read_video_frame(video_path: Path, time_ms: int) -> tuple[bytes, int, int]:
    frame = read_video_image(video_path, time_ms)
    height, width = frame.shape[:2]
    encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not encoded:
        raise CalibrationError("영상 대표 장면을 만들 수 없습니다.")
    return buffer.tobytes(), width, height


def validate_points(
    image_points: list[list[float]],
    court_points: list[list[float]],
    hoop_point: list[float],
    width: int,
    height: int,
) -> None:
    if len(image_points) < 4 or len(image_points) != len(court_points):
        raise CalibrationError("바닥 기준점은 서로 대응하는 네 개 이상이 필요합니다.")
    if uses_known_inconsistent_lane_points(court_points):
        raise CalibrationError("이전 페인트존 좌표 19~31은 현재 구역 경계 17~33과 다릅니다. 코트 기준점을 다시 맞춰 주세요.")
    if any(len(point) != 2 for point in [*image_points, *court_points]) or len(hoop_point) != 2:
        raise CalibrationError("모든 기준점은 가로와 세로 위치 두 값이어야 합니다.")
    if any(not (0 <= point[0] < width and 0 <= point[1] < height) for point in image_points):
        raise CalibrationError("바닥 기준점이 영상 화면 밖에 있습니다.")
    if not (0 <= hoop_point[0] < width and 0 <= hoop_point[1] < height):
        raise CalibrationError("골대 기준점이 영상 화면 밖에 있습니다.")

    image_array = np.asarray(image_points, dtype=np.float32)
    court_array = np.asarray(court_points, dtype=np.float32)
    if len(np.unique(image_array, axis=0)) < 4:
        raise CalibrationError("바닥 기준점이 겹쳐 있습니다.")
    if cv2.contourArea(image_array[:4]) < width * height * 0.01:
        raise CalibrationError("바닥 기준점이 한곳에 몰려 있습니다.")
    if not cv2.isContourConvex(image_array[:4]):
        raise CalibrationError("바닥 기준점 순서가 교차했습니다.")
    if cv2.contourArea(court_array[:4]) <= 0:
        raise CalibrationError("가상 코트 기준점 순서가 올바르지 않습니다.")


def calculate_transform(
    image_points: list[list[float]], court_points: list[list[float]]
) -> list[list[float]]:
    matrix, _ = cv2.findHomography(
        np.asarray(image_points, dtype=np.float32),
        np.asarray(court_points, dtype=np.float32),
        method=0,
    )
    if matrix is None or abs(np.linalg.det(matrix)) < 1e-9:
        raise CalibrationError("코트 좌표 변환을 계산할 수 없습니다.")
    return matrix.tolist()


def transform_point(transform: list[list[float]], point: list[float]) -> list[float]:
    source = np.asarray([[point]], dtype=np.float32)
    result = cv2.perspectiveTransform(source, np.asarray(transform, dtype=np.float64))[0][0]
    return [round(float(result[0]), 4), round(float(result[1]), 4)]
