"""Component balancing and initial orders (guide §5, paper Def. 8/14, Eqs. 8-10).

Removing the backbone splits the rest into weakly connected components; each
component goes wholly left or right of the spine. The paper's IP is plain
balanced number partitioning, so an exact subset-sum DP replaces the solver
(errata: the printed Eqs. 9/10 carry a stray quantifier — the intended pair is
``y ≥ ±Σ size(c)·(2·d_c - 1)``). Virtual nodes count toward component size:
that is what makes the balance meaningful for edge-routing space.
"""

from __future__ import annotations

import networkx as nx

from .virtual import Expanded

LEFT = -1
RIGHT = 1


def components(expanded: Expanded) -> list[frozenset[str]]:
    """Weakly connected components of the non-backbone remainder, ordered by
    (size desc, min id) — this ordering also fixes Def. 14's within-rank
    sequence, which reproduces the paper's toy initial orders."""
    kept = set(expanded.ranks) - expanded.backbone_set
    graph: nx.Graph = nx.Graph()
    graph.add_nodes_from(kept)
    for source, target in [*expanded.unit_edges, *expanded.horizontal_edges]:
        if source in kept and target in kept:
            graph.add_edge(source, target)
    found = [frozenset(component) for component in nx.connected_components(graph)]
    return sorted(found, key=lambda component: (-len(component), min(component)))


def assign_sides_subset_sum(component_list: list[frozenset[str]]) -> dict[int, int]:
    """Exact balanced partition; ties prefer the heavier side on the LEFT so
    the paper's toy split (left 6 / right 5, y = 1) reproduces."""
    sizes = [len(component) for component in component_list]
    total = sum(sizes)
    reachable: dict[int, tuple[int, ...]] = {0: ()}
    for index, size in enumerate(sizes):
        for value, chosen in sorted(reachable.items()):
            candidate = value + size
            if candidate not in reachable:
                reachable[candidate] = (*chosen, index)
    best = min(reachable, key=lambda value: (abs(2 * value - total), -value))
    left = set(reachable[best])
    return {index: (LEFT if index in left else RIGHT) for index in range(len(sizes))}


def assign_sides_greedy(component_list: list[frozenset[str]]) -> dict[int, int]:
    """Mennens-2019 placement (guide §10): everything right, then move
    components left in descending size order until the difference stops
    shrinking."""
    sizes = [len(component) for component in component_list]
    sides = {index: RIGHT for index in range(len(sizes))}
    left_total, right_total = 0, sum(sizes)
    for index in sorted(range(len(sizes)), key=lambda i: (-sizes[i], i)):
        moved = abs((left_total + sizes[index]) - (right_total - sizes[index]))
        if moved >= abs(left_total - right_total):
            break
        sides[index] = LEFT
        left_total += sizes[index]
        right_total -= sizes[index]
    return sides


def initial_orders(
    expanded: Expanded,
    component_list: list[frozenset[str]],
    sides: dict[int, int],
) -> dict[str, int]:
    """Def. 14: backbone at 0; per rank, left components fill -1, -2, … and
    right components +1, +2, … (1-based, per the paper's worked example).
    Sequence within a rank: component order, then id."""
    orders = {node: 0 for node in expanded.backbone_set}
    per_rank_side: dict[tuple[int, int], list[str]] = {}
    for index, component in enumerate(component_list):
        side = sides[index]
        for node in sorted(component):
            per_rank_side.setdefault((expanded.ranks[node], side), []).append(node)
    # Nodes were appended component-by-component in component order; within a
    # component the sorted() above fixes ties by id.
    for (_rank, side), nodes in per_rank_side.items():
        for position, node in enumerate(nodes, start=1):
            orders[node] = side * position
    return orders


def balance_summary(component_list: list[frozenset[str]], sides: dict[int, int]) -> tuple[int, int]:
    """(left node count, right node count) — feeds QM_bal."""
    left_total = sum(len(c) for i, c in enumerate(component_list) if sides[i] == LEFT)
    right_total = sum(len(c) for i, c in enumerate(component_list) if sides[i] == RIGHT)
    return left_total, right_total
