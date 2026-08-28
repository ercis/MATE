"""Cross-minimization (guide §6) and positioning (guide §7) on the toy.

These stages are exercised on the golden IP ranks directly so the assertions
hold with or without a solver installed.
"""

from __future__ import annotations

from itertools import pairwise

from modules.discovery.layout.balance import (
    LEFT,
    RIGHT,
    assign_sides_subset_sum,
    components,
    initial_orders,
)
from modules.discovery.layout.crossmin import count_bilayer, minimize_crossings, total_crossings
from modules.discovery.layout.model import LayoutOptions
from modules.discovery.layout.position import place
from modules.discovery.layout.virtual import insert_virtual_nodes

from .layout_fixtures import GOLDEN_BACKBONE, GOLDEN_RANKS, toy_graph


def _expanded():
    return insert_virtual_nodes(toy_graph(), GOLDEN_RANKS, GOLDEN_BACKBONE)


def _paper_sides(expanded, component_list):
    by_member = {min(c): c for c in component_list}
    left = (by_member["F"], by_member["G"], by_member["L"])
    return {
        index: (LEFT if component in left else RIGHT)
        for index, component in enumerate(component_list)
    }


def test_count_bilayer() -> None:
    assert count_bilayer([(1, 2), (2, 1)]) == 1
    assert count_bilayer([(1, 1), (2, 2)]) == 0
    assert count_bilayer([(0, 0), (0, 1)]) == 0  # shared upper endpoint never crosses


def test_toy_paper_split_reaches_zero_crossings() -> None:
    expanded = _expanded()
    component_list = components(expanded)
    sides = _paper_sides(expanded, component_list)
    orders = initial_orders(expanded, component_list, sides)
    # Before optimization: 1 left crossing (the F-chain vs D->L) and 1 right
    # crossing (H->I vs C->J). The guide's rank-6 swap only becomes necessary
    # after the rank-5 swap shifts the crossing down a gap.
    assert total_crossings(expanded, orders) == 2
    optimized = minimize_crossings(expanded, orders, pinned=True, iterations=12)
    assert total_crossings(expanded, optimized) == 0
    assert all(optimized[node] == 0 for node in expanded.backbone)


def test_toy_dp_split_reaches_zero_crossings() -> None:
    expanded = _expanded()
    component_list = components(expanded)
    sides = assign_sides_subset_sum(component_list)
    orders = initial_orders(expanded, component_list, sides)
    optimized = minimize_crossings(expanded, orders, pinned=True, iterations=12)
    assert total_crossings(expanded, optimized) == 0


def test_sides_never_swap_during_crossmin() -> None:
    expanded = _expanded()
    component_list = components(expanded)
    orders = initial_orders(expanded, component_list, assign_sides_subset_sum(component_list))
    optimized = minimize_crossings(expanded, orders, pinned=True, iterations=12)
    for node, order in orders.items():
        if order < 0:
            assert optimized[node] < 0
        elif order > 0:
            assert optimized[node] > 0


def _place_toy():
    expanded = _expanded()
    component_list = components(expanded)
    orders = initial_orders(expanded, component_list, _paper_sides(expanded, component_list))
    orders = minimize_crossings(expanded, orders, pinned=True, iterations=12)
    graph = toy_graph()
    x, y = place(expanded, orders, graph.sizes(), LayoutOptions(), straight_backbone=True)
    return expanded, orders, x, y


def test_backbone_center_variance_is_zero() -> None:
    expanded, _orders, x, _y = _place_toy()
    spine = [x[node] for node in expanded.backbone]
    assert max(spine) - min(spine) == 0.0


def test_row_pitch_matches_celonis_grid() -> None:
    expanded, _orders, _x, y = _place_toy()
    # y pitch = max node height (59) + v_gap (90) = 149, identical to the
    # client-side Celonis layout so mode switches morph instead of rescaling.
    for node, rank in expanded.ranks.items():
        assert y[node] == rank * 149.0


def test_sides_stay_on_their_side_of_the_spine() -> None:
    expanded, orders, x, _y = _place_toy()
    spine_x = x[expanded.backbone[0]]
    for node, order in orders.items():
        if order < 0:
            assert x[node] < spine_x
        elif order > 0:
            assert x[node] > spine_x


def test_no_overlaps_within_a_rank() -> None:
    expanded, orders, x, _y = _place_toy()
    graph = toy_graph()
    sizes = graph.sizes()

    def width(node: str) -> float:
        return sizes[node][0] if node in sizes else 1.0

    by_rank: dict[int, list[str]] = {}
    for node, rank in expanded.ranks.items():
        by_rank.setdefault(rank, []).append(node)
    for nodes in by_rank.values():
        nodes.sort(key=lambda n: (orders[n], n))
        for left_node, right_node in pairwise(nodes):
            required = (width(left_node) + width(right_node)) / 2
            assert x[right_node] - x[left_node] >= required - 1e-6


def test_terminals_sit_on_the_spine() -> None:
    expanded, _orders, x, _y = _place_toy()
    spine_x = x["A"]
    # Terminals are backbone members: their centers share the spine column even
    # though they are narrower than activity cards.
    assert x[expanded.backbone[0]] == spine_x
    assert x[expanded.backbone[-1]] == spine_x
