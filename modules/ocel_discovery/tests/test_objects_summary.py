"""Object lifecycle summary route test."""

from __future__ import annotations

import asyncio

from modules.ocel_discovery.module import OcelDiscoveryModule

from .conftest import FakeCtx

_DAY = 86400.0


def test_objects_summary(ctx: FakeCtx) -> None:
    out = asyncio.run(OcelDiscoveryModule().objects_summary(ctx))  # type: ignore[arg-type]

    types = {t["type"]: t for t in out["types"]}
    assert types["order"]["objects"] == 1
    assert types["item"]["objects"] == 2

    # order o1: 2024-01-01 .. 2024-01-04 = 3 days (single object → median == value).
    assert types["order"]["median_duration_s"] == 3 * _DAY
    # items i1 (3d) + i2 (2d) → interpolated median 2.5 days.
    assert types["item"]["median_duration_s"] == 2.5 * _DAY

    # Longest-lived object is 3 days; o1 spans 4 events.
    assert out["top_objects"][0]["duration_s"] == 3 * _DAY
    o1 = next(t for t in out["top_objects"] if t["oid"] == "o1")
    assert o1["n_events"] == 4

    # No O2O relations in the sample → interacting degree unavailable.
    assert out["has_interacting"] is False
    assert all(t["avg_interacting"] is None for t in out["types"])
