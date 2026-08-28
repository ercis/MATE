"""Node positioning (guide §7, paper Def. 16-21).

Works in pixel space directly (the paper's fixed w=200/h=150 grid, adapted to
the caller's node sizes): x pitch = widest node + h_gap, y pitch = tallest node
+ v_gap — the same 280x149 grid the client-side Celonis layout uses, so
layout-mode switches morph instead of rescaling.

`packcutBN` deviation from the printed formula: the paper computes
``base + width(bn)/2`` per node, which only yields one shared coordinate when
all backbone widths are equal (their label-width nodes vary, ours differ only
for terminals). A truly vertical spine through the node *centers* is the whole
point of the step — and what a bottom/top-handle canvas needs — so every
backbone node gets one common center derived from the widest backbone node.
"""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import median

from .model import LayoutOptions
from .virtual import Expanded

_VIRTUAL_SIZE = 1.0  # virtual nodes take part in spacing with a hairline width

# Above this many expanded nodes, `packcut`'s inner walk (every node right of a
# gap, per gap) turns quadratic and can burn minutes on virtual-heavy graphs.
# Median + spine + outward separation alone stays near-linear and readable.
_HUGE_NODE_COUNT = 1500


def _dimensions(
    expanded: Expanded, sizes: dict[str, tuple[float, float]]
) -> tuple[dict[str, float], dict[str, float]]:
    widths: dict[str, float] = {}
    heights: dict[str, float] = {}
    for node in expanded.ranks:
        widths[node], heights[node] = sizes.get(node, (_VIRTUAL_SIZE, _VIRTUAL_SIZE))
    return widths, heights


def place(
    expanded: Expanded,
    orders: dict[str, int],
    sizes: dict[str, tuple[float, float]],
    opts: LayoutOptions,
    *,
    straight_backbone: bool,
) -> tuple[dict[str, float], dict[str, float]]:
    """Assign center coordinates. ``straight_backbone`` selects the paper's
    left→packcutBN→right driver; off = plain median + packcut over every node
    (the Mennens/Gansner baseline, no backbone awareness)."""
    widths, heights = _dimensions(expanded, sizes)
    real_widths = [widths[n] for n in expanded.real_ids] or [220.0]
    real_heights = [heights[n] for n in expanded.real_ids] or [59.0]
    x_pitch = max(real_widths) + opts.h_gap
    y_pitch = max(real_heights) + opts.v_gap
    eps = opts.h_gap

    x = {node: float(orders[node]) * x_pitch for node in expanded.ranks}
    y = {node: float(rank) * y_pitch for node, rank in expanded.ranks.items()}

    by_rank: dict[int, list[str]] = {}
    for node in sorted(expanded.ranks, key=lambda n: (expanded.ranks[n], orders[n], n)):
        by_rank.setdefault(expanded.ranks[node], []).append(node)
    left_neighbor: dict[str, str | None] = {}
    for nodes in by_rank.values():
        previous: str | None = None
        for node in nodes:
            left_neighbor[node] = previous
            previous = node

    up_neighbors: dict[str, list[str]] = {}
    for source, target in expanded.unit_edges:
        upper, lower = (
            (source, target)
            if expanded.ranks[source] < expanded.ranks[target]
            else (target, source)
        )
        up_neighbors.setdefault(lower, []).append(upper)

    def _medianpos(node: str) -> float:
        above = up_neighbors.get(node)
        if not above:
            return x[node]
        return float(median(x[neighbor] for neighbor in above))

    def _packcut(nodes: list[str]) -> None:
        ordered = sorted(nodes, key=lambda n: (x[n], n))
        for index in range(len(ordered) - 1):
            gap = x[ordered[index + 1]] - x[ordered[index]]
            if gap <= 0:
                continue
            for node in ordered[index + 1 :]:
                neighbor = left_neighbor[node]
                bound = (
                    -math.inf
                    if neighbor is None
                    else x[neighbor] + (widths[neighbor] + widths[node]) / 2 + eps
                )
                x[node] = max(x[node] - gap, bound)

    def _min_separation(left_node: str, right_node: str) -> float:
        return (widths[left_node] + widths[right_node]) / 2 + eps

    def _separate_outward() -> None:
        """Restore per-rank separation walking outward from the spine.

        `packcut`'s bound only re-separates nodes it happens to pull; a median
        sweep can park a side node ON the spine column with no positive gap
        left to trigger the bound. Sides must also never cross the spine, so
        the walk is directional: right side pushed right, left side left.
        """
        for nodes in by_rank.values():
            spine_index = next((i for i, node in enumerate(nodes) if orders[node] == 0), None)
            if spine_index is None:
                _separate_rightward(nodes)
                continue
            for i in range(spine_index + 1, len(nodes)):
                x[nodes[i]] = max(
                    x[nodes[i]], x[nodes[i - 1]] + _min_separation(nodes[i - 1], nodes[i])
                )
            for i in range(spine_index - 1, -1, -1):
                x[nodes[i]] = min(
                    x[nodes[i]], x[nodes[i + 1]] - _min_separation(nodes[i], nodes[i + 1])
                )

    def _separate_rightward(nodes: list[str]) -> None:
        """Plain left-to-right overlap removal along one rank."""
        for previous, node in pairwise(nodes):
            x[node] = max(x[node], x[previous] + _min_separation(previous, node))

    def _packcut_backbone() -> None:
        margins: list[float] = []
        for node in expanded.backbone:
            neighbor = left_neighbor[node]
            if neighbor is not None:
                margins.append(x[neighbor] + widths[neighbor] / 2 + eps)
        if not margins:
            return  # empty left side: the spine is already a shared column
        center = max(margins) + max(widths[node] for node in expanded.backbone) / 2
        for node in expanded.backbone:
            x[node] = center

    huge = len(expanded.ranks) > _HUGE_NODE_COUNT
    iterations = 1 if huge else opts.position_iterations

    if straight_backbone:
        movable = [
            node
            for rank in sorted(by_rank)
            for node in by_rank[rank]
            if node not in expanded.backbone_set
        ]
        left_side = [node for node in expanded.ranks if orders[node] < 0]
        right_side = [node for node in expanded.ranks if orders[node] > 0]
        for _iteration in range(iterations):
            snapshot = dict(x)
            for node in movable:
                x[node] = _medianpos(node)
            if not huge:
                _packcut(left_side)
            _packcut_backbone()
            if not huge:
                _packcut(right_side)
            _separate_outward()
            if max((abs(x[n] - snapshot[n]) for n in x), default=0.0) < 1.0:
                break
    else:
        every = [node for rank in sorted(by_rank) for node in by_rank[rank]]
        for _iteration in range(iterations):
            snapshot = dict(x)
            for node in every:
                x[node] = _medianpos(node)
            if not huge:
                _packcut(every)
            for nodes in by_rank.values():
                _separate_rightward(nodes)
            if max((abs(x[n] - snapshot[n]) for n in x), default=0.0) < 1.0:
                break

    return x, y
