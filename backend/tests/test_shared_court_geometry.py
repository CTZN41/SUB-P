"""The exported drawing must partition the same court as classify_zone."""
import json
from pathlib import Path

import pytest

pytest.importorskip("shapely", reason="Install requirements-geometry.txt for geometry validation")
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from app.services.court_zones import classify_zone
from scripts.generate_court_geometry import OUTPUT, build_geometry


def test_generated_geometry_is_current():
    assert json.loads(OUTPUT.read_text(encoding="utf-8")) == build_geometry()


def test_polygons_have_no_gaps_or_overlapping_interiors():
    data = build_geometry()
    regions = [unary_union([Polygon(rings[0], rings[1:]) for rings in polygons]) for polygons in data["zones"].values()]
    assert all(region.is_valid for region in regions)
    union = unary_union(regions)
    assert union.symmetric_difference(box(0, 0, 50, 47)).area < 1e-6
    assert sum(region.area for region in regions) - union.area < 1e-6


def test_drawing_matches_classifier_over_entire_court():
    data = build_geometry()
    regions = {code: unary_union([Polygon(rings[0], rings[1:]) for rings in polygons]) for code, polygons in data["zones"].items()}
    checked = 0
    for ix in range(200):
        for iy in range(188):
            x, y = (ix + .37) / 4, (iy + .41) / 4
            point = Point(x, y)
            expected = classify_zone(x, y).value
            assert regions[expected].distance(point) < 0.00001, (x, y, expected)
            assert not any(region.contains(point) for code, region in regions.items() if code != expected), (x, y, expected)
            checked += 1
    assert checked == 37600
