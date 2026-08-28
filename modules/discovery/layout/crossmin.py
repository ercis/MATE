"""Cross-minimization (guide §6, paper Def. 15; Gansner et al. mincross).

Backbone-aware mode runs Gansner's wmedian + transpose **independently per
side** with the backbone pinned at order 0. The left side is optimized in
mirrored coordinates (distance from the spine), which makes it algorithmically
identical to the right side; crossings are invariant under mirroring.

Counting is inclusive per the guide's errata decision: edges from a side node
to the spine participate in that side's crossing counts (they genuinely cross
side edges), but only side nodes are ever permuted. Spine-to-spine chain edges
sit at constant coordinate 0 and can never cross anything on a side, so they
are skipped outright.
"""

from __future__ import annotations

from statistics import median

from .virtual import Expanded

_TRANSPOSE_ROUNDS = 100  # safety cap; Gansner converges in a handful of rounds

# Above this many expanded nodes (real + virtual), transpose's swap-and-recount
# passes turn quadratic-ish and can burn minutes on virtual-heavy graphs (a
# full unfiltered DFG under heuristic ranks spawns tens of thousands of dummy
# nodes). wmedian-only with few sweeps is the standard large-graph degradation.
_HUGE_NODE_COUNT = 1500
_HUGE_ITERATIONS = 2


def _inversions(sequence: list[int]) -> int:
    """Merge-sort inversion count — O(k log k) bilayer crossing counting."""
    if len(sequence) < 2:
        return 0

    def _sort(part: list[int]) -> tuple[list[int], int]:
        if len(part) < 2:
            return part, 0
        mid = len(part) // 2
        left, left_count = _sort(part[:mid])
        right, right_count = _sort(part[mid:])
        merged: list[int] = []
        count = left_count + right_count
        i = j = 0
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                merged.append(left[i])
                i += 1
            else:
                merged.append(right[j])
                count += len(left) - i
                j += 1
        merged.extend(left[i:])
        merged.extend(right[j:])
        return merged, count

    return _sort(sequence)[1]


def count_bilayer(pairs: list[tuple[int, int]]) -> int:
    """Crossings between two adjacent ranks given (upper_pos, lower_pos) pairs.

    Sorting by (upper, lower) makes shared-endpoint edges contribute zero,
    which matches the geometric count for straight adjacent-rank segments.
    """
    ordered = sorted(pairs)
    return _inversions([lower for _upper, lower in ordered])


class _SideProblem:
    """One side (or the whole graph, in unpinned mode) as mutable rank lists."""

    def __init__(
        self,
        movable_by_rank: dict[int, list[str]],
        fixed_positions: dict[str, int],
        edges: list[tuple[str, str]],
        ranks: dict[str, int],
    ) -> None:
        self.by_rank = {rank: list(nodes) for rank, nodes in movable_by_rank.items()}
        self.fixed = fixed_positions  # spine nodes at coordinate 0 (empty when unpinned)
        self.base = 1 if fixed_positions else 0  # movable coordinates start above the spine
        self.pos: dict[str, int] = dict(fixed_positions)
        for nodes in self.by_rank.values():
            for index, node in enumerate(nodes):
                self.pos[node] = self.base + index
        member = set(self.pos)
        self.edges_by_gap: dict[int, list[tuple[str, str]]] = {}
        self.neighbors_down: dict[str, list[str]] = {}
        self.neighbors_up: dict[str, list[str]] = {}
        for source, target in edges:
            if source not in member or target not in member:
                continue
            if source in fixed_positions and target in fixed_positions:
                continue  # spine-spine: constant 0, never crosses a side edge
            upper, lower = (source, target) if ranks[source] < ranks[target] else (target, source)
            self.edges_by_gap.setdefault(ranks[upper], []).append((upper, lower))
            self.neighbors_up.setdefault(lower, []).append(upper)
            self.neighbors_down.setdefault(upper, []).append(lower)

    def _reindex(self, rank: int) -> None:
        for index, node in enumerate(self.by_rank[rank]):
            self.pos[node] = self.base + index

    def crossings_at(self, gap_rank: int) -> int:
        pairs = self.edges_by_gap.get(gap_rank)
        if not pairs:
            return 0
        return count_bilayer([(self.pos[u], self.pos[v]) for u, v in pairs])

    def total_crossings(self) -> int:
        return sum(self.crossings_at(rank) for rank in self.edges_by_gap)

    def wmedian(self, downward: bool) -> None:
        ranks_order = sorted(self.by_rank)
        if not downward:
            ranks_order.reverse()
        for rank in ranks_order:
            nodes = self.by_rank[rank]
            if len(nodes) < 2:
                continue
            adjacency = self.neighbors_up if downward else self.neighbors_down

            def _key(node: str, adjacency: dict[str, list[str]] = adjacency) -> float:
                positions = [self.pos[n] for n in adjacency.get(node, [])]
                return float(median(positions)) if positions else float(self.pos[node])

            nodes.sort(key=_key)  # stable: equal medians keep their order
            self._reindex(rank)

    def transpose(self) -> None:
        for _round in range(_TRANSPOSE_ROUNDS):
            improved = False
            for rank in sorted(self.by_rank):
                nodes = self.by_rank[rank]
                for index in range(len(nodes) - 1):
                    before = self.crossings_at(rank - 1) + self.crossings_at(rank)
                    nodes[index], nodes[index + 1] = nodes[index + 1], nodes[index]
                    self._reindex(rank)
                    after = self.crossings_at(rank - 1) + self.crossings_at(rank)
                    if after < before:
                        improved = True
                    else:
                        nodes[index], nodes[index + 1] = nodes[index + 1], nodes[index]
                        self._reindex(rank)
            if not improved:
                return

    def optimize(self, iterations: int, *, use_transpose: bool = True) -> dict[int, list[str]]:
        best = {rank: list(nodes) for rank, nodes in self.by_rank.items()}
        best_crossings = self.total_crossings()
        for iteration in range(iterations):
            self.wmedian(downward=iteration % 2 == 0)
            if use_transpose:
                self.transpose()
            current = self.total_crossings()
            if current < best_crossings:
                best_crossings = current
                best = {rank: list(nodes) for rank, nodes in self.by_rank.items()}
        return best


def minimize_crossings(
    expanded: Expanded,
    orders: dict[str, int],
    *,
    pinned: bool,
    iterations: int,
) -> dict[str, int]:
    """Return improved integer orders (spine 0, left negative, right positive
    when pinned; a plain 0-based sequence per rank when unpinned)."""
    huge = len(expanded.ranks) > _HUGE_NODE_COUNT
    use_transpose = not huge
    if huge:
        iterations = min(iterations, _HUGE_ITERATIONS)
    if pinned:
        result = {node: 0 for node in expanded.backbone_set}
        spine_fixed = {node: 0 for node in expanded.backbone_set}
        for sign in (-1, 1):
            movable: dict[int, list[str]] = {}
            side_nodes = sorted(
                (node for node, order in orders.items() if order * sign > 0),
                key=lambda node: (expanded.ranks[node], abs(orders[node]), node),
            )
            for node in side_nodes:
                movable.setdefault(expanded.ranks[node], []).append(node)
            problem = _SideProblem(movable, spine_fixed, expanded.unit_edges, expanded.ranks)
            for nodes in problem.optimize(iterations, use_transpose=use_transpose).values():
                for index, node in enumerate(nodes, start=1):
                    result[node] = sign * index
        return result

    movable = {}
    for node in sorted(orders, key=lambda n: (expanded.ranks[n], orders[n], n)):
        movable.setdefault(expanded.ranks[node], []).append(node)
    problem = _SideProblem(movable, {}, expanded.unit_edges, expanded.ranks)
    result = {}
    for nodes in problem.optimize(iterations, use_transpose=use_transpose).values():
        for index, node in enumerate(nodes):
            result[node] = index
    return result


def total_crossings(expanded: Expanded, orders: dict[str, int]) -> int:
    """Global crossing count over every interlayer edge — the QM_ec metric."""
    by_gap: dict[int, list[tuple[int, int]]] = {}
    for source, target in expanded.unit_edges:
        upper, lower = (
            (source, target)
            if expanded.ranks[source] < expanded.ranks[target]
            else (target, source)
        )
        by_gap.setdefault(expanded.ranks[upper], []).append((orders[upper], orders[lower]))
    return sum(count_bilayer(pairs) for pairs in by_gap.values())
