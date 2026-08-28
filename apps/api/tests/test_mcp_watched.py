"""Tests for the MCP watched-folders toolset: CRUD roundtrip against a local
source dir, scan counts, delete preview vs confirm, validation, tenant
isolation.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Folder, User, UserSetting, WatchedFolder
from mate.api.mcp import limits
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.scopes import ALL_SCOPES, SCOPE_WATCHED_READ
from mate.api.mcp.toolsets import watched as watched_tools

from .conftest import TEST_USER_ID

USER_B_ID = "00000000-0000-7000-8000-0000000000b4"
FIXTURES = Path(__file__).parent / "fixtures"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _FakeRequest:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.scope: dict[str, object] = {} if principal is None else {"mate_principal": principal}


class _FakeRequestContext:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request = _FakeRequest(principal)


class _FakeCtx:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request_context = _FakeRequestContext(principal)


def _principal(user_id: str, scopes: tuple[str, ...] = ALL_SCOPES) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=())
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type="pat")


def _ctx(user_id: str = TEST_USER_ID, scopes: tuple[str, ...] = ALL_SCOPES) -> Any:
    return _FakeCtx(_principal(user_id, scopes))


@pytest.fixture(autouse=True)
def _fresh_rate_buckets() -> None:  # pyright: ignore[reportUnusedFunction]
    limits.reset_for_tests()


async def _set_consent(user_id: str, value: bool) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
        if row is None:
            session.add(UserSetting(user_id=user_id, key=MCP_EGRESS_CONSENT_KEY, value_json=value))
        else:
            row.value_json = value
        await session.commit()


async def _ensure_user_b() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(User, USER_B_ID) is None:
            session.add(
                User(id=USER_B_ID, email="b4@mate.local", created_at=_now(), last_seen_at=_now())
            )
            await session.commit()


# ── CRUD roundtrip + scan ────────────────────────────────────────────────────


async def test_watched_folder_crud_and_scan_roundtrip(client: AsyncClient, tmp_path: Path) -> None:
    await _set_consent(TEST_USER_ID, True)
    src = tmp_path / "watch-src"
    src.mkdir()

    created = await watched_tools.create_watched_folder(
        _ctx(), name="MCP watch", source_path=str(src)
    )
    wid = created["id"]
    assert created["mode"] == "manual" and created["status"] == "active"
    assert created["dest_folder_id"] is None  # create_dest_folder defaults False here

    listing = await watched_tools.list_watched_folders(_ctx())
    assert any(w["id"] == wid for w in listing)

    detail = await watched_tools.get_watched_folder(_ctx(), wid)
    assert detail["id"] == wid and detail["files"] == []

    updated = await watched_tools.update_watched_folder(
        _ctx(), wid, name="Renamed watch", status="paused"
    )
    assert updated["name"] == "Renamed watch" and updated["status"] == "paused"

    # Scan of an empty source: found 0, nothing imported.
    empty = await watched_tools.scan_watched_folder(_ctx(), wid)
    assert empty == {"found": 0, "imported": 0, "skipped": 0, "failed": 0}

    # Drop a CSV in and scan again - one import job is enqueued for it.
    shutil.copy(FIXTURES / "sample.csv", src / "log1.csv")
    first = await watched_tools.scan_watched_folder(_ctx(), wid)
    assert first["found"] == 1 and first["imported"] == 1

    detail2 = await watched_tools.get_watched_folder(_ctx(), wid)
    assert len(detail2["files"]) == 1
    assert detail2["files"][0]["log_id"]

    # Re-scan is a dedup no-op.
    second = await watched_tools.scan_watched_folder(_ctx(), wid)
    assert second["imported"] == 0 and second["skipped"] == 1


async def test_create_watched_folder_with_new_dest_folder(
    client: AsyncClient, tmp_path: Path
) -> None:
    await _set_consent(TEST_USER_ID, True)
    src = tmp_path / "watch-dest"
    src.mkdir()
    created = await watched_tools.create_watched_folder(
        _ctx(), name="With dest", source_path=str(src), create_dest_folder=True
    )
    dest_id = created["dest_folder_id"]
    assert dest_id
    sm = get_sessionmaker()
    async with sm() as session:
        folder = await session.get(Folder, dest_id)
        assert folder is not None
        assert folder.user_id == TEST_USER_ID and folder.name == "With dest"


async def test_watched_folder_validation(client: AsyncClient, tmp_path: Path) -> None:
    await _set_consent(TEST_USER_ID, True)
    # interval mode without a cadence is rejected by the shared schema.
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await watched_tools.create_watched_folder(
            _ctx(), name="No cadence", source_path=str(tmp_path / "x"), mode="interval"
        )
    # Unknown mode value.
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await watched_tools.create_watched_folder(
            _ctx(), name="Bad mode", source_path=str(tmp_path / "y"), mode="sometimes"
        )

    src = tmp_path / "upd"
    src.mkdir()
    created = await watched_tools.create_watched_folder(_ctx(), name="Upd", source_path=str(src))
    # manual → interval without a cadence is rejected like the route (422).
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await watched_tools.update_watched_folder(_ctx(), created["id"], mode="interval")


async def test_watched_folder_delete_preview_then_confirm(
    client: AsyncClient, tmp_path: Path
) -> None:
    await _set_consent(TEST_USER_ID, True)
    src = tmp_path / "watch-del"
    src.mkdir()
    created = await watched_tools.create_watched_folder(
        _ctx(), name="Delete me", source_path=str(src)
    )
    wid = created["id"]

    preview = await watched_tools.delete_watched_folder(_ctx(), wid)
    assert preview["confirmed"] is False
    assert preview["preview"]["name"] == "Delete me"
    assert preview["preview"]["files_seen"] == 0
    assert "[confirm_required]" in preview["message"]
    # Dry run: still listed.
    assert any(w["id"] == wid for w in await watched_tools.list_watched_folders(_ctx()))

    result = await watched_tools.delete_watched_folder(_ctx(), wid, confirm=True)
    assert result == {"deleted": True, "watched_folder_id": wid}
    assert all(w["id"] != wid for w in await watched_tools.list_watched_folders(_ctx()))
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await watched_tools.get_watched_folder(_ctx(), wid)

    # Soft delete like the route: the row survives with deleted_at set.
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(WatchedFolder, wid)
        assert row is not None and row.deleted_at is not None


# ── tenant isolation + scopes ────────────────────────────────────────────────


async def test_watched_folder_tenant_isolation(client: AsyncClient, tmp_path: Path) -> None:
    await _ensure_user_b()
    await _set_consent(TEST_USER_ID, True)
    await _set_consent(USER_B_ID, True)
    src = tmp_path / "watch-iso"
    src.mkdir()
    created = await watched_tools.create_watched_folder(
        _ctx(), name="Mine only", source_path=str(src)
    )
    wid = created["id"]

    assert all(w["id"] != wid for w in await watched_tools.list_watched_folders(_ctx(USER_B_ID)))
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await watched_tools.get_watched_folder(_ctx(USER_B_ID), wid)
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await watched_tools.update_watched_folder(_ctx(USER_B_ID), wid, name="hijack")
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await watched_tools.delete_watched_folder(_ctx(USER_B_ID), wid, confirm=True)


async def test_watched_write_requires_write_scope(client: AsyncClient, tmp_path: Path) -> None:
    await _set_consent(TEST_USER_ID, True)
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await watched_tools.create_watched_folder(
            _ctx(scopes=(SCOPE_WATCHED_READ,)), name="Nope", source_path=str(tmp_path)
        )
