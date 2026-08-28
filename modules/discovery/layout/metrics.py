"""Layout quality metrics (guide §9, paper Def. 23).

Defaults are the errata-corrected formulas; ``paper_compat=True`` reproduces
the literal published ones so Appendix-A numbers can be replicated:

- ``qm_bal``  default ``1 - |L-R|/(L+R)`` (higher better), compat ``|L-R|``.
- ``qm_eo``   default averages the fold-to-45° deviation over drawn segments,
  compat uses the printed ``1/|N|`` denominator over straight edges.
- ``qm_el``   default sums drawn polyline lengths (through virtual waypoints),
  compat sums straight endpoint distances.
- ``qm_no``   default divides real nodes by rank span x full order width,
  compat by ``max(rank) * max(order)`` (right extent only).
"""

from __future__ import annotations

import math
from itertools import pairwise

from .crossmin import total_crossings
from .obstacles import ObstacleField
from .router import RoutedEdge, overlap_samples
from .virtual import Expanded


def _angle_deviation(dx: float, dy: float) -> float:
    """Fold the segment's angle to (1,0) onto [0,1]: 0 = axis-aligned, 1 = 45°."""
    if dx == 0.0 and dy == 0.0:
        return 0.0
    theta = abs(math.degrees(math.atan2(dy, dx)))  # [0, 180]
    return min(theta, abs(90.0 - theta), 180.0 - theta) / 45.0


def quality_metrics(
    expanded: Expanded,
    orders: dict[str, int],
    x: dict[str, float],
    y: dict[str, float],
    real_edges: list[tuple[str, str]],
    left_count: int,
    right_count: int,
    *,
    paper_compat: bool = False,
    segments: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
) -> dict[str, float]:
    """``segments`` (backbone-v2) supplies the *drawn* polyline per edge.

    When given, ``qm_el`` and ``qm_eo`` measure what is actually on screen
    instead of the virtual-node chain. Leaving it ``None`` reproduces the v1
    numbers exactly.

    Caveat when comparing algorithms: v2's ``qm_eo`` reads higher purely
    because an orthogonal skeleton is axis-aligned by construction. It is not a
    meaningful v1-vs-v2 headline.
    """
    ranks = expanded.ranks

    qm_be = float(sum(1 for source, target in real_edges if ranks[source] > ranks[target]))

    total_sides = left_count + right_count
    if paper_compat:
        qm_bal = float(abs(left_count - right_count))
    else:
        qm_bal = 1.0 - (abs(left_count - right_count) / total_sides if total_sides else 0.0)

    qm_ec = float(total_crossings(expanded, orders))

    segment_deviations: list[float] = []
    straight_deviation_sum = 0.0
    polyline_total = 0.0
    straight_total = 0.0
    for source, target in real_edges:
        drawn = segments.get((source, target)) if segments is not None else None
        if drawn is not None:
            points = drawn
        else:
            chain = expanded.chains.get((source, target), [source, target])
            points = [(x[node], y[node]) for node in chain]
        for (ax, ay), (bx, by) in pairwise(points):
            dx, dy = bx - ax, by - ay
            polyline_total += math.hypot(dx, dy)
            segment_deviations.append(_angle_deviation(dx, dy))
        dx, dy = x[target] - x[source], y[target] - y[source]
        straight_total += math.hypot(dx, dy)
        straight_deviation_sum += _angle_deviation(dx, dy)

    if paper_compat:
        qm_el = straight_total
        node_count = len(expanded.real_ids)
        qm_eo = 1.0 - (straight_deviation_sum / node_count if node_count else 0.0)
    else:
        qm_el = polyline_total
        qm_eo = 1.0 - (
            sum(segment_deviations) / len(segment_deviations) if segment_deviations else 0.0
        )

    rank_values = list(ranks.values())
    order_values = list(orders.values())
    if paper_compat:
        area = float(max(rank_values, default=0) * max(order_values, default=0))
    else:
        rank_span = (max(rank_values) - min(rank_values) + 1) if rank_values else 0
        order_span = (max(order_values) - min(order_values) + 1) if order_values else 0
        area = float(rank_span * order_span)
    qm_no = len(expanded.real_ids) / area if area > 0 else 0.0

    return {
        "qm_be": qm_be,
        "qm_bal": qm_bal,
        "qm_ec": qm_ec,
        "qm_el": qm_el,
        "qm_eo": qm_eo,
        "qm_no": qm_no,
    }


def route_metrics(
    routes: dict[tuple[str, str], RoutedEdge],
    field: ObstacleField,
    *,
    repairs: int = 0,
) -> dict[str, float]:
    """Routing quality for backbone-v2. ``qm_no_overlap`` is the hard one.

    Everything geometric is measured on the *emitted* curve samples, not on the
    skeleton — a fillet that bulges into a node is exactly the bug a
    skeleton-only check would miss.
    """
    if not routes:
        return {}

    overlaps = 0
    bends: list[int] = []
    radii: list[float] = []
    for (source, target), route in routes.items():
        ignore = frozenset((source, target))
        overlaps += overlap_samples(route, field, ignore)
        bends.append(route.bends)
        if route.geometry is not None and route.geometry.min_radius is not None:
            radii.append(route.geometry.min_radius)

    radii.sort()
    percentile_index = max(0, int(0.05 * (len(radii) - 1))) if radii else 0
    straight = sum(1 for value in bends if value == 0)
    return {
        "qm_no_overlap": float(overlaps),
        "qm_bends": float(sum(bends)),
        "qm_bends_max": float(max(bends, default=0)),
        "qm_straight_frac": straight / len(bends) if bends else 0.0,
        "qm_min_radius": radii[0] if radii else 0.0,
        "qm_curv_p05": radii[percentile_index] if radii else 0.0,
        "qm_repairs": float(repairs),
    }
