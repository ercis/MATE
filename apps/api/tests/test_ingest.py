from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 5.0) -> dict:
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
async def test_xes_round_trip(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.xes"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xes", f, "application/xml")},
            data={"name": "Sample XES"},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["log_id"]
    assert body["job_id"]

    detail = await _wait_until_ready(client, body["log_id"])
    assert detail["events_count"] == 9
    assert detail["cases_count"] == 3
    assert detail["variants_count"] == 2  # case-1/case-3 share a variant; case-2 cancels

    # Parquet artefacts on disk
    from mate.api.ingest.storage import log_paths

    from .conftest import TEST_USER_ID

    paths = log_paths(body["log_id"], TEST_USER_ID)
    assert paths.events.exists()
    assert paths.cases.exists()
    assert paths.meta.exists()

    meta = json.loads(paths.meta.read_text())
    assert meta["source_format"] == "xes"
    assert meta["events_count"] == 9


@pytest.mark.asyncio
async def test_csv_round_trip(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.csv"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    assert detail["source_format"] == "csv"
    assert detail["events_count"] == 9
    # The default test client loads no modules, so nothing subscribes to
    # `log.imported` - the import must skip the `processing` gate and resolve
    # straight to `ready`. (See test_module_processing.py for the
    # subscriber-installed → `processing` path.)
    assert detail["status"] == "ready"


@pytest.mark.asyncio
async def test_xml_round_trip_autodetect(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.xml"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xml", f, "application/xml")},
            data={"name": "Sample XML"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    assert detail["source_format"] == "xml"
    assert detail["events_count"] == 9
    assert detail["cases_count"] == 3
    schema = detail.get("detected_schema") or {}
    assert schema.get("xml_event_element") == "event"
    assert "case_id" in (schema.get("canonical_columns") or [])


@pytest.mark.asyncio
async def test_xml_probe_endpoint(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.xml"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs/probe-xml",
            files={"file": ("sample.xml", f, "application/xml")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_element"] == "event"
    # All four fields appear in every event in the fixture.
    field_names = {f["name"] for f in body["fields"]}
    assert {"case_id", "activity", "timestamp", "resource"}.issubset(field_names)
    # Autodetect should succeed for canonically-named fields.
    assert body["auto_mapping"] is not None
    assert body["auto_mapping"]["case_id"] == "case_id"
    assert body["auto_mapping"]["activity"] == "activity"


@pytest.mark.asyncio
async def test_xml_with_xes_payload_delegates_to_xes_parser(client: AsyncClient) -> None:
    """A .xml file whose contents are XES should parse via the XES parser
    instead of the generic flat-event model - otherwise the typed-attribute
    children (<string>/<int>/<date>) out-vote the real <event> rows and the
    detected schema comes out garbage.
    """
    fixture = FIXTURES / "sample_xes_as_xml.xml"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xml", f, "application/xml")},
            data={"name": "XES-as-XML"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    # Same numbers as the equivalent .xes fixture - 9 events / 3 cases.
    assert detail["events_count"] == 9
    assert detail["cases_count"] == 3
    schema = detail.get("detected_schema") or {}
    assert schema.get("format_hint") == "xes"


@pytest.mark.asyncio
async def test_xml_probe_recognises_xes(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample_xes_as_xml.xml"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs/probe-xml",
            files={"file": ("sample.xml", f, "application/xml")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format_hint"] == "xes"
    # XES probes skip field enumeration; the frontend uses format_hint to
    # bypass the mapping wizard entirely.
    assert body["fields"] == []
    assert body["auto_mapping"] is None


@pytest.mark.asyncio
async def test_xml_round_trip_with_explicit_mapping(client: AsyncClient) -> None:
    # Same fixture but supply an explicit mapping - exercises the wizard path.
    mapping = {
        "event_element": "event",
        "case_id": "case_id",
        "activity": "activity",
        "timestamp": "timestamp",
        "resource": "resource",
    }
    fixture = FIXTURES / "sample.xml"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xml", f, "application/xml")},
            data={"xml_mapping": json.dumps(mapping)},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    assert detail["events_count"] == 9


@pytest.mark.asyncio
async def test_json_round_trip_autodetect(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.json"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.json", f, "application/json")},
            data={"name": "Sample JSON"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    detail = await _wait_until_ready(client, log_id)
    # A plain .json event array is case-centric, not OCEL.
    assert detail["source_format"] == "json"
    assert detail["log_model"] == "case_centric"
    assert detail["events_count"] == 9
    assert detail["cases_count"] == 3
    schema = detail.get("detected_schema") or {}
    assert schema.get("json_event_path") == "events"
    assert "case_id" in (schema.get("canonical_columns") or [])


@pytest.mark.asyncio
async def test_json_probe_endpoint(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.json"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs/probe-json",
            files={"file": ("sample.json", f, "application/json")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format_hint"] == "generic"
    assert body["event_path"] == "events"
    field_names = {f["name"] for f in body["fields"]}
    assert {"case_id", "activity", "timestamp", "resource"}.issubset(field_names)
    assert body["auto_mapping"] is not None
    assert body["auto_mapping"]["case_id"] == "case_id"


@pytest.mark.asyncio
async def test_unsupported_format_415(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/event-logs",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_list_and_delete(client: AsyncClient) -> None:
    fixture = FIXTURES / "sample.xes"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xes", f, "application/xml")},
        )
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)

    listing = await client.get("/api/v1/event-logs")
    assert listing.status_code == 200
    assert any(row["id"] == log_id for row in listing.json())

    delete = await client.delete(f"/api/v1/event-logs/{log_id}")
    assert delete.status_code == 204

    after = await client.get(f"/api/v1/event-logs/{log_id}")
    assert after.status_code == 404
