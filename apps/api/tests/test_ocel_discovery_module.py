"""End-to-end: the ocel_discovery module mounts its routes and serves the
object-centric Petri net (and OC-DFG / summary) for an OCEL log.

The default test client points MODULES_DIR at an empty dir, so this test copies
the real `ocel_discovery` module into a tmp MODULES_DIR and loads it - it is an
``in_process`` module with no private packages, so no venv is built (cheap)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import (
    _override_current_user_for_tests,
    _seed_module_installs_for_test_user,
)

FIXTURES = Path(__file__).parent / "fixtures"
OCEL_DISCOVERY_SRC = Path(__file__).resolve().parents[3] / "modules" / "ocel_discovery"


@contextlib.asynccontextmanager
async def _ocel_discovery_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    dst = tmp_path / "modules" / "ocel_discovery"
    shutil.copytree(
        OCEL_DISCOVERY_SRC,
        dst,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"),
    )

    prev = os.environ.get("MODULES_DIR")
    os.environ["MODULES_DIR"] = str(tmp_path / "modules")
    from mate.api import config as cfg

    cfg.get_settings.cache_clear()
    shutil.rmtree(cfg.get_settings().uploaded_modules_dir, ignore_errors=True)
    try:
        from mate.api.main import create_app

        app = create_app()
        _override_current_user_for_tests(app)
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://testserver") as c,
            app.router.lifespan_context(app),
        ):
            await _seed_module_installs_for_test_user()
            yield c
    finally:
        if prev is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev
        cfg.get_settings.cache_clear()


@pytest.fixture
async def ocel_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with _ocel_discovery_client(tmp_path) as c:
        yield c


async def _upload_ocel(client: AsyncClient) -> str:
    with (FIXTURES / "sample.jsonocel").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.jsonocel", f, "application/json")},
            data={"name": "sample.jsonocel"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        st = (await client.get(f"/api/v1/event-logs/{log_id}")).json()["status"]
        if st == "ready":
            return log_id
        if st == "failed":
            raise AssertionError("OCEL import failed")
        await asyncio.sleep(0.05)
    raise AssertionError("OCEL import did not finish in time")


async def test_module_reports_frontend_panel(ocel_client: AsyncClient) -> None:
    resp = await ocel_client.get("/api/v1/modules")
    assert resp.status_code == 200, resp.text
    mod = next(m for m in resp.json() if m["id"] == "ocel_discovery")
    # The whole point of the fix: the panel is now declared in the manifest.
    assert mod["has_frontend"] is True
    assert "ocel_discovery.ocpn" in mod["provides"]


async def test_ocpn_route_serves_object_centric_petri_net(ocel_client: AsyncClient) -> None:
    log_id = await _upload_ocel(ocel_client)
    resp = await ocel_client.get(f"/api/v1/modules/ocel_discovery/ocpn?log_id={log_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["object_types"] == ["customer", "item", "order"]
    assert len(body["nets"]) == 3
    by_ot = {n["object_type"]: n for n in body["nets"]}

    # The order object has a multi-step lifecycle → a real net with marking.
    order = by_ot["order"]
    assert len(order["places"]) >= 1
    assert len(order["transitions"]) >= 1
    assert all(p["id"].startswith("order::") for p in order["places"])

    # Degenerate single-event object types still serialize (possibly empty),
    # never 500 - the per-object-type guard in _serialize_ocpn.
    for ot in ("item", "customer"):
        assert ot in by_ot
        assert isinstance(by_ot[ot]["places"], list)


async def test_summary_and_ocdfg_still_serve(ocel_client: AsyncClient) -> None:
    log_id = await _upload_ocel(ocel_client)

    summary = await ocel_client.get(f"/api/v1/modules/ocel_discovery/summary?log_id={log_id}")
    assert summary.status_code == 200, summary.text
    assert summary.json()["events_count"] == 4

    ocdfg = await ocel_client.get(f"/api/v1/modules/ocel_discovery/ocdfg?log_id={log_id}")
    assert ocdfg.status_code == 200, ocdfg.text
    assert "order" in ocdfg.json()["object_types"]
