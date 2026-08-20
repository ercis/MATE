"""End-to-end tests for the Events / Variants / Settings tab APIs."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/event-logs/{log_id}")
        last = resp.json()
        if last["status"] == "ready":
            return last
        if last["status"] == "failed":
            raise AssertionError(f"Import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Import did not finish in {timeout}s - last state: {last}")


async def _seed_log(client: AsyncClient) -> str:
    """Upload sample.csv and wait for it to be ready, return its log_id."""
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)
    return log_id


# ── events: list / sort / filter / missing-only ──────────────────────────────


@pytest.mark.asyncio
async def test_events_list_default_sort(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(f"/api/v1/event-logs/{log_id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 9
    assert len(body["rows"]) == 9
    # sorted by (case_id, timestamp) - first row is case-1's first event
    assert body["rows"][0]["case_id"] == "case-1"
    assert body["rows"][0]["activity"] == "register order"
    # column specs include the canonical columns + resource
    names = {c["name"] for c in body["columns"]}
    assert {"case_id", "activity", "timestamp", "resource"} <= names
    # required flag is set on the canonical three
    required = {c["name"] for c in body["columns"] if c["required"]}
    assert required == {"case_id", "activity", "timestamp"}
    # header counters mirror the log row
    assert body["header"]["events_count"] == 9
    assert body["header"]["cases_count"] == 3
    assert body["header"]["variants_count"] == 2
    # synthetic _has_missing column on every row
    assert all("_has_missing" in r for r in body["rows"])
    assert not any(r["_has_missing"] for r in body["rows"])


@pytest.mark.asyncio
async def test_events_pagination_and_sort(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(
        f"/api/v1/event-logs/{log_id}/events",
        params={"limit": 3, "offset": 0, "sort": "timestamp:desc"},
    )
    body = resp.json()
    assert len(body["rows"]) == 3
    assert body["rows"][0]["timestamp"] >= body["rows"][2]["timestamp"]


@pytest.mark.asyncio
async def test_events_filter_contains(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(
        f"/api/v1/event-logs/{log_id}/events",
        params={
            "filter": '[{"field":"case_id","op":"contains","value":"case-1"}]',
        },
    )
    body = resp.json()
    assert body["total"] == 3
    assert all(r["case_id"] == "case-1" for r in body["rows"])


@pytest.mark.asyncio
async def test_events_filter_unknown_field_422(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(
        f"/api/v1/event-logs/{log_id}/events",
        params={"filter": '[{"field":"nope","op":"contains","value":"x"}]'},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_events_case_id_filter(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(
        f"/api/v1/event-logs/{log_id}/events",
        params={"case_id": "case-2"},
    )
    body = resp.json()
    assert body["total"] == 3
    assert all(r["case_id"] == "case-2" for r in body["rows"])


# ── events: editing - single, bulk, validation, re-sort ──────────────────────


@pytest.mark.asyncio
async def test_patch_event_simple_field(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/0",
        json={"field": "resource", "value": "diana"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row"]["resource"] == "diana"
    # editing a non-canonical field doesn't move the row
    assert body["row_index"] == body["new_row_index"] == 0
    # header counts unchanged
    assert body["header"]["events_count"] == 9

    # The change persists on re-fetch.
    again = await client.get(f"/api/v1/event-logs/{log_id}/events", params={"limit": 1})
    assert again.json()["rows"][0]["resource"] == "diana"


@pytest.mark.asyncio
async def test_patch_event_resorts_on_timestamp_change(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    # Move the first event of case-1 to be the latest event in the log.
    resp = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/0",
        json={"field": "timestamp", "value": "2030-01-01T00:00:00"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Within case-1, the now-latest event should be at the end of case-1's block
    # (rows 0..2 were case-1; the moved row should now sit at index 2).
    assert body["new_row_index"] == 2
    # header date_max moved forward
    assert body["header"]["date_max"].startswith("2030-")


@pytest.mark.asyncio
async def test_patch_event_changing_activity_recomputes_variants(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    detail = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
    assert detail["variants_count"] == 2

    # case-2 currently does (register, check stock, cancel). Changing "cancel"
    # to "ship" makes case-2 share the (register, check stock, ship) variant -
    # so total variants should drop from 2 to 1.
    rows = (await client.get(f"/api/v1/event-logs/{log_id}/events")).json()["rows"]
    cancel_row = next(i for i, r in enumerate(rows) if r["activity"] == "cancel")
    resp = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/{cancel_row}",
        json={"field": "activity", "value": "ship"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["header"]["variants_count"] == 1

    after = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
    assert after["variants_count"] == 1
    assert after["last_edited_at"] is not None


@pytest.mark.asyncio
async def test_patch_event_validation_errors(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    # required field cannot be cleared
    bad = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/0",
        json={"field": "case_id", "value": None},
    )
    assert bad.status_code == 422

    # bad datetime
    bad2 = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/0",
        json={"field": "timestamp", "value": "not-a-date"},
    )
    assert bad2.status_code == 422

    # unknown column
    bad3 = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/0",
        json={"field": "nope", "value": "x"},
    )
    assert bad3.status_code == 422


@pytest.mark.asyncio
async def test_patch_event_out_of_range_404(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.patch(
        f"/api/v1/event-logs/{log_id}/events/9999",
        json={"field": "resource", "value": "x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bulk_fill_resource(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.post(
        f"/api/v1/event-logs/{log_id}/events/bulk-fill",
        json={"row_indices": [0, 1, 2], "field": "resource", "value": "team-alpha"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 3

    listing = (await client.get(f"/api/v1/event-logs/{log_id}/events", params={"limit": 5})).json()
    for r in listing["rows"][:3]:
        assert r["resource"] == "team-alpha"


@pytest.mark.asyncio
async def test_edits_history(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    await client.patch(
        f"/api/v1/event-logs/{log_id}/events/0",
        json={"field": "resource", "value": "diana"},
    )
    await client.patch(
        f"/api/v1/event-logs/{log_id}/events/1",
        json={"field": "resource", "value": "eve"},
    )
    resp = await client.get(f"/api/v1/event-logs/{log_id}/edits")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    # Newest first.
    assert body["rows"][0]["new_value_json"] == "eve"
    assert body["rows"][0]["row_index"] == 1


# ── variants ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_variants_listing(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(f"/api/v1/event-logs/{log_id}/variants")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    # Top variant (most cases) should be the (register, check stock, ship) one - 2 cases.
    top = body["rows"][0]
    assert top["rank"] == 1
    assert top["case_count"] == 2
    assert top["activities"] == ["register order", "check stock", "ship"]


@pytest.mark.asyncio
async def test_variant_detail_and_cases(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    listing = (await client.get(f"/api/v1/event-logs/{log_id}/variants")).json()
    top_id = listing["rows"][0]["variant_id"]

    detail = (await client.get(f"/api/v1/event-logs/{log_id}/variants/{top_id}")).json()
    assert detail["case_count"] == 2
    assert detail["activities"] == ["register order", "check stock", "ship"]
    assert detail["duration_histogram"]
    # resource breakdown should be present
    columns = {b["column"] for b in detail["attribute_breakdowns"]}
    assert "resource" in columns

    cases = (await client.get(f"/api/v1/event-logs/{log_id}/variants/{top_id}/cases")).json()
    assert cases["total"] == 2
    case_ids = {r["case_id"] for r in cases["rows"]}
    assert case_ids == {"case-1", "case-3"}


# ── data quality ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_data_quality_no_missing(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(f"/api/v1/event-logs/{log_id}/data-quality")
    body = resp.json()
    assert body["total_events"] == 9
    by_col = {c["column"]: c for c in body["columns"]}
    # all canonical columns are populated in the fixture
    assert by_col["case_id"]["null_count"] == 0
    assert by_col["case_id"]["distinct_count"] == 3
    assert by_col["activity"]["distinct_count"] == 4  # register/check/ship/cancel


# ── time bounds ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_time_bounds(client: AsyncClient) -> None:
    """The dashboard time-range slider is seeded from min/max of the timestamp."""
    log_id = await _seed_log(client)
    resp = await client.get(f"/api/v1/event-logs/{log_id}/time-bounds")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["field"] == "timestamp"
    assert body["min_ts"] is not None
    assert body["max_ts"] is not None
    # A real span - min strictly precedes max in the fixture.
    assert body["min_ts"] < body["max_ts"]


# ── activities ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activities_listing(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.get(f"/api/v1/event-logs/{log_id}/activities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4  # register order, check stock, ship, cancel
    by_act = {r["activity"]: r["count"] for r in body["rows"]}
    assert by_act["register order"] == 3
    assert by_act["check stock"] == 3
    assert by_act["ship"] == 2
    assert by_act["cancel"] == 1
    # Sorted by count desc - first row should be one of the 3-counts.
    assert body["rows"][0]["count"] == 3


@pytest.mark.asyncio
async def test_activity_labels_round_trip(client: AsyncClient) -> None:
    """The Activities tab persists renames inside column_overrides.activity_labels."""
    log_id = await _seed_log(client)
    resp = await client.patch(
        f"/api/v1/event-logs/{log_id}",
        json={
            "column_overrides": {
                "activity_labels": {
                    "register order": "Receive Order",
                    "ship": "Ship Order",
                },
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["column_overrides"]["activity_labels"]["register order"] == "Receive Order"

    # Activities endpoint still returns raw names - renames are display-only.
    activities = (await client.get(f"/api/v1/event-logs/{log_id}/activities")).json()
    raw_names = {r["activity"] for r in activities["rows"]}
    assert "register order" in raw_names
    assert "Receive Order" not in raw_names


# ── log-level update endpoint extensions ────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_log_description_and_overrides(client: AsyncClient) -> None:
    log_id = await _seed_log(client)
    resp = await client.patch(
        f"/api/v1/event-logs/{log_id}",
        json={
            "description": "Synthetic order-fulfilment log.",
            "column_overrides": {
                "labels": {"resource": "Operator"},
                "order": ["case_id", "activity", "timestamp", "resource"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "Synthetic order-fulfilment log."
    assert body["column_overrides"]["labels"]["resource"] == "Operator"

    # The override is reflected in the events column specs.
    events = (await client.get(f"/api/v1/event-logs/{log_id}/events")).json()
    by_name = {c["name"]: c for c in events["columns"]}
    assert by_name["resource"]["label"] == "Operator"
