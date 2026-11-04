"""Analytic boundaries of the 14-zone classifier, without drawing approximations.

Split the rule lines/circles at their intersections and retain only pieces whose
two sides classify differently. This keeps diagnostics and guards aligned with
the actual classifier, including clipped arcs and three-way intersections.
"""
from dataclasses import dataclass
from functools import lru_cache
from math import atan2, cos, hypot, pi, sin, sqrt

from app.services import court_zones as rules

TAU = 2 * pi


def zone_family(zone: str) -> str | None:
    if zone == 'restricted':
        return 'restricted'
    for prefix in ('paint', 'mid', 'three'):
        if zone.startswith(prefix + '_'):
            return prefix
    return None


@dataclass(frozen=True)
class ZoneBoundary:
    zones: tuple[str, str]
    kind: str
    start: tuple[float, float]
    end: tuple[float, float]
    radius: float = 0
    start_angle: float = 0
    end_angle: float = 0

    @property
    def physical(self) -> bool:
        return zone_family(self.zones[0]) != zone_family(self.zones[1])

    def point(self, fraction: float = .5) -> tuple[float, float]:
        if self.kind == 'line':
            return tuple(a + fraction * (b-a) for a, b in zip(self.start, self.end))
        angle = self.start_angle + fraction * (self.end_angle-self.start_angle)
        return (rules.HOOP_X_FT + self.radius*cos(angle), rules.HOOP_Y_FT + self.radius*sin(angle))

    def normal(self, fraction: float = .5) -> tuple[float, float]:
        if self.kind == 'arc':
            angle = self.start_angle + fraction * (self.end_angle-self.start_angle)
            return cos(angle), sin(angle)
        dx, dy = self.end[0]-self.start[0], self.end[1]-self.start[1]
        length = hypot(dx, dy)
        return -dy/length, dx/length

    def distance(self, x: float, y: float) -> float:
        if self.kind == 'line':
            dx, dy = self.end[0]-self.start[0], self.end[1]-self.start[1]
            t = max(0, min(1, ((x-self.start[0])*dx+(y-self.start[1])*dy)/(dx*dx+dy*dy)))
            return hypot(x-self.start[0]-t*dx, y-self.start[1]-t*dy)
        angle = atan2(y-rules.HOOP_Y_FT, x-rules.HOOP_X_FT) % TAU
        if self.start_angle <= angle <= self.end_angle:
            return abs(hypot(x-rules.HOOP_X_FT, y-rules.HOOP_Y_FT)-self.radius)
        return min(hypot(x-a, y-b) for a, b in (self.start, self.end))


@lru_cache(maxsize=1)
def zone_boundaries() -> tuple[ZoneBoundary, ...]:
    width, height = rules.COURT_WIDTH_FT, rules.HALF_COURT_LENGTH_FT
    hx, hy = rules.HOOP_X_FT, rules.HOOP_Y_FT
    left, right = rules.PAINT_LEFT_X_FT, rules.PAINT_RIGHT_X_FT
    third = (right-left)/3
    xs = sorted({0., width, rules.CORNER_THREE_X_FT, width-rules.CORNER_THREE_X_FT,
                 left, right, left+third, right-third,
                 hx-rules.MID_CENTER_HALF_WIDTH_FT, hx+rules.MID_CENTER_HALF_WIDTH_FT,
                 hx-rules.THREE_CENTER_HALF_WIDTH_FT, hx+rules.THREE_CENTER_HALF_WIDTH_FT})
    ys = sorted({0., height, rules.BASELINE_MID_END_Y_FT,
                 rules.CORNER_THREE_END_Y_FT, rules.FREE_THROW_LINE_Y_FT})
    radii = (rules.RESTRICTED_RADIUS_FT, rules.THREE_POINT_RADIUS_FT)
    pieces = []

    def retain(kind, start, end, radius=0., angles=(0., 0.)):
        probe = ZoneBoundary(('', ''), kind, start, end, radius, *angles)
        x, y = probe.point()
        nx, ny = probe.normal()
        sides = [(x+sign*nx*1e-5, y+sign*ny*1e-5) for sign in (-1, 1)]
        if not all(0 < px < width and 0 < py < height for px, py in sides):
            return
        zones = tuple(sorted(rules.classify_zone(px, py).value for px, py in sides))
        if zones[0] != zones[1]:
            pieces.append(ZoneBoundary(zones, kind, start, end, radius, *angles))

    for x in xs[1:-1]:
        cuts = set(ys)
        for radius in radii:
            squared = radius*radius-(x-hx)**2
            if squared >= 0:
                cuts.update(v for v in (hy-sqrt(squared), hy+sqrt(squared)) if 0 < v < height)
        ordered = sorted(cuts)
        for a, b in zip(ordered, ordered[1:]):
            if b-a > 1e-8:
                retain('line', (x, a), (x, b))
    for y in ys[1:-1]:
        cuts = set(xs)
        for radius in radii:
            squared = radius*radius-(y-hy)**2
            if squared >= 0:
                cuts.update(v for v in (hx-sqrt(squared), hx+sqrt(squared)) if 0 < v < width)
        ordered = sorted(cuts)
        for a, b in zip(ordered, ordered[1:]):
            if b-a > 1e-8:
                retain('line', (a, y), (b, y))
    for radius in radii:
        angles = {0., TAU}
        for x in xs:
            squared = radius*radius-(x-hx)**2
            if squared >= 0:
                angles.update(atan2(y-hy, x-hx) % TAU for y in (hy-sqrt(squared), hy+sqrt(squared)))
        for y in ys:
            squared = radius*radius-(y-hy)**2
            if squared >= 0:
                angles.update(atan2(y-hy, x-hx) % TAU for x in (hx-sqrt(squared), hx+sqrt(squared)))
        ordered = sorted(angles)
        for a, b in zip(ordered, ordered[1:]):
            if b-a > 1e-8:
                start = (hx+radius*cos(a), hy+radius*sin(a))
                end = (hx+radius*cos(b), hy+radius*sin(b))
                retain('arc', start, end, radius, (a, b))
    return tuple(pieces)


def physical_clearance(x: float, y: float) -> float:
    """Distance to any true distance-band boundary or court edge."""
    return min(x, rules.COURT_WIDTH_FT-x, y, rules.HALF_COURT_LENGTH_FT-y,
               *(b.distance(x, y) for b in zone_boundaries() if b.physical))
