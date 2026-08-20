"""Activity/object-type matrix route test."""

from __future__ import annotations

import asyncio

from modules.ocel_discovery.module import OcelDiscoveryModule

from .conftest import FakeCtx


def test_activity_object_types(ctx: FakeCtx) -> None:
    out = asyncio.run(OcelDiscoveryModule().activity_object_types(ctx))  # type: ignore[arg-type]

    assert set(out["activities"]) == {"create order", "add item", "pay", "ship"}
    assert set(out["object_types"]) == {"order", "item"}

    cells = {(c["activity"], c["object_type"]): c for c in out["cells"]}
    # "add item" touches the order in e2 + e3 (2 events, 1 distinct object o1).
    assert cells[("add item", "order")]["events"] == 2
    assert cells[("add item", "order")]["objects"] == 1
    # "add item" touches items in e2 + e3 (2 events, 2 distinct objects i1/i2).
    assert cells[("add item", "item")]["events"] == 2
    assert cells[("add item", "item")]["objects"] == 2
    # "ship" is a single event e5 spanning both items.
    assert cells[("ship", "item")]["events"] == 1
    assert cells[("ship", "item")]["objects"] == 2
    # The order never participates in "ship".
    assert ("ship", "order") not in cells
