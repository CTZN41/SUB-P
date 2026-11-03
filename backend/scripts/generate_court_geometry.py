"""Generate the frontend's shared polygons from the backend classification rules.

From backend: python scripts/generate_court_geometry.py [--check]
Requires requirements-geometry.txt; not needed to run the application.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import Point, box
from shapely.geometry.polygon import orient
from app.services import court_zones as rules

OUTPUT = Path(__file__).resolve().parents[2] / "frontend/src/utils/courtGeometry.json"


def build_geometry() -> dict:
    width, length = rules.COURT_WIDTH_FT, rules.HALF_COURT_LENGTH_FT
    hx, hy = rules.HOOP_X_FT, rules.HOOP_Y_FT
    left, right = rules.PAINT_LEFT_X_FT, rules.PAINT_RIGHT_X_FT
    free = rules.FREE_THROW_LINE_Y_FT
    corner, end = rules.CORNER_THREE_X_FT, rules.CORNER_THREE_END_Y_FT
    mid, three = rules.MID_CENTER_HALF_WIDTH_FT, rules.THREE_CENTER_HALF_WIDTH_FT
    court = box(0, 0, width, length)
    # 4096 segments: maximum deviation from the analytic circle < 0.00001 ft.
    circle = Point(hx, hy).buffer(rules.THREE_POINT_RADIUS_FT, quad_segs=1024)
    restricted = Point(hx, hy).buffer(rules.RESTRICTED_RADIUS_FT, quad_segs=1024)
    third = (right - left) / 3
    masks = [
        ("three_corner_left", box(0, 0, corner, end)),
        ("three_corner_right", box(width - corner, 0, width, end)),
        ("three_wing_left", court.difference(circle).intersection(box(0, 0, hx - three, length))),
        ("three_wing_right", court.difference(circle).intersection(box(hx + three, 0, width, length))),
        ("three_center", court.difference(circle)),
        ("restricted", restricted),
        ("paint_left", box(left, 0, left + third, free)),
        ("paint_center", box(left + third, 0, right - third, free)),
        ("paint_right", box(right - third, 0, right, free)),
        ("mid_baseline_left", box(0, 0, left, rules.BASELINE_MID_END_Y_FT)),
        ("mid_baseline_right", box(right, 0, width, rules.BASELINE_MID_END_Y_FT)),
        ("mid_wing_left", box(0, 0, hx - mid, length)),
        ("mid_wing_right", box(hx + mid, 0, width, length)),
        ("mid_center", court),
    ]
    remaining = court
    zones = {}
    for code, mask in masks:
        region = remaining.intersection(mask)
        remaining = remaining.difference(mask)
        polygons = [region] if region.geom_type == "Polygon" else list(region.geoms)
        rings = []
        for polygon in polygons:
            if polygon.geom_type != "Polygon" or polygon.area < 1e-10:
                continue
            polygon = orient(polygon, sign=1)
            rings.append([
                [[x, y] for x, y in ring.coords]
                for ring in [polygon.exterior, *polygon.interiors]
            ])
        zones[code] = rings
    assert remaining.area < 1e-9
    parameters = {name: value for name, value in vars(rules).items() if name.endswith("_FT")}
    return {"parameters": parameters, "zones": zones}


def serialized_geometry() -> str:
    return json.dumps(build_geometry(), ensure_ascii=False, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    content = serialized_geometry()
    if "--check" in sys.argv:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            raise SystemExit("Court geometry is stale. Run python scripts/generate_court_geometry.py")
        print("Shared court geometry matches backend rules.")
    else:
        OUTPUT.write_text(content, encoding="utf-8")
        print("Updated frontend/src/utils/courtGeometry.json")
