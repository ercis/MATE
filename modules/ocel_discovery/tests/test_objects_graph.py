"""Object-graph route + aggregation tests."""

from __future__ import annotations

import asyncio

from modules.ocel_discovery.module import OcelDiscoveryModule, _aggregate_object_pairs

from .conftest import FakeCtx


def test_objects_graph_interaction(ctx: FakeCtx) -> None:
    out = asyncio.run(OcelDiscoveryModule().objects_graph(ctx, graph_type="object_interaction"))  # type: ignore[arg-type]

    assert out["graph_type"] == "object_interaction"
    assert out["directed"] is False
    types = {t["type"]: t for t in out["object_types"]}
    assert types["order"]["count"] == 1
    assert types["item"]["count"] == 2
    # Cross-type edge (item, order): distinct co-occurring pairs (o1,i1) + (o1,i2) = 2.
    cross = {(e["source"], e["target"]): e["count"] for e in out["edges"]}
    assert cross[("item", "order")] == 2
    # Intra-type: items i1 and i2 share event e5 → 1 pair, folded onto the node.
    assert types["item"]["intra_count"] == 1


def test_objects_graph_descendants(ctx: FakeCtx) -> None:
    out = asyncio.run(OcelDiscoveryModule().objects_graph(ctx, graph_type="object_descendants"))  # type: ignore[arg-type]

    assert out["directed"] is True
    assert {t["type"] for t in out["object_types"]} == {"order", "item"}
    assert all(e["directed"] is True for e in out["edges"])


def test_aggregate_object_pairs_undirected_dedupes() -> None:
    pairs = {("o1", "i1"), ("i1", "o1"), ("i1", "i2")}
    type_of = {"o1": "order", "i1": "item", "i2": "item"}

    edges, intra = _aggregate_object_pairs(pairs, type_of, directed=False)

    # (o1,i1) and (i1,o1) collapse to a single undirected pair.
    assert edges == {("item", "order"): 1}
    assert intra == {"item": 1}


def test_aggregate_object_pairs_directed_keeps_orientation() -> None:
    pairs = {("o1", "i1"), ("i1", "o1")}
    type_of = {"o1": "order", "i1": "item"}

    edges, _ = _aggregate_object_pairs(pairs, type_of, directed=True)

    assert edges == {("order", "item"): 1, ("item", "order"): 1}
