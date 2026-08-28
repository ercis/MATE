"""Primitive geometry for the backbone-v2 edge router.

Pure functions and value types only — no graph knowledge, no options. Kept
separate from `obstacles.py` so the containment proofs in `fillet.py` can be
unit-tested against the same primitives the router validates with.

Coordinate convention matches the rest of the package: +x right, +y down, and
a `Rect` is stored by its border coordinates (not centre + size), because every
consumer here asks "is this point/segment inside" rather than "where is the
middle".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Face = Literal["top", "bottom", "left", "right"]
Point = tuple[float, float]

# Segment/point comparisons run on floats that came out of a median sweep, so
# exact equality is never safe. One hundredth of a pixel is far below anything
# the renderer can show and far above float noise at these magnitudes.
EPS = 0.01


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp into ``[lo, hi]``, tolerating an inverted interval (returns ``lo``)."""
    if hi < lo:
        return lo
    return min(max(value, lo), hi)


@dataclass(frozen=True)
class Rect:
    """An axis-aligned box in border coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float

    @staticmethod
    def from_center(cx: float, cy: float, width: float, height: float) -> Rect:
        half_w = width / 2.0
        half_h = height / 2.0
        return Rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def inflate(self, amount: float) -> Rect:
        return Rect(self.x0 - amount, self.y0 - amount, self.x1 + amount, self.y1 + amount)

    def contains(self, px: float, py: float) -> bool:
        """Strict containment — a point exactly on the border is outside.

        Ports live *on* their own node's border, so a non-strict test would
        report every route as colliding with the node it starts from.
        """
        return self.x0 + EPS < px < self.x1 - EPS and self.y0 + EPS < py < self.y1 - EPS

    def face_point(self, face: Face, u: float) -> Point:
        """The point at parameter ``u`` in [0,1] along ``face``.

        ``u`` runs left→right on horizontal faces and top→bottom on vertical
        ones, so ``0.5`` is always the face centre.
        """
        t = clamp(u, 0.0, 1.0)
        if face == "top":
            return (self.x0 + t * self.width, self.y0)
        if face == "bottom":
            return (self.x0 + t * self.width, self.y1)
        if face == "left":
            return (self.x0, self.y0 + t * self.height)
        return (self.x1, self.y0 + t * self.height)

    def face_u(self, face: Face, coordinate: float) -> float:
        """Inverse of :meth:`face_point` — the ``u`` of a coordinate on ``face``."""
        if face in ("top", "bottom"):
            span = self.width
            return clamp((coordinate - self.x0) / span, 0.0, 1.0) if span > 0 else 0.5
        span = self.height
        return clamp((coordinate - self.y0) / span, 0.0, 1.0) if span > 0 else 0.5


def point_rect_dist(px: float, py: float, rect: Rect) -> float:
    """Euclidean distance from a point to a rect (0 when inside)."""
    dx = max(rect.x0 - px, 0.0, px - rect.x1)
    dy = max(rect.y0 - py, 0.0, py - rect.y1)
    return math.hypot(dx, dy)


def seg_rect_hit(ax: float, ay: float, bx: float, by: float, rect: Rect) -> bool:
    """Does the segment A→B enter the rect's interior?

    Liang-Barsky clipping. Touching a border does not count (see
    :meth:`Rect.contains`) — the router emits runs that graze a node's border
    by exactly `clearance` and those must not read as collisions.
    """
    dx = bx - ax
    dy = by - ay
    t0 = 0.0
    t1 = 1.0
    for delta, start, lo, hi in (
        (dx, ax, rect.x0 + EPS, rect.x1 - EPS),
        (dy, ay, rect.y0 + EPS, rect.y1 - EPS),
    ):
        if hi <= lo:
            return False  # degenerate after the epsilon inset: nothing to hit
        if abs(delta) < 1e-12:
            if start <= lo or start >= hi:
                return False
            continue
        near = (lo - start) / delta
        far = (hi - start) / delta
        if near > far:
            near, far = far, near
        t0 = max(t0, near)
        t1 = min(t1, far)
        if t0 >= t1:
            return False
    return True


def polyline_length(points: list[Point]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
    return total


def collapse(points: list[Point]) -> list[Point]:
    """Drop duplicate and collinear vertices, preserving the endpoints.

    The skeleton builder emits a jog per guide level whether or not the level
    actually moved; this is what turns those into real bends.
    """
    if len(points) < 3:
        return [p for i, p in enumerate(points) if i == 0 or _apart(points[i - 1], p)]

    out: list[Point] = [points[0]]
    for point in points[1:]:
        if not _apart(out[-1], point):
            continue
        if len(out) >= 2 and _collinear(out[-2], out[-1], point):
            out[-1] = point
            continue
        out.append(point)
    return out


def _apart(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) > EPS or abs(a[1] - b[1]) > EPS


def _collinear(a: Point, b: Point, c: Point) -> bool:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    scale = max(abs(b[0] - a[0]), abs(b[1] - a[1]), abs(c[0] - a[0]), abs(c[1] - a[1]), 1.0)
    return abs(cross) < EPS * scale
