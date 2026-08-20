"""Smart column-role resolution + manual re-map."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest
from httpx import AsyncClient

from mate.api.ingest.mapping import (
    apply_roles,
    dedupe_case_insensitive_columns,
    resolve_roles,
)

# ── unit: resolver ───────────────────────────────────────────────────────────


def test_dedupe_keeps_canonical_and_displaces_case_collisions() -> None:
    # A domain `Activity` column alongside the canonical `activity`: the
    # canonical role must keep its exact name (DuckDB folds the two otherwise).
    rename = dedupe_case_insensitive_columns(["Activity", "case_id", "activity", "timestamp"])
    assert rename["activity"] == "activity"
    assert rename["Activity"] == "Activity__src"
    assert rename["case_id"] == "case_id"


def test_dedupe_suffixes_non_canonical_collisions() -> None:
    # Two non-canonical columns that collide only in case still must be unique.
    rename = dedupe_case_insensitive_columns(["Permit id", "permit id"])
    assert rename["Permit id"] == "Permit id"
    assert rename["permit id"] == "permit id__src"


def test_dedupe_is_noop_without_collisions() -> None:
    cols = ["case_id", "activity", "timestamp", "resource", "Cost Type"]
    assert dedupe_case_insensitive_columns(cols) == {c: c for c in cols}


def test_exact_lowercase_match() -> None:
    res = resolve_roles(["case_id", "activity", "timestamp", "resource"])
    assert res.roles["case_id"] == "case_id"
    assert res.roles["activity"] == "activity"
    assert res.quality["activity"] == "exact"
    assert res.needs_review is False


def test_exact_is_case_and_punctuation_insensitive() -> None:
    # The travel-permit scenario: `Activity` should map to the activity role
    # cleanly (no review needed) - this is the bug that broke modules.
    res = resolve_roles(["Case ID", "Activity", "Timestamp"])
    assert res.roles["activity"] == "Activity"
    assert res.quality["activity"] == "exact"
    assert res.roles["case_id"] == "Case ID"
    assert res.needs_review is False


def test_fuzzy_match_flags_review() -> None:
    res = resolve_roles(["case_id", "Action Type", "timestamp"])
    assert res.roles["activity"] == "Action Type"
    assert res.quality["activity"] == "fuzzy"
    assert res.needs_review is True


def test_type_fallback_for_missing_timestamp() -> None:
    df = pd.DataFrame(
        {
            "case_id": ["a", "a", "b"],
            "activity": ["x", "y", "x"],
            "when_it_happened": ["2024-01-01", "2024-01-02", "2024-01-03"],
        }
    )
    res = resolve_roles(list(df.columns), sample=df)
    assert res.roles["timestamp"] == "when_it_happened"
    assert res.quality["timestamp"] == "fallback"
    assert res.needs_review is True


def test_override_wins() -> None:
    res = resolve_roles(
        ["case_id", "activity", "Activity", "timestamp"],
        overrides={"activity": "Activity"},
    )
    assert res.roles["activity"] == "Activity"
    assert res.quality["activity"] == "user"
    assert res.needs_review is False


def test_apply_roles_renames_and_displaces_collision() -> None:
    df = pd.DataFrame({"case_id": ["a"], "activity": ["auto"], "Activity": ["real"]})
    res = resolve_roles(list(df.columns), overrides={"activity": "Activity"})
    out = apply_roles(df, res)
    assert out["activity"].iloc[0] == "real"
    assert "activity__src" in out.columns  # the displaced auto-guess


# ── integration: upload + re-map ─────────────────────────────────────────────


async def _wait_ready(client: AsyncClient, log_id: str, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        last = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
        if last["status"] == "ready":
            return last
        if last["status"] == "failed":
            raise AssertionError(f"import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"import did not finish: {last}")


async def _upload_csv(client: AsyncClient, content: bytes, name: str = "log") -> str:
    resp = await client.post(
        "/api/v1/event-logs",
        files={"file": (f"{name}.csv", content, "text/csv")},
        data={"name": name},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["log_id"]


@pytest.mark.asyncio
async def test_capitalised_headers_auto_map_without_review(client: AsyncClient) -> None:
    csv = (
        b"Case ID,Activity,Timestamp,Resource\n"
        b"c1,register,2024-01-01T00:00:00,alice\n"
        b"c1,ship,2024-01-02T00:00:00,bob\n"
        b"c2,register,2024-01-01T00:00:00,alice\n"
    )
    log_id = await _upload_csv(client, csv, "caps")
    detail = await _wait_ready(client, log_id)
    assert detail["events_count"] == 3
    assert detail["cases_count"] == 2
    # Clean (case-insensitive) match → no review prompt.
    assert detail["mapping_needs_review"] is False
    assert detail["column_roles"]["activity"] in ("activity", "Activity")


@pytest.mark.asyncio
async def test_fuzzy_headers_flag_review_then_remap_fixes_it(client: AsyncClient) -> None:
    csv = (
        b"Case ID,Action Type,Timestamp\n"
        b"c1,register,2024-01-01T00:00:00\n"
        b"c1,ship,2024-01-02T00:00:00\n"
        b"c2,register,2024-01-01T00:00:00\n"
    )
    log_id = await _upload_csv(client, csv, "fuzzy")
    detail = await _wait_ready(client, log_id)
    assert detail["events_count"] == 3
    # 'Action Type' was only a fuzzy activity match → review requested.
    assert detail["mapping_needs_review"] is True

    cols = detail["detected_schema"]["source_columns"]
    assert "Action Type" in cols

    # Manually confirm the roles → re-import resolves cleanly.
    remap = await client.post(
        f"/api/v1/event-logs/{log_id}/remap",
        json={"case_id": "Case ID", "activity": "Action Type", "timestamp": "Timestamp"},
    )
    assert remap.status_code == 202, remap.text
    after = await _wait_ready(client, log_id)
    assert after["events_count"] == 3
    assert after["mapping_needs_review"] is False


@pytest.mark.asyncio
async def test_remap_rejects_unknown_column(client: AsyncClient) -> None:
    csv = b"case_id,activity,timestamp\nc1,a,2024-01-01T00:00:00\n"
    log_id = await _upload_csv(client, csv, "known")
    await _wait_ready(client, log_id)
    resp = await client.post(
        f"/api/v1/event-logs/{log_id}/remap",
        json={"case_id": "case_id", "activity": "nope", "timestamp": "timestamp"},
    )
    assert resp.status_code == 422
