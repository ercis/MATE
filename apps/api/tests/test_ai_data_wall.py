"""MATE AI data wall.

The chat assistant only ever sees module *outputs* and curated *aggregate*
metadata - never raw XES/parquet event rows. Coverage:

* ``_RestrictedEventLog`` denies every raw accessor.
* ``_make_context(restrict_event_log=True)`` wires that stub while the module
  cache (its own outputs) still works; the default path keeps full access.
* ``_activities_variants_block`` surfaces aggregate activities/variants only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID

FIXTURES = Path(__file__).parent / "fixtures"


async def _seed_log(client: AsyncClient, timeout: float = 10.0) -> str:
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    log_id = resp.json()["log_id"]
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/api/v1/event-logs/{log_id}")
        status = r.json()["status"]
        if status == "ready":
            return log_id
        if status == "failed":
            raise AssertionError(f"import failed: {r.json()}")
        await asyncio.sleep(0.05)
    raise AssertionError("import did not finish in time")


@pytest.mark.asyncio
async def test_restricted_event_log_denies_all_raw_access() -> None:
    from mate.api.modules.loader import _RestrictedEventLog

    el = _RestrictedEventLog()

    for coro in (
        el.pandas(),
        el.polars(),
        el.pm4py(),
        el.duckdb_fetch("SELECT 1"),
        el.materialize_parquet(),
    ):
        with pytest.raises(PermissionError):
            await coro

    for prop in ("events_path", "cases_path", "active_filter", "some_future_reader"):
        with pytest.raises(PermissionError):
            getattr(el, prop)

    # Entering it as an async context manager raises too.
    with pytest.raises(PermissionError):
        async with el:
            pass


@pytest.mark.asyncio
async def test_activities_variants_block_is_aggregate_only(client: AsyncClient) -> None:
    from mate.api.routes.ai import _activities_variants_block

    log_id = await _seed_log(client)
    block = await _activities_variants_block(log_id, TEST_USER_ID, None)

    assert block
    # sample.csv activities surface with their event counts.
    assert "register order" in block
    assert "Activities (" in block
    # Variant sequences appear as activity paths with case counts.
    assert "Top variants" in block
    assert "→" in block
    assert "cases:" in block


@pytest.mark.asyncio
async def test_make_context_restrict_flag(client_with_sample_mod: AsyncClient) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.event_log_access import EventLogAccess
    from mate.api.modules.loader import _RestrictedEventLog

    log_id = await _seed_log(client_with_sample_mod)
    loader = get_module_loader()
    mod_id = next(iter(loader.loaded))

    restricted = await loader._make_context(mod_id, log_id, TEST_USER_ID, restrict_event_log=True)
    assert isinstance(restricted.event_log, _RestrictedEventLog)
    with pytest.raises(PermissionError):
        await restricted.event_log.pandas()
    # The module's own cache still works under the wall.
    assert await restricted.cache.exists("missing-key") is False

    # Default (non-AI) path keeps full raw access.
    full = await loader._make_context(mod_id, log_id, TEST_USER_ID)
    assert isinstance(full.event_log, EventLogAccess)
