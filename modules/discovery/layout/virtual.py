"""Virtual (dummy) node generation (guide §4, paper Def. 12-13).

Every edge spanning more than one rank is replaced by a chain of unit edges
through per-rank virtual nodes, so cross-minimization and positioning only ever
see adjacent-rank edges — and edge polylines get one waypoint per crossed rank.

Errata honored: the chain rank formula needs ``δ = sign(rank(v) - rank(u))``
(destination minus source); the paper's printed ``sign(rank(u) - rank(v))``
contradicts its own worked example.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from .backbone import backbone_edges
from .model import LayoutGraph


@dataclass
class Expanded:
    """The graph after virtualization — what every later stage works on."""

    ranks: dict[str, int]
    real_ids: frozenset[str]
    virtual_ids: frozenset[str]
    unit_edges: list[tuple[str, str]]  # |Δrank| == 1
    horizontal_edges: list[tuple[str, str]]  # Δrank == 0
    chains: dict[tuple[str, str], list[str]]  # original edge -> [u, *virtuals, v]
    backbone: list[str]  # BN' — virtuals on backbone edges spliced in
    backbone_set: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        self.backbone_set = frozenset(self.backbone)

    def all_ids(self) -> list[str]:
        return sorted(self.ranks)


def insert_virtual_nodes(
    graph: LayoutGraph,
    ranks: dict[str, int],
    backbone: list[str],
) -> Expanded:
    """Expand multi-rank edges into virtual chains.

    Virtual ids never leave the server (clients only see their coordinates as
    waypoints), so the naming just has to dodge real activity ids.
    """
    real_ids = set(graph.node_ids())
    be = backbone_edges(backbone, graph.edge_set())
    bn = list(backbone)

    expanded_ranks = dict(ranks)
    virtual_ids: list[str] = []
    unit_edges: set[tuple[str, str]] = set()
    horizontal_edges: list[tuple[str, str]] = []
    chains: dict[tuple[str, str], list[str]] = {}
    counter = 0

    for source, target in sorted(graph.edges):
        span = abs(ranks[target] - ranks[source])
        if span == 0:
            horizontal_edges.append((source, target))
            chains[(source, target)] = [source, target]
            continue
        if span == 1:
            unit_edges.add((source, target))
            chains[(source, target)] = [source, target]
            continue

        direction = 1 if ranks[target] > ranks[source] else -1
        chain = [source]
        for step in range(1, span):
            counter += 1
            virtual = f"__virtual_{counter}__"
            while virtual in real_ids:  # a real activity could shadow the scheme
                counter += 1
                virtual = f"__virtual_{counter}__"
            virtual_ids.append(virtual)
            expanded_ranks[virtual] = ranks[source] + step * direction
            chain.append(virtual)
        chain.append(target)
        chains[(source, target)] = chain
        for chain_source, chain_target in pairwise(chain):
            unit_edges.add((chain_source, chain_target))
        if (source, target) in be:
            # Keep BN' contiguous: the backbone's own long edges carry their
            # virtuals inside the sequence, pinning them to order 0 later.
            index = bn.index(source)
            bn[index + 1 : index + 1] = chain[1:-1]

    return Expanded(
        ranks=expanded_ranks,
        real_ids=frozenset(real_ids),
        virtual_ids=frozenset(virtual_ids),
        unit_edges=sorted(unit_edges),
        horizontal_edges=sorted(horizontal_edges),
        chains=chains,
        backbone=bn,
    )
