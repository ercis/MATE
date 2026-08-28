"""Turn a routed skeleton into a smooth path (backbone-v2).

The brief: paths are rounded, not sharp; a detour past an obstacle reads as
*one large arc* rather than two tight corners; and the sharpest corner on any
path is as gentle as the free space allows. Three rules deliver that:

1. **Maximize each radius**, bounded by `ObstacleField.clearance_at` at the
   corner and by the room its neighbours leave on the shared segment. An
   isolated corner at the end of a long run gets the full ``r_max``; only
   corners that actually crowd each other shrink.
2. **Merge close corner pairs.** Two corners less than ``merge_len`` apart emit
   a single cubic through both vertices instead of two arcs — one broad S with
   far gentler peak curvature. ``merge_len = 2 * r_max`` makes the rule fire
   exactly when the two arcs would otherwise have had to shrink below ``r_max``.
3. **Verify, then fall back.** A merge is accepted only if the sampled curve
   stays out of every inflated rect; otherwise the pair degrades to two
   independent arcs, which are safe by construction (see below).

Containment: for a corner ``C`` filleted with tangent distance ``t`` and turn
``θ``, every point of the arc lies within ``t`` of ``C`` — the arc midpoint sits
at ``t·(sec(θ/2) - 1)/tan(θ/2)`` (≈ 0.414·t at 90°). Bounding ``t`` by
``clearance_at(C)`` therefore keeps the arc outside every node's envelope
without a further test.

Output is an explicit ``M``/``L``/``C`` path string in final absolute
coordinates. The radius is an obstacle-derived quantity and the client has no
node index, so a "client fillets it" design would have to ship the radii
anyway; a cubic approximates a 90° circular arc to 0.027%, so SVG ``A``
commands would only add a second shape language next to the drift-fallback
bezier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geom import Point, collapse
from .obstacles import ObstacleField

# Bezier circle constant generalized to an arbitrary turn: k = 4/3 * tan(θ/4).
_ARC_K = 4.0 / 3.0

_CURVE_SAMPLES = 16
_MERGE_SAMPLES = 12


@dataclass(frozen=True)
class FilletResult:
    """Everything the wire and the metrics need for one routed edge."""

    path: str
    polyline: list[Point]
    arrow: tuple[float, float, float]  # x, y, angle in degrees
    label_at: Point
    min_radius: float | None  # None = the path is a straight line
    samples: list[Point]  # curve samples, for the overlap metric


def fillet_path(
    points: list[Point],
    field: ObstacleField,
    *,
    r_max: float,
    merge_len: float,
    clearance_margin: float,
    arrow_gap: float,
    ignore: frozenset[str] = frozenset(),
) -> FilletResult:
    """Smooth ``points`` (an axis-aligned skeleton, source port → target port)."""
    pts = collapse(points)
    if len(pts) < 2:
        only = pts[0] if pts else (0.0, 0.0)
        return FilletResult(
            path=f"M {_n(only[0])} {_n(only[1])}",
            polyline=[only],
            arrow=(only[0], only[1], 0.0),
            label_at=only,
            min_radius=None,
            samples=[only],
        )

    arrow = _arrow_pose(pts)
    trimmed = _trim_tail(pts, arrow_gap)
    label_at = _label_anchor(pts, field, ignore)

    if len(trimmed) == 2:
        path = (
            f"M {_n(trimmed[0][0])} {_n(trimmed[0][1])} L {_n(trimmed[1][0])} {_n(trimmed[1][1])}"
        )
        return FilletResult(
            path=path,
            polyline=trimmed,
            arrow=arrow,
            label_at=label_at,
            min_radius=None,
            samples=list(trimmed),
        )

    tangents = _tangent_distances(trimmed, field, r_max=r_max, margin=clearance_margin)
    merges = _merge_pairs(trimmed, tangents, field, merge_len=merge_len, r_max=r_max, ignore=ignore)
    return _emit(trimmed, tangents, merges, arrow, label_at)


# -- radius selection ------------------------------------------------------


def _tangent_distances(
    pts: list[Point], field: ObstacleField, *, r_max: float, margin: float
) -> list[float]:
    """Per-vertex tangent distance; 0 for the two endpoints.

    Seeded from the obstacle clearance, then reduced until every segment can
    pay for the corners at both of its ends. Reduction is proportional, so two
    equally-crowded corners shrink together instead of one starving the other.
    """
    n = len(pts)
    tangent = [0.0] * n
    for index in range(1, n - 1):
        room = field.clearance_at(pts[index][0], pts[index][1]) - margin
        turn = _turn_angle(pts[index - 1], pts[index], pts[index + 1])
        # t = r * tan(θ/2); clamp the radius first so `room` bounds the arc.
        radius = max(0.0, min(r_max, room))
        tangent[index] = radius * _half_turn_tan(turn)

    for _pass in range(4):
        changed = False
        for index in range(n - 1):
            length = _dist(pts[index], pts[index + 1])
            demand = tangent[index] + tangent[index + 1]
            if demand > length and demand > 0.0:
                scale = length / demand
                tangent[index] *= scale
                tangent[index + 1] *= scale
                changed = True
        if not changed:
            break
    return tangent


def _merge_pairs(
    pts: list[Point],
    tangent: list[float],
    field: ObstacleField,
    *,
    merge_len: float,
    r_max: float,
    ignore: frozenset[str],
) -> dict[int, float]:
    """Corner indices that start a merged pair → the tangent distance to use.

    Greedy left-to-right over corner pairs; a merged pair consumes both corners
    so a run of three yields one merge plus one plain arc (deterministic, and it
    never stacks control points into a degenerate hull).
    """
    merges: dict[int, float] = {}
    index = 1
    while index < len(pts) - 2:
        if _dist(pts[index], pts[index + 1]) >= merge_len:
            index += 1
            continue
        # Room the neighbouring corners leave on the entering/leaving segments.
        in_avail = _dist(pts[index - 1], pts[index]) - (tangent[index - 1] if index > 1 else 0.0)
        out_room = tangent[index + 2] if index + 2 < len(pts) - 1 else 0.0
        out_avail = _dist(pts[index + 1], pts[index + 2]) - out_room
        span = min(r_max, in_avail, out_avail)
        if span <= 0.0:
            index += 1
            continue
        start = _along(pts[index], pts[index - 1], span)
        end = _along(pts[index + 1], pts[index + 2], span)
        if _cubic_clear(start, pts[index], pts[index + 1], end, field, ignore):
            merges[index] = span
            index += 2
        else:
            index += 1
    return merges


# -- emission --------------------------------------------------------------


def _emit(
    pts: list[Point],
    tangent: list[float],
    merges: dict[int, float],
    arrow: tuple[float, float, float],
    label_at: Point,
) -> FilletResult:
    parts = [f"M {_n(pts[0][0])} {_n(pts[0][1])}"]
    samples: list[Point] = [pts[0]]
    min_radius = float("inf")

    index = 1
    while index < len(pts) - 1:
        if index in merges:
            span = merges[index]
            start = _along(pts[index], pts[index - 1], span)
            end = _along(pts[index + 1], pts[index + 2], span)
            parts.append(f"L {_n(start[0])} {_n(start[1])}")
            parts.append(_cubic(pts[index], pts[index + 1], end))
            curve = _sample_cubic(start, pts[index], pts[index + 1], end, _CURVE_SAMPLES)
            samples.extend(curve)
            min_radius = min(min_radius, _min_radius(start, pts[index], pts[index + 1], end))
            index += 2
            continue

        span = tangent[index]
        if span <= 0.0:
            parts.append(f"L {_n(pts[index][0])} {_n(pts[index][1])}")
            samples.append(pts[index])
            index += 1
            continue

        corner = pts[index]
        start = _along(corner, pts[index - 1], span)
        end = _along(corner, pts[index + 1], span)
        turn = _turn_angle(pts[index - 1], corner, pts[index + 1])
        k = _ARC_K * math.tan(turn / 4.0) * (span / _half_turn_tan(turn))
        c1 = _along(start, corner, k)
        c2 = _along(end, corner, k)
        parts.append(f"L {_n(start[0])} {_n(start[1])}")
        parts.append(_cubic(c1, c2, end))
        samples.extend(_sample_cubic(start, c1, c2, end, _CURVE_SAMPLES))
        min_radius = min(min_radius, _min_radius(start, c1, c2, end))
        index += 1

    last = pts[-1]
    if _dist(samples[-1], last) > 0.05:  # the final arc can already end on it
        parts.append(f"L {_n(last[0])} {_n(last[1])}")
        samples.append(last)

    return FilletResult(
        path=" ".join(parts),
        polyline=pts,
        arrow=arrow,
        label_at=label_at,
        min_radius=None if min_radius == float("inf") else min_radius,
        samples=samples,
    )


def _cubic(c1: Point, c2: Point, end: Point) -> str:
    return f"C {_n(c1[0])} {_n(c1[1])}, {_n(c2[0])} {_n(c2[1])}, {_n(end[0])} {_n(end[1])}"


# -- geometry helpers ------------------------------------------------------


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _along(origin: Point, toward: Point, distance: float) -> Point:
    length = _dist(origin, toward)
    if length <= 0.0:
        return origin
    t = min(distance, length) / length
    return (origin[0] + (toward[0] - origin[0]) * t, origin[1] + (toward[1] - origin[1]) * t)


def _half_turn_tan(turn: float) -> float:
    """``tan(θ/2)``, clamped. A near-180° reversal would send it to infinity."""
    if turn <= 0.0:
        return 0.0
    return min(math.tan(min(turn, math.radians(170.0)) / 2.0), 4.0)


def _turn_angle(a: Point, b: Point, c: Point) -> float:
    """Angle in radians the path turns through at ``b`` (0 = straight)."""
    ax, ay = b[0] - a[0], b[1] - a[1]
    bx, by = c[0] - b[0], c[1] - b[1]
    la = math.hypot(ax, ay)
    lb = math.hypot(bx, by)
    if la <= 0.0 or lb <= 0.0:
        return 0.0
    cosine = (ax * bx + ay * by) / (la * lb)
    return math.acos(max(-1.0, min(1.0, cosine)))


def _arrow_pose(pts: list[Point]) -> tuple[float, float, float]:
    tip = pts[-1]
    before = pts[-2]
    angle = math.degrees(math.atan2(tip[1] - before[1], tip[0] - before[0]))
    return (tip[0], tip[1], angle)


def _trim_tail(pts: list[Point], gap: float) -> list[Point]:
    """Stop the visible path short of the port so the arrowhead fills the gap."""
    if gap <= 0.0:
        return list(pts)
    out = list(pts)
    length = _dist(out[-2], out[-1])
    # Always leave a visible last millimetre, so the trim can never collapse the
    # final vertex into its predecessor and shift every index downstream.
    out[-1] = _along(out[-1], out[-2], min(gap, max(length - 1.0, 0.0)))
    return out


def _label_anchor(pts: list[Point], field: ObstacleField, ignore: frozenset[str]) -> Point:
    """Midpoint of the longest node-clear straight run.

    The count chip renders as DOM, so it can land on a node even when the path
    does not; anchoring it to a clear straight segment is what keeps it legible.
    """
    best: Point | None = None
    best_len = 0.0
    for index in range(len(pts) - 1):
        a, b = pts[index], pts[index + 1]
        length = _dist(a, b)
        if length <= best_len:
            continue
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        if field.stab_point(mid[0], mid[1], ignore):
            continue
        best = mid
        best_len = length
    if best is not None:
        return best
    index = len(pts) // 2
    a, b = pts[max(index - 1, 0)], pts[min(index, len(pts) - 1)]
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _sample_cubic(p0: Point, p1: Point, p2: Point, p3: Point, count: int) -> list[Point]:
    out: list[Point] = []
    for step in range(1, count + 1):
        t = step / count
        s = 1.0 - t
        out.append(
            (
                s * s * s * p0[0]
                + 3 * s * s * t * p1[0]
                + 3 * s * t * t * p2[0]
                + t * t * t * p3[0],
                s * s * s * p0[1]
                + 3 * s * s * t * p1[1]
                + 3 * s * t * t * p2[1]
                + t * t * t * p3[1],
            )
        )
    return out


def _min_radius(p0: Point, p1: Point, p2: Point, p3: Point) -> float:
    """Smallest radius of curvature over the cubic (sampled)."""
    worst = 0.0
    for step in range(_CURVE_SAMPLES + 1):
        t = step / _CURVE_SAMPLES
        s = 1.0 - t
        dx = 3 * s * s * (p1[0] - p0[0]) + 6 * s * t * (p2[0] - p1[0]) + 3 * t * t * (p3[0] - p2[0])
        dy = 3 * s * s * (p1[1] - p0[1]) + 6 * s * t * (p2[1] - p1[1]) + 3 * t * t * (p3[1] - p2[1])
        ddx = 6 * s * (p2[0] - 2 * p1[0] + p0[0]) + 6 * t * (p3[0] - 2 * p2[0] + p1[0])
        ddy = 6 * s * (p2[1] - 2 * p1[1] + p0[1]) + 6 * t * (p3[1] - 2 * p2[1] + p1[1])
        speed = math.hypot(dx, dy)
        if speed <= 1e-9:
            continue
        curvature = abs(dx * ddy - dy * ddx) / (speed**3)
        worst = max(worst, curvature)
    return float("inf") if worst <= 1e-9 else 1.0 / worst


def _cubic_clear(
    p0: Point, p1: Point, p2: Point, p3: Point, field: ObstacleField, ignore: frozenset[str]
) -> bool:
    for point in _sample_cubic(p0, p1, p2, p3, _MERGE_SAMPLES):
        if field.stab_point(point[0], point[1], ignore):
            return False
    return True


def _n(value: float) -> str:
    """Fixed-point, never scientific — an SVG path must stay parseable."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text
