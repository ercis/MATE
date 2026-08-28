"""Quality metrics (guide §9): errata-corrected defaults vs paper-compat."""

from __future__ import annotations

import pytest
from modules.discovery.layout.balance import (
    LEFT,
    RIGHT,
    balance_summary,
    components,
    initial_orders,
)
from modules.discovery.layout.crossmin import minimize_crossings
from modules.discovery.layout.metrics import _angle_deviation, quality_metrics
from modules.discovery.layout.model import LayoutNode, LayoutOptions, normalize_graph
from modules.discovery.layout.position import place
from modules.discovery.layout.virtual import insert_virtual_nodes

from .layout_fixtures import GOLDEN_BACKBONE, GOLDEN_RANKS, toy_graph


def _toy_layout():
    graph = toy_graph()
    expanded = insert_virtual_nodes(graph, GOLDEN_RANKS, GOLDEN_BACKBONE)
    component_list = components(expanded)
    # The paper's split ({F,...} + {G} + {L} left) — QM_bal is split-invariant
    # among the y=1 optima, but QM_no's order width is not, so the goldens
    # below are anchored to the published layout.
    by_member = {min(c): c for c in component_list}
    left = (by_member["F"], by_member["G"], by_member["L"])
    sides = {
        index: (LEFT if component in left else RIGHT)
        for index, component in enumerate(component_list)
    }
    orders = initial_orders(expanded, component_list, sides)
    orders = minimize_crossings(expanded, orders, pinned=True, iterations=12)
    x, y = place(expanded, orders, graph.sizes(), LayoutOptions(), straight_backbone=True)
    left_count, right_count = balance_summary(component_list, sides)
    return graph, expanded, orders, x, y, left_count, right_count


def test_angle_deviation_folding() -> None:
    assert _angle_deviation(0.0, 100.0) == 0.0  # vertical: on the 90° axis
    assert _angle_deviation(100.0, 0.0) == 0.0  # horizontal: on the 0° axis
    assert _angle_deviation(50.0, 50.0) == pytest.approx(1.0)  # 45°: worst case
    assert _angle_deviation(-50.0, 50.0) == pytest.approx(1.0)  # 135° folds too
    assert _angle_deviation(0.0, 0.0) == 0.0


def test_toy_metrics_default_formulas() -> None:
    graph, expanded, orders, x, y, left, right = _toy_layout()
    values = quality_metrics(
        expanded, orders, x, y, list(graph.edges), left, right, paper_compat=False
    )
    assert values["qm_be"] == 0.0  # no upward real edge (B<->G is horizontal)
    assert values["qm_ec"] == 0.0
    assert values["qm_bal"] == pytest.approx(1.0 - 1.0 / 11.0)  # left 6 / right 5
    # 14 real nodes over rank span 10 x order width (-2..2 -> 5).
    assert values["qm_no"] == pytest.approx(14.0 / 50.0)
    assert values["qm_el"] > 0.0
    assert 0.0 <= values["qm_eo"] <= 1.0


def test_toy_metrics_paper_compat_formulas() -> None:
    graph, expanded, orders, x, y, left, right = _toy_layout()
    values = quality_metrics(
        expanded, orders, x, y, list(graph.edges), left, right, paper_compat=True
    )
    assert values["qm_bal"] == 1.0  # literal |L - R|
    # Literal max(rank) x max(order): right extent only.
    assert values["qm_no"] == pytest.approx(14.0 / (10.0 * 2.0))


def test_single_vertical_edge_is_perfectly_oriented() -> None:
    nodes = [LayoutNode("a"), LayoutNode("b")]
    graph, _ = normalize_graph(nodes, [("a", "b")], None, None)
    expanded = insert_virtual_nodes(graph, {"a": 1, "b": 2}, [])
    orders = {"a": 0, "b": 0}
    x = {"a": 0.0, "b": 0.0}
    y = {"a": 0.0, "b": 149.0}
    values = quality_metrics(expanded, orders, x, y, [("a", "b")], 0, 0, paper_compat=False)
    assert values["qm_eo"] == 1.0
    assert values["qm_el"] == pytest.approx(149.0)
    assert values["qm_bal"] == 1.0  # empty sides count as balanced


def test_back_edge_counts_toward_qm_be() -> None:
    nodes = [LayoutNode("a"), LayoutNode("b")]
    graph, _ = normalize_graph(nodes, [("a", "b"), ("b", "a")], None, None)
    expanded = insert_virtual_nodes(graph, {"a": 1, "b": 2}, [])
    orders = {"a": 0, "b": 0}
    coords = {"a": 0.0, "b": 0.0}
    values = quality_metrics(
        expanded,
        orders,
        coords,
        {"a": 0.0, "b": 149.0},
        [("a", "b"), ("b", "a")],
        0,
        0,
        paper_compat=False,
    )
    assert values["qm_be"] == 1.0  # only (b, a) goes upward


def test_segments_none_keeps_the_v1_numbers() -> None:
    """`segments=` is backbone-v2's hook; omitting it must change nothing."""
    graph, expanded, orders, x, y, left_count, right_count = _toy_layout()
    edges = list(graph.edges)
    baseline = quality_metrics(expanded, orders, x, y, edges, left_count, right_count)
    explicit = quality_metrics(
        expanded, orders, x, y, edges, left_count, right_count, segments=None
    )
    assert baseline == explicit


def test_segments_measure_the_drawn_polyline_not_the_chain() -> None:
    """With a routed polyline, qm_el/qm_eo describe what is on screen."""
    nodes = [LayoutNode("a"), LayoutNode("b")]
    graph, _ = normalize_graph(nodes, [("a", "b")], None, None)
    expanded = insert_virtual_nodes(graph, {"a": 1, "b": 2}, [])
    orders = {"a": 0, "b": 0}
    x = {"a": 0.0, "b": 0.0}
    y = {"a": 0.0, "b": 149.0}

    # A two-corner detour: longer than the straight chain, still axis-aligned.
    detour = [(0.0, 0.0), (0.0, 74.5), (60.0, 74.5), (60.0, 149.0), (0.0, 149.0)]
    values = quality_metrics(
        expanded, orders, x, y, [("a", "b")], 0, 0, segments={("a", "b"): detour}
    )
    assert values["qm_el"] == pytest.approx(149.0 + 120.0)
    assert values["qm_eo"] == 1.0  # every segment is on an axis
