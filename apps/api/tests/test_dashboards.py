"""End-to-end tests for the Dashboards API (/api/v1/dashboards)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/event-logs/{log_id}")
        body = resp.json()
        if body["status"] == "ready":
            return
        if body["status"] == "failed":
            raise AssertionError(f"Import failed: {body.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError("Import did not finish")


async def _seed_log(client: AsyncClient) -> str:
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)
    return log_id


async def _seed_ocel_log(client: AsyncClient) -> str:
    with (FIXTURES / "sample.jsonocel").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.jsonocel", f, "application/json")},
            data={"name": "Sample OCEL"},
        )
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)
    return log_id


def _item(i: str, *, x: int = 0, y: int = 0) -> dict:
    return {
        "i": i,
        "module_id": "performance",
        "widget_id": "kpi-overview",
        "title": "KPIs",
        "x": x,
        "y": y,
        "w": 6,
        "h": 8,
        "config": {},
    }


@pytest.mark.asyncio
async def test_dashboard_crud_lifecycle(client: AsyncClient) -> None:
    # Create empty
    resp = await client.post("/api/v1/dashboards", json={"name": "  My board  "})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "My board"  # trimmed
    assert created["items"] == []
    dash_id = created["id"]

    # List shows it with card_count 0
    resp = await client.get("/api/v1/dashboards")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["id"] == dash_id and r["card_count"] == 0 for r in rows)

    # Bind a log + add cards
    log_id = await _seed_log(client)
    resp = await client.patch(
        f"/api/v1/dashboards/{dash_id}",
        json={"event_log_id": log_id, "items": [_item("a"), _item("b", y=8)]},
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["event_log_id"] == log_id
    assert len(detail["items"]) == 2
    assert detail["items"][0]["module_id"] == "performance"

    # List reflects card count
    resp = await client.get("/api/v1/dashboards")
    row = next(r for r in resp.json() if r["id"] == dash_id)
    assert row["card_count"] == 2

    # Get detail round-trips the geometry
    resp = await client.get(f"/api/v1/dashboards/{dash_id}")
    assert resp.json()["items"][1]["y"] == 8

    # Delete
    resp = await client.delete(f"/api/v1/dashboards/{dash_id}")
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/dashboards/{dash_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_rejects_unowned_log(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "bad", "event_log_id": "does-not-exist"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_empty_name_rejected(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/dashboards", json={"name": "   "})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dashboard_export_import_roundtrip(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "Exportable", "items": [_item("x")]},
    )
    dash_id = resp.json()["id"]

    resp = await client.get(f"/api/v1/dashboards/{dash_id}/export")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["kind"] == "mate.dashboard"
    assert doc["name"] == "Exportable"
    assert len(doc["items"]) == 1
    assert "event_log_id" not in doc  # export is log-agnostic

    # Re-import creates a fresh, independent board
    resp = await client.post("/api/v1/dashboards/import", json=doc)
    assert resp.status_code == 201, resp.text
    imported = resp.json()
    assert imported["id"] != dash_id
    assert imported["name"] == "Exportable"
    assert len(imported["items"]) == 1


@pytest.mark.asyncio
async def test_dashboard_defaults_to_case_centric(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/dashboards", json={"name": "Default model"})
    assert resp.status_code == 201, resp.text
    dash_id = resp.json()["id"]
    assert resp.json()["log_model"] == "case_centric"

    # Surfaces in list + detail.
    rows = (await client.get("/api/v1/dashboards")).json()
    assert next(r for r in rows if r["id"] == dash_id)["log_model"] == "case_centric"
    detail = (await client.get(f"/api/v1/dashboards/{dash_id}")).json()
    assert detail["log_model"] == "case_centric"


@pytest.mark.asyncio
async def test_object_centric_dashboard_roundtrips(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "OC board", "log_model": "object_centric"},
    )
    assert resp.status_code == 201, resp.text
    dash_id = resp.json()["id"]
    assert resp.json()["log_model"] == "object_centric"

    # Export carries the model and re-import preserves it.
    doc = (await client.get(f"/api/v1/dashboards/{dash_id}/export")).json()
    assert doc["log_model"] == "object_centric"
    imported = (await client.post("/api/v1/dashboards/import", json=doc)).json()
    assert imported["log_model"] == "object_centric"


@pytest.mark.asyncio
async def test_dashboard_log_model_locked_after_creation(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "Locked", "log_model": "case_centric"},
    )
    dash_id = resp.json()["id"]

    # PATCH ignores log_model - it isn't an editable field.
    resp = await client.patch(
        f"/api/v1/dashboards/{dash_id}",
        json={"name": "Locked still", "log_model": "object_centric"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["log_model"] == "case_centric"


@pytest.mark.asyncio
async def test_dashboard_rejects_mismatched_log_model(client: AsyncClient) -> None:
    log_id = await _seed_log(client)  # case-centric

    # Object-centric board cannot bind a case-centric log.
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "OC", "log_model": "object_centric", "event_log_id": log_id},
    )
    assert resp.status_code == 400, resp.text

    # Create unbound, then a mismatched bind via PATCH is also rejected.
    dash_id = (
        await client.post("/api/v1/dashboards", json={"name": "OC2", "log_model": "object_centric"})
    ).json()["id"]
    resp = await client.patch(f"/api/v1/dashboards/{dash_id}", json={"event_log_id": log_id})
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_dashboard_binds_matching_object_centric_log(client: AsyncClient) -> None:
    log_id = await _seed_ocel_log(client)
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "OC bound", "log_model": "object_centric", "event_log_id": log_id},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["event_log_id"] == log_id


# ── 12-column grid: coercion + the clamp-not-skip guard ──────────────────────


@pytest.mark.asyncio
async def test_stored_out_of_range_card_is_clamped_not_dropped(client: AsyncClient) -> None:
    """Tightening the grid bounds must never delete a user's card.

    Before the 12-column grid, `x` was valid up to 59. `dashboard_items()` used
    to skip any card that failed validation, so narrowing the bound would have
    silently wiped every card past column 11 the moment this shipped. It must
    clamp the geometry and keep the card instead.
    """
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Dashboard

    resp = await client.post("/api/v1/dashboards", json={"name": "Legacy geometry"})
    dash_id = resp.json()["id"]

    # Write pre-v2 geometry straight into the blob, with NO legacy marker - so
    # coercion cannot rescue it and only the clamp can.
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(Dashboard, dash_id)
        assert row is not None
        row.layout_json = {
            "items": [
                {**_item("wide"), "x": 48, "w": 24},
                {**_item("tall"), "y": 99999, "h": 400},
                {"i": "broken", "kind": "widget", "module_id": "performance"},  # no widget_id
            ],
            "settings": {"chrome": {"border": True}, "presets": []},
        }
        await session.commit()

    body = (await client.get(f"/api/v1/dashboards/{dash_id}")).json()
    cards = {c["i"]: c for c in body["items"]}
    # Both salvageable cards survive; only the genuinely malformed one is gone.
    assert set(cards) == {"wide", "tall"}
    assert cards["wide"]["w"] == 12
    assert cards["wide"]["x"] == 0  # pulled back so x + w stays on the grid
    assert cards["tall"]["y"] <= 400
    assert cards["tall"]["h"] <= 48


@pytest.mark.asyncio
async def test_out_of_grid_geometry_is_normalised_at_write(client: AsyncClient) -> None:
    """The schema accepts legacy-wide geometry; storage must still be 12-col.

    `DashboardItem` allows x up to 59 so old export files import (see
    test_dashboard_item_schema.py). That makes `layout_blob` the only place the
    12-column invariant can hold - so an unmarked, out-of-grid payload must
    come back clamped, not stored as sent.
    """
    resp = await client.post(
        "/api/v1/dashboards",
        json={
            "name": "Wide input",
            # No `granularity` marker, so coercion does not apply - only the clamp.
            "items": [{**_item("wide"), "x": 48, "w": 24}],
        },
    )
    assert resp.status_code == 201, resp.text
    card = resp.json()["items"][0]
    assert card["w"] == 12
    assert card["x"] == 0  # pulled back so x + w stays on the grid

    # And it is the *stored* value, not just the response.
    body = (await client.get(f"/api/v1/dashboards/{resp.json()['id']}")).json()
    assert (body["items"][0]["x"], body["items"][0]["w"]) == (0, 12)


@pytest.mark.asyncio
async def test_legacy_export_imports_onto_the_12_column_grid(client: AsyncClient) -> None:
    """A board file exported before the rework still carries `granularity`."""
    legacy_doc = {
        "kind": "mate.dashboard",
        "version": 1,
        "name": "From an old build",
        "log_model": "case_centric",
        # 24-column geometry: a right-half card.
        "items": [{**_item("half"), "x": 12, "w": 12}],
        "settings": {"granularity": "medium", "chrome": {"border": True}, "presets": []},
    }
    resp = await client.post("/api/v1/dashboards/import", json=legacy_doc)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    card = body["items"][0]
    assert (card["x"], card["w"]) == (6, 6)  # 24 -> 12 columns
    assert "granularity" not in body["settings"]
    assert body["settings"]["grid_version"] == 2

    # Re-exporting the migrated board yields a v2 file, so a second import
    # round-trip cannot rescale it again.
    doc = (await client.get(f"/api/v1/dashboards/{body['id']}/export")).json()
    assert "granularity" not in doc["settings"]
    reimported = (await client.post("/api/v1/dashboards/import", json=doc)).json()
    assert (reimported["items"][0]["x"], reimported["items"][0]["w"]) == (6, 6)
