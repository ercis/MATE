"""Virtual chains (guide §4) and component balancing (guide §5) on the toy."""

from __future__ import annotations

from modules.discovery.layout.balance import (
    LEFT,
    RIGHT,
    assign_sides_greedy,
    assign_sides_subset_sum,
    balance_summary,
    components,
    initial_orders,
)
from modules.discovery.layout.model import LayoutNode, normalize_graph
from modules.discovery.layout.virtual import insert_virtual_nodes

from .layout_fixtures import (
    GOLDEN_BACKBONE,
    GOLDEN_COMPONENT_SIZES,
    GOLDEN_RANKS,
    toy_graph,
)


def _toy_expanded():
    return insert_virtual_nodes(toy_graph(), GOLDEN_RANKS, GOLDEN_BACKBONE)


def test_virtual_chain_counts_and_ranks() -> None:
    expanded = _toy_expanded()
    assert len(expanded.virtual_ids) == 7  # Σ(span-1) = 2 + 1 + 3 + 1

    def mid_ranks(edge: tuple[str, str]) -> list[int]:
        return [expanded.ranks[n] for n in expanded.chains[edge][1:-1]]

    assert mid_ranks(("C", "D")) == [5, 6]
    assert mid_ranks(("D", "E")) == [8]
    assert mid_ranks(("F", "E")) == [6, 7, 8]
    assert mid_ranks(("I", "D")) == [6]


def test_backbone_chain_virtuals_spliced_in_order() -> None:
    expanded = _toy_expanded()
    cd = expanded.chains[("C", "D")][1:-1]
    de = expanded.chains[("D", "E")][1:-1]
    assert expanded.backbone == [
        GOLDEN_BACKBONE[0],
        "A",
        "B",
        "C",
        *cd,
        "D",
        *de,
        "E",
        GOLDEN_BACKBONE[-1],
    ]
    assert [expanded.ranks[n] for n in expanded.backbone] == list(range(1, 11))


def test_upward_edge_uses_destination_minus_source_sign() -> None:
    # Errata: δ = sign(rank(v) - rank(u)); the printed formula flips the chain.
    nodes = [LayoutNode("a"), LayoutNode("b")]
    graph, _ = normalize_graph(nodes, [("a", "b")], None, None)
    expanded = insert_virtual_nodes(graph, {"a": 5, "b": 2}, [])
    assert [expanded.ranks[n] for n in expanded.chains[("a", "b")][1:-1]] == [4, 3]


def test_toy_component_sizes() -> None:
    expanded = _toy_expanded()
    component_list = components(expanded)
    assert [len(c) for c in component_list] == GOLDEN_COMPONENT_SIZES
    by_member = {min(c & expanded.real_ids, default=min(c)): c for c in component_list}
    assert len(by_member["F"]) == 4  # F + its three virtuals toward E
    assert {"H", "I"} <= by_member["H"]
    assert len(by_member["H"]) == 3  # + the (I,D) virtual
    assert by_member["J"] == frozenset({"J", "K"})
    assert by_member["G"] == frozenset({"G"})
    assert by_member["L"] == frozenset({"L"})


def test_subset_sum_balance_y_equals_one_heavier_left() -> None:
    expanded = _toy_expanded()
    component_list = components(expanded)
    sides = assign_sides_subset_sum(component_list)
    left_total, right_total = balance_summary(component_list, sides)
    assert (left_total, right_total) == (6, 5)  # y = 1, ties prefer a heavier left


def test_greedy_balance_stops_when_difference_stops_shrinking() -> None:
    fake = [frozenset(f"n{i}{j}" for j in range(size)) for i, size in enumerate([5, 3, 2, 1])]
    sides = assign_sides_greedy(fake)
    left_total, right_total = balance_summary(fake, sides)
    # Move 5 left (|5-6|=1 beats 11); moving 3 would regress to 5 — stop.
    assert (left_total, right_total) == (5, 6)


def test_initial_orders_match_paper_example() -> None:
    expanded = _toy_expanded()
    component_list = components(expanded)
    by_member = {min(c): c for c in component_list}
    # The paper's toy split: {F,...} + {G} + {L} left, {H,I,...} + {J,K} right.
    sides = {
        index: (LEFT if component in (by_member["F"], by_member["G"], by_member["L"]) else RIGHT)
        for index, component in enumerate(component_list)
    }
    orders = initial_orders(expanded, component_list, sides)

    assert all(orders[node] == 0 for node in expanded.backbone)
    rank8_left = sorted(
        (n for n in expanded.ranks if expanded.ranks[n] == 8 and orders[n] < 0),
        key=lambda n: orders[n],
        reverse=True,
    )
    # LN_8 = [v_F8, L] -> orders -1, -2 (component order, then id; 1-based).
    f_virtual_at_8 = next(n for n in by_member["F"] if expanded.ranks[n] == 8)
    assert rank8_left == [f_virtual_at_8, "L"]
    assert orders[f_virtual_at_8] == -1
    assert orders["L"] == -2
    # RN_5 = [I, J], RN_6 = [v_ID, K].
    assert orders["I"] == 1
    assert orders["J"] == 2
    id_virtual = next(iter(by_member["H"] - {"H", "I"}))
    assert orders[id_virtual] == 1
    assert orders["K"] == 2
