"""Tests for the MCP "processes" toolset: tenant isolation, ready-gates, scope
enforcement, confirm previews for the destructive tools, folder lifecycle, and
the parquet-backed aggregate reads (which must never expose raw rows).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import EventLog, Folder, User, UserSetting
from mate.api.mcp import limits
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.scopes import ALL_SCOPES, SCOPE_PROCESSES_READ
from mate.api.mcp.toolsets import processes as tools
from mate.api.uuid7 import uuid7_str

from .conftest import TEST_USER_ID

FIXTURES = Path(__file__).parent / "fixtures"
USER_B_ID = "00000000-0000-7000-8000-0000000000b3"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _FakeRequest:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.scope: dict[str, object] = {} if principal is None else {"mate_principal": principal}


class _FakeRequestContext:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request = _FakeRequest(principal)


class _FakeCtx:
    """Stand-in for FastMCP's Context - tools only read request_context.request.scope."""

    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request_context = _FakeRequestContext(principal)


def _principal(user_id: str, scopes: tuple[str, ...] = ALL_SCOPES) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=())
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type="pat")


def _ctx(user_id: str = TEST_USER_ID, scopes: tuple[str, ...] = ALL_SCOPES) -> Any:
    return _FakeCtx(_principal(user_id, scopes))


async def _set_consent(user_id: str, value: bool = True) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
        if row is None:
            session.add(UserSetting(user_id=user_id, key=MCP_EGRESS_CONSENT_KEY, value_json=value))
        else:
            row.value_json = value
        await session.commit()


@pytest.fixture(autouse=True)
def fresh_rate_buckets() -> None:
    """The write-rate bucket is in-process per-user state; reset it per test so
    the destructive-tool tests don't drain each other's burst allowance."""
    limits.reset_for_tests()


async def _db_log(session: Any, log_id: str) -> EventLog:
    row = await session.get(EventLog, log_id)
    assert row is not None
    return row


async def _db_folder(session: Any, folder_id: str) -> Folder:
    row = await session.get(Folder, folder_id)
    assert row is not None
    return row


async def _seed_user(user_id: str) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(User, user_id) is None:
            session.add(
                User(
                    id=user_id,
                    email=f"{user_id}@mate.local",
                    created_at=_now(),
                    last_seen_at=_now(),
                )
            )
            await session.commit()


async def _seed_meta_log(user_id: str, *, status: str = "ready", **kw: Any) -> str:
    """A metadata-only EventLog row (no parquet on disk)."""
    log_id = uuid7_str()
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            EventLog(
                id=log_id,
                user_id=user_id,
                name=f"log-{log_id[:8]}",
                status=status,
                created_at=_now(),
                **kw,
            )
        )
        await session.commit()
    return log_id


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        last = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
        if last["status"] == "ready":
            return
        if last["status"] == "failed":
            raise AssertionError(f"Import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Import did not finish in {timeout}s - last state: {last}")


async def _seed_real_log(client: AsyncClient) -> str:
    """Upload fixtures/sample.csv through the real import pipeline (9 events,
    3 cases, 2 variants) and wait until ready."""
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "MCP sample"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]
    await _wait_until_ready(client, log_id)
    return log_id


# ── tenant isolation + schema wall ───────────────────────────────────────────


async def test_get_process_tenant_isolated(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    await _seed_user(USER_B_ID)
    foreign = await _seed_meta_log(USER_B_ID)

    with pytest.raises(ValueError, match=r"\[not_found\]"):
        await tools.get_process(_ctx(), log_id=foreign)


async def test_get_process_strips_schema_samples(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_meta_log(
        TEST_USER_ID,
        detected_schema={
            "columns": ["case_id", "activity", "timestamp"],
            "column_roles": {"case_id": "case"},
            "fields": [{"name": "case_id", "coverage": 1.0, "samples": ["RAW-CELL"]}],
        },
    )
    out = await tools.get_process(_ctx(), log_id=log_id)
    assert out["id"] == log_id
    schema = out["detected_schema"]
    assert schema["columns"] == ["case_id", "activity", "timestamp"]
    assert schema["column_roles"] == {"case_id": "case"}
    # The raw-cell wall: per-column sample values never leave over MCP.
    assert schema["fields"] == [{"name": "case_id", "coverage": 1.0}]
    assert "RAW-CELL" not in str(out)


# ── ready-gate conflicts ─────────────────────────────────────────────────────


async def test_not_ready_log_conflicts(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_meta_log(TEST_USER_ID, status="processing")

    with pytest.raises(ValueError, match=r"\[conflict\]"):
        await tools.get_activities(_ctx(), log_id=log_id)
    with pytest.raises(ValueError, match=r"\[conflict\]"):
        await tools.get_variants(_ctx(), log_id=log_id)


async def test_ocel_tools_reject_case_centric(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_meta_log(TEST_USER_ID)  # case_centric by default
    with pytest.raises(ValueError, match=r"\[conflict\]"):
        await tools.get_ocel_overview(_ctx(), log_id=log_id)


# ── scope enforcement ────────────────────────────────────────────────────────


async def test_read_scope_cannot_call_write_tools(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_meta_log(TEST_USER_ID)
    read_only = _ctx(scopes=(SCOPE_PROCESSES_READ,))

    with pytest.raises(ValueError, match=r"\[scope_missing\]"):
        await tools.delete_process(read_only, log_id=log_id, confirm=True)
    # Reads keep working on the same principal.
    out = await tools.get_process(read_only, log_id=log_id)
    assert out["id"] == log_id


# ── destructive confirm previews ─────────────────────────────────────────────


async def test_delete_process_preview_then_confirm(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_meta_log(TEST_USER_ID, events_count=9, cases_count=3)

    preview = await tools.delete_process(_ctx(), log_id=log_id)
    assert preview["confirmed"] is False
    assert preview["preview"]["events_count"] == 9
    assert "[confirm_required]" in preview["message"]

    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(EventLog, log_id)
        assert row is not None and row.deleted_at is None  # nothing happened

    done = await tools.delete_process(_ctx(), log_id=log_id, confirm=True)
    assert done == {"deleted": True, "log_id": log_id}
    async with sm() as session:
        row = await session.get(EventLog, log_id)
        assert row is not None and row.deleted_at is not None
    # Soft-deleted → gone from the tool surface too.
    with pytest.raises(ValueError, match=r"\[not_found\]"):
        await tools.get_process(_ctx(), log_id=log_id)


async def test_delete_folder_preview_then_confirm_cascades(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    parent = await tools.create_folder(_ctx(), name="cascade-parent")
    child = await tools.create_folder(_ctx(), name="cascade-child", parent_id=parent["id"])
    log_id = await _seed_meta_log(TEST_USER_ID, folder_id=child["id"])

    preview = await tools.delete_folder(_ctx(), folder_id=parent["id"])
    assert preview["confirmed"] is False
    assert preview["preview"]["folders_deleted"] == 2
    assert preview["preview"]["logs_deleted"] == 1
    assert preview["preview"]["log_names"] and preview["preview"]["log_names_truncated"] is False

    sm = get_sessionmaker()
    async with sm() as session:  # nothing deleted yet
        assert (await _db_folder(session, parent["id"])).deleted_at is None
        assert (await _db_folder(session, child["id"])).deleted_at is None
        assert (await _db_log(session, log_id)).deleted_at is None

    done = await tools.delete_folder(_ctx(), folder_id=parent["id"], confirm=True)
    assert done["deleted"] is True
    assert done["folders_deleted"] == 2 and done["logs_deleted"] == 1
    async with sm() as session:
        assert (await _db_folder(session, parent["id"])).deleted_at is not None
        assert (await _db_folder(session, child["id"])).deleted_at is not None
        assert (await _db_log(session, log_id)).deleted_at is not None


# ── update_process + folders ─────────────────────────────────────────────────


async def test_update_process_rename_and_folder_roundtrip(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_meta_log(TEST_USER_ID)
    folder = await tools.create_folder(_ctx(), name="roundtrip")

    out = await tools.update_process(
        _ctx(), log_id=log_id, name="  Renamed  ", description="notes", folder_id=folder["id"]
    )
    assert out["name"] == "Renamed"
    assert out["description"] == "notes"
    assert out["folder_id"] == folder["id"]

    cleared = await tools.update_process(_ctx(), log_id=log_id, description="", clear_folder=True)
    assert cleared["description"] is None
    assert cleared["folder_id"] is None

    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.update_process(_ctx(), log_id=log_id, name="   ")
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.update_process(_ctx(), log_id=log_id, folder_id="x", clear_folder=True)
    with pytest.raises(ValueError, match=r"\[not_found\]"):
        await tools.update_process(_ctx(), log_id=log_id, folder_id="no-such-folder")


async def test_folder_create_move_list(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    a = await tools.create_folder(_ctx(), name="A")
    b = await tools.create_folder(_ctx(), name="B", parent_id=a["id"])
    assert b["parent_id"] == a["id"]

    listed = await tools.list_folders(_ctx())
    ids = {f["id"] for f in listed["items"]}
    assert {a["id"], b["id"]} <= ids

    # Cycle guard: A cannot move under its own child B.
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.update_folder(_ctx(), folder_id=a["id"], parent_id=b["id"])

    moved = await tools.update_folder(_ctx(), folder_id=b["id"], clear_parent=True, position=5)
    assert moved["parent_id"] is None and moved["position"] == 5

    renamed = await tools.update_folder(_ctx(), folder_id=b["id"], name="B2")
    assert renamed["name"] == "B2"

    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.create_folder(_ctx(), name="   ")
    # Foreign/missing folders 404-shape.
    with pytest.raises(ValueError, match=r"\[not_found\]"):
        await tools.update_folder(_ctx(), folder_id="missing", name="x")


# ── import_process_from_url ──────────────────────────────────────────────────


async def test_import_from_url_rejects_non_http_schemes(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    for url in ("file:///etc/passwd", "ftp://host/log.xes", "not-a-url"):
        with pytest.raises(ValueError, match=r"\[invalid\]"):
            await tools.import_process_from_url(_ctx(), url=url)


# ── set_committed_filter ─────────────────────────────────────────────────────


async def test_set_committed_filter_preview_invalid_and_commit(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_real_log(client)

    # Unsupported op / unknown field → [invalid], never a preview.
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.set_committed_filter(
            _ctx(), log_id=log_id, filter=[{"field": "case_id", "op": "regex", "value": "x"}]
        )
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.set_committed_filter(
            _ctx(), log_id=log_id, filter=[{"field": "nope", "op": "equals", "value": "x"}]
        )
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.set_committed_filter(_ctx(), log_id=log_id, filter=["not-a-dict"])  # type: ignore[list-item]

    entries = [{"field": "case_id", "op": "equals", "value": "case-1"}]
    preview = await tools.set_committed_filter(_ctx(), log_id=log_id, filter=entries)
    assert preview["confirmed"] is False
    assert preview["preview"]["current_filter"] == []
    assert preview["preview"]["new_filter"] == entries

    sm = get_sessionmaker()
    async with sm() as session:  # preview committed nothing
        assert (await _db_log(session, log_id)).active_filter is None

    done = await tools.set_committed_filter(_ctx(), log_id=log_id, filter=entries, confirm=True)
    assert done["active_filter"] == entries
    assert isinstance(done["modules_retriggered"], bool)
    async with sm() as session:
        assert (await _db_log(session, log_id)).active_filter == entries

    # The committed filter now shapes the aggregates (only case-1 remains).
    acts = await tools.get_activities(_ctx(), log_id=log_id)
    assert sum(i["count"] for i in acts["items"]) == 3

    cleared = await tools.set_committed_filter(_ctx(), log_id=log_id, filter=[], confirm=True)
    assert cleared["active_filter"] == []
    async with sm() as session:
        assert (await _db_log(session, log_id)).active_filter is None


# ── parquet-backed aggregates (happy path) ───────────────────────────────────


async def test_aggregate_reads_shapes_and_no_raw_rows(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_real_log(client)

    acts = await tools.get_activities(_ctx(), log_id=log_id)
    assert acts["total"] == 4
    by_name = {i["activity"]: i["count"] for i in acts["items"]}
    assert by_name["register order"] == 3 and by_name["ship"] == 2
    assert acts["items"][0]["count"] == 3  # frequency-ordered

    act = await tools.get_activity(_ctx(), log_id=log_id, name="ship")
    assert act["event_count"] == 2 and act["case_count"] == 2
    assert act["end_case_count"] == 2 and act["variant_count"] == 1
    assert act["top_variants"][0]["activities"] == ["register order", "check stock", "ship"]

    variants = await tools.get_variants(_ctx(), log_id=log_id)
    assert variants["total"] == 2 and variants["next_cursor"] is None
    top = variants["items"][0]
    assert top["rank"] == 1 and top["case_count"] == 2
    assert top["activities"] == ["register order", "check stock", "ship"]
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.get_variants(_ctx(), log_id=log_id, sort="bogus")

    detail = await tools.get_variant(_ctx(), log_id=log_id, variant_id=top["variant_id"])
    assert detail["case_count"] == 2 and detail["duration_histogram"]
    assert {b["column"] for b in detail["attribute_breakdowns"]} >= {"resource"}
    with pytest.raises(ValueError, match=r"\[not_found\]"):
        await tools.get_variant(_ctx(), log_id=log_id, variant_id="no-such-variant")

    cases = await tools.get_variant_cases(_ctx(), log_id=log_id, variant_id=top["variant_id"])
    assert cases["total"] == 2
    assert {c["case_id"] for c in cases["items"]} == {"case-1", "case-3"}
    assert set(cases["items"][0]) == {
        "case_id",
        "case_start",
        "case_end",
        "case_duration_seconds",
        "event_count",
    }  # case-level metadata only - no event payloads

    quality = await tools.get_data_quality(_ctx(), log_id=log_id)
    assert quality["total_events"] == 9
    q_cols = {c["column"] for c in quality["columns"]}
    assert {"case_id", "activity", "timestamp"} <= q_cols

    bounds = await tools.get_time_bounds(_ctx(), log_id=log_id)
    assert bounds["field"] == "timestamp"
    assert bounds["min_ts"] is not None and bounds["min_ts"] < bounds["max_ts"]

    schema = await tools.get_column_schema(_ctx(), log_id=log_id)
    fields = {c["field"] for c in schema["columns"]}
    assert {"case_id", "activity", "timestamp", "resource"} <= fields
    for col in schema["columns"]:
        # Schema only: exactly these keys, never enum_values / cell samples.
        assert set(col) == {"field", "role", "label", "required", "type"}
    required = {c["field"] for c in schema["columns"] if c["required"]}
    assert required == {"case_id", "activity", "timestamp"}


# ── remap / reimport previews on a real log ──────────────────────────────────


async def test_remap_and_reimport_previews(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID)
    log_id = await _seed_real_log(client)

    preview = await tools.remap_columns(
        _ctx(), log_id=log_id, case_id="case_id", activity="activity", timestamp="timestamp"
    )
    assert preview["confirmed"] is False
    assert preview["preview"]["requested_column_roles"] == {
        "case_id": "case_id",
        "activity": "activity",
        "timestamp": "timestamp",
    }
    assert isinstance(preview["preview"]["current_column_roles"], dict)

    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.remap_columns(
            _ctx(), log_id=log_id, case_id="no_such_col", activity="activity", timestamp="timestamp"
        )
    with pytest.raises(ValueError, match=r"\[invalid\]"):
        await tools.remap_columns(
            _ctx(), log_id=log_id, case_id="", activity="activity", timestamp="timestamp"
        )

    re_preview = await tools.reimport_process(_ctx(), log_id=log_id)
    assert re_preview["confirmed"] is False
    assert re_preview["preview"]["source_filename"] == "sample.csv"
    assert re_preview["preview"]["source_format"] == "csv"

    sm = get_sessionmaker()
    async with sm() as session:  # previews changed nothing
        assert (await _db_log(session, log_id)).status == "ready"
