"""The obstacle index the backbone-v2 router queries.

Everything the router treats as "safe by construction" bottoms out here, so
these are the tests that keep that phrase honest.
"""

from __future__ import annotations

import math

from modules.discovery.layout.geom import Rect, point_rect_dist
from modules.discovery.layout.obstacles import build_field

_CLEARANCE = 8.0


def _field(rows: dict[int, list[tuple[str, float]]]):  # type: ignore[no-untyped-def]
    """Build a field from {rank: [(id, centre x), …]} on the standard pitch."""
    ranks: dict[str, int] = {}
    xs: dict[str, float] = {}
    ys: dict[str, float] = {}
    sizes: dict[str, tuple[float, float]] = {}
    for rank, entries in rows.items():
        for node, x in entries:
            ranks[node] = rank
            xs[node] = x
            ys[node] = float(rank) * 149.0
            sizes[node] = (220.0, 59.0)
    return build_field(
        list(ranks), ranks, xs, ys, sizes, clearance=_CLEARANCE, fallback_channel_h=90.0
    )


def test_free_intervals_are_the_complement_of_the_row() -> None:
    field = _field({1: [("A", 0.0), ("B", 280.0), ("C", 560.0)]})
    # Inside a node there is no free interval at all.
    assert field.free_interval(1, 0.0) is None
    assert field.free_interval(1, 280.0) is None
    # Between two nodes: bounded by their inflated borders, and ordered.
    gap = field.free_interval(1, 140.0)
    assert gap is not None
    assert gap == (118.0, 162.0)
    assert gap[0] < gap[1]
    # Outside the outermost nodes the row is unbounded — this is what makes the
    # router's level-2 escape hatch always feasible.
    left = field.free_interval(1, -1000.0)
    right = field.free_interval(1, 1000.0)
    assert left is not None and left[0] == -math.inf
    assert right is not None and right[1] == math.inf


def test_nearest_free_x_lands_inside_a_free_interval() -> None:
    field = _field({1: [("A", 0.0), ("B", 280.0), ("C", 560.0)]})
    for probe in (-500.0, 0.0, 130.0, 280.0, 420.0, 560.0, 900.0):
        x = field.nearest_free_x(1, probe, width=20.0)
        interval = field.free_interval(1, x)
        assert interval is not None, f"nearest_free_x({probe}) -> {x} is inside a node"
        assert interval[1] - interval[0] >= 20.0
    # Already free: the probe is returned untouched, so a good column is never
    # perturbed into a worse one.
    assert field.nearest_free_x(1, 140.0, width=20.0) == 140.0


def test_columns_and_channels_match_the_router_s_two_invariants() -> None:
    field = _field({1: [("A", 0.0), ("B", 280.0)], 2: [("C", 0.0)]})
    # (1) A column in the gap between two nodes is clear top to bottom.
    assert field.vertical_clear(140.0, -100.0, 500.0)
    # A column through a node is not.
    assert not field.vertical_clear(0.0, -100.0, 500.0)
    assert field.stab(1, 0.0) == ["A"]
    # (2) The band between two rank rows contains nothing at all.
    y_lo, y_hi = field.channel_between(1, 2)
    assert y_hi > y_lo
    assert field.horizontal_clear((y_lo + y_hi) / 2.0, -1000.0, 1000.0)
    # …and it is at least `v_gap` tall (the pitch minus one node height).
    assert y_hi - y_lo >= 89.0


def test_ignoring_an_endpoint_lets_a_route_leave_its_own_node() -> None:
    field = _field({1: [("A", 0.0)], 2: [("B", 0.0)]})
    # A run leaving A's bottom border is inside A's inflated rect by design.
    assert not field.vertical_clear(0.0, 178.5, 268.5)
    assert field.vertical_clear(0.0, 178.5, 268.5, frozenset({"A", "B"}))


def test_clearance_at_matches_brute_force() -> None:
    rows = {
        1: [("A", 0.0), ("B", 280.0), ("C", 560.0)],
        2: [("D", 140.0), ("E", 420.0)],
        4: [("F", 0.0)],
    }
    field = _field(rows)
    rects = [
        Rect.from_center(x, float(rank) * 149.0, 220.0, 59.0).inflate(_CLEARANCE)
        for rank, entries in rows.items()
        for _node, x in entries
    ]

    state = 12345
    for _sample in range(500):
        state = (state * 1103515245 + 12345) % (2**31)
        px = (state % 160_000) / 100.0 - 400.0
        state = (state * 1103515245 + 12345) % (2**31)
        py = (state % 100_000) / 100.0 - 200.0
        expected = min(point_rect_dist(px, py, rect) for rect in rects)
        assert abs(field.clearance_at(px, py) - expected) < 1e-6, (px, py)
