"""Corner rounding for backbone-v2.

The brief was "not fully straight but smoothly rounded — one large arc rather
than going around the obstacle, and keep the tightest cornering to a minimum".
These tests pin the three mechanisms that deliver it: radii are maximized, close
corner pairs merge into a single sweep, and neither is allowed to bulge into a
node.
"""

from __future__ import annotations

import math
import re

from modules.discovery.layout.fillet import fillet_path
from modules.discovery.layout.geom import Point
from modules.discovery.layout.obstacles import build_field

# Only `M`, `L` and `C` — one shape language, so the client never has to parse
# a second one next to the drift-fallback bezier.
_PATH_GRAMMAR = re.compile(r"^M -?[\d.]+ -?[\d.]+(?: [LC](?: -?[\d.]+ -?[\d.]+,?){1,3})+$")


def _empty_field(nodes: dict[str, tuple[float, float]] | None = None):  # type: ignore[no-untyped-def]
    nodes = nodes or {}
    ranks = {node: 1 for node in nodes}
    xs = {node: point[0] for node, point in nodes.items()}
    ys = {node: point[1] for node, point in nodes.items()}
    sizes = {node: (220.0, 59.0) for node in nodes}
    return build_field(list(nodes), ranks, xs, ys, sizes, clearance=8.0, fallback_channel_h=90.0)


def _fillet(points: list[Point], field, **kwargs):  # type: ignore[no-untyped-def]
    options = {
        "r_max": 40.0,
        "merge_len": 80.0,
        "clearance_margin": 1.0,
        "arrow_gap": 0.0,
    }
    options.update(kwargs)
    return fillet_path(points, field, **options)  # type: ignore[arg-type]


def _dist_to_segment(point: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return math.hypot(point[0] - ax, point[1] - ay)
    t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq))
    return math.hypot(point[0] - (ax + t * dx), point[1] - (ay + t * dy))


def test_path_uses_only_move_line_and_cubic() -> None:
    result = _fillet([(0.0, 0.0), (0.0, 200.0), (400.0, 200.0), (400.0, 400.0)], _empty_field())
    assert _PATH_GRAMMAR.match(result.path), result.path


def test_a_straight_run_stays_a_straight_line() -> None:
    result = _fillet([(0.0, 0.0), (0.0, 300.0)], _empty_field())
    assert result.path == "M 0 0 L 0 300"
    assert result.min_radius is None  # no curvature at all


def test_curve_never_strays_further_than_the_radius_from_the_skeleton() -> None:
    """The containment argument, executable.

    Every point of a fillet lies within its tangent distance of the corner, so
    bounding that distance is what keeps the curve inside the corridor the
    router validated. If this holds, `qm_no_overlap` cannot be broken by the
    rounding alone.
    """
    skeleton: list[Point] = [(0.0, 0.0), (0.0, 200.0), (400.0, 200.0), (400.0, 400.0)]
    result = _fillet(skeleton, _empty_field(), r_max=40.0)
    for sample in result.samples:
        nearest = min(
            _dist_to_segment(sample, skeleton[i], skeleton[i + 1]) for i in range(len(skeleton) - 1)
        )
        assert nearest <= 40.0 + 1e-6, f"{sample} strayed {nearest:.2f}px off the route"


def test_close_corners_merge_into_one_sweep() -> None:
    """Two corners 40px apart: one broad arc, not two tight ones."""
    skeleton: list[Point] = [(0.0, 0.0), (0.0, 200.0), (40.0, 200.0), (40.0, 400.0)]
    merged = _fillet(skeleton, _empty_field(), merge_len=80.0)
    split = _fillet(skeleton, _empty_field(), merge_len=0.0)

    assert merged.path.count("C") == 1, merged.path
    assert split.path.count("C") == 2, split.path
    # "Keep the tightest cornering to a minimum": the merged sweep is gentler.
    assert merged.min_radius is not None and split.min_radius is not None
    assert merged.min_radius > split.min_radius


def test_far_apart_corners_stay_separate() -> None:
    skeleton: list[Point] = [(0.0, 0.0), (0.0, 200.0), (400.0, 200.0), (400.0, 400.0)]
    result = _fillet(skeleton, _empty_field(), merge_len=80.0)
    assert result.path.count("C") == 2, result.path


def test_radius_shrinks_to_the_room_a_nearby_node_leaves() -> None:
    """A node 20px from the corner caps the radius at 20px, not `r_max`."""
    corner: Point = (0.0, 0.0)
    # Node centred so its inflated left border sits 20px right of the corner.
    field = _empty_field({"N": (0.0 + 20.0 + 8.0 + 110.0, 0.0)})
    assert abs(field.clearance_at(*corner) - 20.0) < 1e-6

    tight = _fillet([(0.0, -300.0), corner, (-300.0, 0.0)], field, r_max=40.0)
    roomy = _fillet([(0.0, -300.0), corner, (-300.0, 0.0)], _empty_field(), r_max=40.0)
    assert tight.min_radius is not None and roomy.min_radius is not None
    assert tight.min_radius < roomy.min_radius
    assert tight.min_radius <= 20.0 + 1e-6


def test_arrow_pose_and_trim_come_from_the_final_tangent() -> None:
    result = _fillet([(0.0, 0.0), (0.0, 300.0)], _empty_field(), arrow_gap=10.0)
    x, y, angle = result.arrow
    assert (x, y) == (0.0, 300.0)  # the tip sits ON the port
    assert abs(angle - 90.0) < 1e-6  # pointing down
    assert result.polyline[-1] == (0.0, 290.0)  # the visible line stops short
