"""Object-centric (OCEL) ingest + isolation tests.

Asserts the two log models stay fully separate: an OCEL import writes the
ocel/* tables and NOT the case-centric root events.parquet/cases.parquet, and a
case-centric import keeps log_model="case_centric" with no ocel/* tables.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID

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


@pytest.mark.asyncio
async def test_ocel_round_trip_and_isolation(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.jsonocel"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.jsonocel", f, "application/json")},
            data={"name": "Sample OCEL"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]

    detail = await _wait_until_ready(client, log_id)
    assert detail["log_model"] == "object_centric"
    assert detail["source_format"] == "ocel"
    assert detail["events_count"] == 4
    assert detail["objects_count"] == 3
    assert detail["object_types_count"] == 3
    assert detail["relations_count"] == 5
    # Case-centric counts stay NULL - the case-centric path never ran.
    assert detail["cases_count"] is None
    assert detail["variants_count"] is None

    from mate.api.ingest.storage import log_paths

    paths = log_paths(log_id, TEST_USER_ID)
    # OCEL tables present...
    assert paths.ocel_events.exists()
    assert paths.ocel_objects.exists()
    assert paths.ocel_relations.exists()
    assert paths.ocel_o2o.exists()
    # ...and the case-centric root tables ABSENT (the isolation guard).
    assert not paths.events.exists()
    assert not paths.cases.exists()

    meta = json.loads(paths.meta.read_text())
    assert meta["log_model"] == "object_centric"
    assert "ocel_flag" not in meta
    assert meta["detected_schema"]["log_model"] == "object_centric"


@pytest.mark.asyncio
async def test_case_centric_stays_case_centric(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.xes"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xes", f, "application/xml")},
            data={"name": "Sample XES"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    assert detail["log_model"] == "case_centric"

    from mate.api.ingest.storage import log_paths

    paths = log_paths(log_id, TEST_USER_ID)
    assert paths.events.exists()
    assert paths.cases.exists()
    assert not paths.ocel_events.exists()


@pytest.mark.asyncio
async def test_ocel_json_plain_extension_autoroutes(client: AsyncClient) -> None:
    """An OCEL 2.0 log named plain `.json` is content-detected and routed to the
    object-centric path - no `.jsonocel` extension required."""
    fixture = FIXTURES / "sample_ocel.json"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample_ocel.json", f, "application/json")},
            data={"name": "Plain JSON OCEL"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    assert detail["log_model"] == "object_centric"
    assert detail["source_format"] == "ocel"
    assert detail["events_count"] == 4
    assert detail["cases_count"] is None

    from mate.api.ingest.storage import log_paths

    paths = log_paths(log_id, TEST_USER_ID)
    assert paths.ocel_events.exists()
    assert not paths.events.exists()
    meta = json.loads(paths.meta.read_text())
    assert meta["ocel_flavor"] == "json"


@pytest.mark.asyncio
async def test_ocel_xml_plain_extension_autoroutes(client: AsyncClient) -> None:
    """An OCEL 2.0 log named plain `.xml` routes to the object-centric path; the
    case-centric XES/generic-XML path is bypassed by content detection."""
    fixture = FIXTURES / "sample_ocel.xml"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample_ocel.xml", f, "application/xml")},
            data={"name": "Plain XML OCEL"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    assert detail["log_model"] == "object_centric"
    assert detail["source_format"] == "ocel"
    assert detail["events_count"] == 4

    from mate.api.ingest.storage import log_paths

    paths = log_paths(log_id, TEST_USER_ID)
    assert paths.ocel_events.exists()
    assert not paths.events.exists()
    meta = json.loads(paths.meta.read_text())
    assert meta["ocel_flavor"] == "xml"


@pytest.mark.asyncio
async def test_ocel_plain_json_reimport_recovers_flavor(client: AsyncClient) -> None:
    """Re-importing a plain-`.json` OCEL log recovers the reader flavor from
    meta.json (the .json suffix alone can't tell OCEL-json from OCEL-xml)."""
    fixture = FIXTURES / "sample_ocel.json"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample_ocel.json", f, "application/json")},
        )
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)

    reimport = await client.post(f"/api/v1/event-logs/{log_id}/reimport")
    assert reimport.status_code == 202, reimport.text
    detail = await _wait_until_ready(client, log_id)
    assert detail["log_model"] == "object_centric"
    assert detail["events_count"] == 4
