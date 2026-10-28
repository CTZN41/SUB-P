import pytest

from app.services.court_zones import ZoneCode, classify_zone


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ((25, 5.25), ZoneCode.RESTRICTED),
        ((19, 10), ZoneCode.PAINT_LEFT),
        ((25, 15), ZoneCode.PAINT_CENTER),
        ((31, 10), ZoneCode.PAINT_RIGHT),
        ((10, 8), ZoneCode.MID_BASELINE_LEFT),
        ((12, 20), ZoneCode.MID_WING_LEFT),
        ((25, 22), ZoneCode.MID_CENTER),
        ((38, 20), ZoneCode.MID_WING_RIGHT),
        ((40, 8), ZoneCode.MID_BASELINE_RIGHT),
        ((2, 10), ZoneCode.THREE_CORNER_LEFT),
        ((5, 25), ZoneCode.THREE_WING_LEFT),
        ((25, 35), ZoneCode.THREE_CENTER),
        ((45, 25), ZoneCode.THREE_WING_RIGHT),
        ((48, 10), ZoneCode.THREE_CORNER_RIGHT),
    ],
)
def test_all_fourteen_zones_have_representative_points(
    point: tuple[float, float], expected: ZoneCode
) -> None:
    assert classify_zone(*point) is expected


def test_boundary_rules_are_deterministic() -> None:
    assert classify_zone(3, 14) is ZoneCode.THREE_CORNER_LEFT
    assert classify_zone(47, 14) is ZoneCode.THREE_CORNER_RIGHT
    assert classify_zone(25, 29) is ZoneCode.THREE_CENTER
    assert classify_zone(17, 19) is ZoneCode.PAINT_LEFT


@pytest.mark.parametrize("point", [(-0.01, 1), (50.01, 1), (1, -0.01), (1, 47.01)])
def test_outside_court_is_rejected(point: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="코트 좌표"):
        classify_zone(*point)
