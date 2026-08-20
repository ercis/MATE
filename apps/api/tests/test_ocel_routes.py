"""OCEL data routes + cross-model 409 rejection.

Verifies the /ocel/* endpoints serve object-centric logs and that the two
models reject each other's endpoints - no endpoint ever serves both.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 10.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/event-logs/{log_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] == "ready":
            return last
        if last["status"] == "failed":
            raise AssertionError(f"Import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Import did not finish in {timeout}s - last state: {last}")


async def _upload(client: AsyncClient, filename: str, mime: str) -> str:
    with (FIXTURES / filename).open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": (filename, f, mime)},
            data={"name": filename},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)
    return log_id


@pytest.mark.asyncio
async def test_ocel_endpoints_serve_object_centric(client: AsyncClient) -> None:
    log_id = await _upload(client, "sample.jsonocel", "application/json")
    base = f"/api/v1/event-logs/{log_id}"

    overview = await client.get(f"{base}/ocel/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["events_count"] == 4
    assert body["object_types_count"] == 3
    assert {e["type"] for e in body["object_types"]} == {"order", "item", "customer"}

    otypes = await client.get(f"{base}/ocel/object-types")
    assert otypes.status_code == 200
    assert {e["type"] for e in otypes.json()} == {"order", "item", "customer"}

    objs = await client.get(f"{base}/ocel/objects", params={"object_type": "order"})
    assert objs.status_code == 200
    assert objs.json()["total"] == 1

    events = await client.get(f"{base}/ocel/events")
    assert events.status_code == 200
    assert events.json()["total"] == 4

    rels = await client.get(f"{base}/ocel/relationships")
    assert rels.status_code == 200
    assert rels.json()["total"] == 5


@pytest.mark.asyncio
async def test_case_centric_endpoints_reject_ocel(client: AsyncClient) -> None:
    log_id = await _upload(client, "sample.jsonocel", "application/json")
    base = f"/api/v1/event-logs/{log_id}"
    for path in ("/events", "/variants", "/activities", "/data-quality"):
        resp = await client.get(f"{base}{path}")
        assert resp.status_code == 409, f"{path}: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_ocel_endpoints_reject_case_centric(client: AsyncClient) -> None:
    log_id = await _upload(client, "sample.xes", "application/xml")
    base = f"/api/v1/event-logs/{log_id}"
    for path in ("/ocel/overview", "/ocel/object-types", "/ocel/objects", "/ocel/events"):
        resp = await client.get(f"{base}{path}")
        assert resp.status_code == 409, f"{path}: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_remap_rejects_ocel(client: AsyncClient) -> None:
    log_id = await _upload(client, "sample.jsonocel", "application/json")
    resp = await client.post(
        f"/api/v1/event-logs/{log_id}/remap",
        json={"case_id": "a", "activity": "b", "timestamp": "c"},
    )
    assert resp.status_code == 409
