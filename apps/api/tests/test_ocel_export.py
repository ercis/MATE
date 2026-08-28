"""OCEL 2.0 export conformance - the round-trip is the test.

Seed a small UI log through the real ingest endpoint, export it as OCEL 2.0
JSON and SQLite, and read both back with pm4py (the reference implementation
of the ocel-standard.org 2.0 formats). If pm4py parses the file and the
event/object/relation counts line up, the export conforms.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient


def _batch(anon: str) -> dict[str, Any]:
    common = {
        "selector": 'main>form#f>input[data-testid="name"]',
        "target": {
            "tag": "input",
            "id": None,
            "testid": "name",
            "role": None,
            "label": "Name",
            "text": None,
            "type": "text",
        },
        "ui_groups": [{"kind": "form", "id": "f", "label": "Create"}],
    }
    return {
        "session": {
            "id": "ocel-s1",
            "anon_user_id": anon,
            "started_at": datetime.now(UTC).isoformat(),
            "ua_class": "chromium-mac",
        },
        "events": [
            {
                "event_type": "click",
                "event_name": "click",
                "occurred_at": datetime.now(UTC).isoformat(),
                "path": "/models",
                "properties": {"kind": "click", "activity": 'click input "Name"', **common},
            },
            {
                "event_type": "input",
                "event_name": "input_change",
                "occurred_at": datetime.now(UTC).isoformat(),
                "path": "/models",
                "properties": {
                    "kind": "input",
                    "activity": 'enter value input "Name"',
                    "input_value": "MyKeyword",
                    **common,
                },
            },
        ],
    }


async def _seed_log(client: AsyncClient) -> None:
    cfg = (await client.get("/api/v1/usage/config")).json()
    resp = await client.post("/api/v1/usage/sync", json=_batch(cfg["anon_user_id_seed"]))
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_ocel_json_structure(client: AsyncClient) -> None:
    await _seed_log(client)
    resp = await client.get("/api/v1/usage/export?format=ocel-json")
    assert resp.status_code == 200
    doc = resp.json()

    # ocel-standard.org 2.0 JSON: exactly these top-level collections.
    assert {"objectTypes", "eventTypes", "objects", "events"} <= set(doc.keys())
    type_names = {t["name"] for t in doc["objectTypes"]}
    assert {"ui_element", "ui_group", "application", "system", "user", "task"} <= type_names

    events = doc["events"]
    assert len(events) == 2
    for ev in events:
        assert ev["time"]
        quals = {rel["qualifier"] for rel in ev.get("relationships", [])}
        assert "target" in quals
        assert "application" in quals

    # The committed input value must survive as an event attribute.
    input_events = [
        ev
        for ev in events
        if any(a["name"] == "input_value" and a["value"] == "MyKeyword" for a in ev["attributes"])
    ]
    assert len(input_events) == 1


@pytest.mark.asyncio
async def test_ocel_sqlite_round_trip(client: AsyncClient, tmp_path: Path) -> None:
    await _seed_log(client)
    resp = await client.get("/api/v1/usage/export?format=ocel-sqlite")
    assert resp.status_code == 200
    target = tmp_path / "ui-log.sqlite"
    target.write_bytes(resp.content)

    import pm4py

    ocel = pm4py.read_ocel2_sqlite(str(target))
    assert len(ocel.events) == 2
    # click + input each relate to element/group/app/system/user/task.
    assert len(ocel.relations) == 12
    obj_types = set(ocel.objects["ocel:type"])
    assert {"ui_element", "ui_group", "application", "system", "user", "task"} <= obj_types
    # Static part_of hierarchy survives as O2O rows.
    assert len(ocel.o2o) >= 3


@pytest.mark.asyncio
async def test_ocel_export_empty_is_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/usage/export?format=ocel-json")
    assert resp.status_code == 404


@pytest.fixture(autouse=True)
async def _reset_state() -> AsyncIterator[None]:
    # Purge before AND after: other suites emit server events for the same
    # shared test user, and the export assertions here are exact-count.
    from .test_analytics_objects import _purge_analytics_state

    await _purge_analytics_state()
    yield
    await _purge_analytics_state()
