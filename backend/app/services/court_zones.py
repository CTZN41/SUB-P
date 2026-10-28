"""표준 반코트 좌표를 SPEC의 14개 슈팅 구역으로 분류한다."""

from __future__ import annotations

from enum import Enum
from math import hypot


COURT_WIDTH_FT = 50.0
HALF_COURT_LENGTH_FT = 47.0
HOOP_X_FT = 25.0
HOOP_Y_FT = 5.25
RESTRICTED_RADIUS_FT = 4.0
THREE_POINT_RADIUS_FT = 23.75
CORNER_THREE_X_FT = 3.0
CORNER_THREE_END_Y_FT = 14.0
PAINT_LEFT_X_FT = 17.0
PAINT_RIGHT_X_FT = 33.0
FREE_THROW_LINE_Y_FT = 19.0


class ZoneCode(str, Enum):
    RESTRICTED = "restricted"
    PAINT_LEFT = "paint_left"
    PAINT_CENTER = "paint_center"
    PAINT_RIGHT = "paint_right"
    MID_BASELINE_LEFT = "mid_baseline_left"
    MID_WING_LEFT = "mid_wing_left"
    MID_CENTER = "mid_center"
    MID_WING_RIGHT = "mid_wing_right"
    MID_BASELINE_RIGHT = "mid_baseline_right"
    THREE_CORNER_LEFT = "three_corner_left"
    THREE_WING_LEFT = "three_wing_left"
    THREE_CENTER = "three_center"
    THREE_WING_RIGHT = "three_wing_right"
    THREE_CORNER_RIGHT = "three_corner_right"


ZONE_LABELS: dict[ZoneCode, str] = {
    ZoneCode.RESTRICTED: "골밑",
    ZoneCode.PAINT_LEFT: "페인트존 왼쪽",
    ZoneCode.PAINT_CENTER: "페인트존 중앙",
    ZoneCode.PAINT_RIGHT: "페인트존 오른쪽",
    ZoneCode.MID_BASELINE_LEFT: "왼쪽 베이스라인 미드레인지",
    ZoneCode.MID_WING_LEFT: "왼쪽 윙 미드레인지",
    ZoneCode.MID_CENTER: "정면 미드레인지",
    ZoneCode.MID_WING_RIGHT: "오른쪽 윙 미드레인지",
    ZoneCode.MID_BASELINE_RIGHT: "오른쪽 베이스라인 미드레인지",
    ZoneCode.THREE_CORNER_LEFT: "왼쪽 코너 3점",
    ZoneCode.THREE_WING_LEFT: "왼쪽 윙 3점",
    ZoneCode.THREE_CENTER: "정면 3점",
    ZoneCode.THREE_WING_RIGHT: "오른쪽 윙 3점",
    ZoneCode.THREE_CORNER_RIGHT: "오른쪽 코너 3점",
}


def classify_zone(x: float, y: float) -> ZoneCode:
    """코트 좌표를 항상 하나의 구역으로 반환한다.

    경계는 `<=` 조건을 먼저 만나는 구역에 배정한다. 3점선 위는 3점,
    제한구역 선 위는 골밑, 페인트존 선 위는 페인트존으로 처리한다.
    """

    if not 0.0 <= x <= COURT_WIDTH_FT or not 0.0 <= y <= HALF_COURT_LENGTH_FT:
        raise ValueError("코트 좌표는 x 0~50ft, y 0~47ft 안에 있어야 합니다.")

    dx = x - HOOP_X_FT
    dy = y - HOOP_Y_FT
    distance = hypot(dx, dy)

    if y <= CORNER_THREE_END_Y_FT and x <= CORNER_THREE_X_FT:
        return ZoneCode.THREE_CORNER_LEFT
    if y <= CORNER_THREE_END_Y_FT and x >= COURT_WIDTH_FT - CORNER_THREE_X_FT:
        return ZoneCode.THREE_CORNER_RIGHT
    if distance >= THREE_POINT_RADIUS_FT:
        if dx < -8.0:
            return ZoneCode.THREE_WING_LEFT
        if dx > 8.0:
            return ZoneCode.THREE_WING_RIGHT
        return ZoneCode.THREE_CENTER

    if distance <= RESTRICTED_RADIUS_FT:
        return ZoneCode.RESTRICTED

    if PAINT_LEFT_X_FT <= x <= PAINT_RIGHT_X_FT and y <= FREE_THROW_LINE_Y_FT:
        paint_third = (PAINT_RIGHT_X_FT - PAINT_LEFT_X_FT) / 3
        if x <= PAINT_LEFT_X_FT + paint_third:
            return ZoneCode.PAINT_LEFT
        if x <= PAINT_RIGHT_X_FT - paint_third:
            return ZoneCode.PAINT_CENTER
        return ZoneCode.PAINT_RIGHT

    if y <= 13.0 and x < PAINT_LEFT_X_FT:
        return ZoneCode.MID_BASELINE_LEFT
    if y <= 13.0 and x > PAINT_RIGHT_X_FT:
        return ZoneCode.MID_BASELINE_RIGHT
    if dx < -6.0:
        return ZoneCode.MID_WING_LEFT
    if dx > 6.0:
        return ZoneCode.MID_WING_RIGHT
    return ZoneCode.MID_CENTER
