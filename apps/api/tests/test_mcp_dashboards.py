"""Tests for the MCP dashboards toolset: CRUD roundtrip, validation, tenant
isolation, scope enforcement, team-scoped sharing (grant/dedup/revoke),
destructive delete preview vs confirm (share cascade), export/import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import (
    DashboardShare,
    EventLog,
    Team,
    TeamMember,
    User,
    UserSetting,
)
from mate.api.mcp import limits
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.scopes import ALL_SCOPES, SCOPE_DASHBOARDS_READ, SCOPE_DASHBOARDS_WRITE
from mate.api.mcp.toolsets import dashboards as tools
from mate.api.uuid7 import uuid7_str

from .conftest import TEST_USER_ID

USER_B_ID = "00000000-0000-7000-8000-0000000000d1"
USER_C_ID = "00000000-0000-7000-8000-0000000000d2"  # seeded, but never a teammate
MISSING_ID = "00000000-0000-7000-8000-00000000ffff"

ITEMS = [
    {
        "i": "card-1",
        "module_id": "performance",
        "widget_id": "kpis",
        "title": "KPIs",
        "x": 0,
        "y": 0,
        "w": 6,
        "h": 8,
        "config": {"metric": "avg_duration"},
    }
]
# Deliberately carries the pre-v2 `granularity` key: an MCP client (or a saved
# script) can still post a legacy board, so create must coerce it. `fine` was a
# 40-column grid, so the `w: 6` card above rescales to `w: 2` on the 12-col grid.
SETTINGS = {
    "granularity": "fine",
    "chrome": {"border": False},
    "presets": [
        {
            "id": "p1",
            "name": "Late cases",
            "filters": [{"column": "status", "op": "eq", "value": "late"}],
        }
    ],
    "active_preset_id": "p1",
}


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


async def _seed_env() -> None:
    """Users B/C + egress consent for A and B (idempotent across tests)."""
    sm = get_sessionmaker()
    async with sm() as session:
        for uid in (USER_B_ID, USER_C_ID):
            if await session.get(User, uid) is None:
                session.add(
                    User(
                        id=uid,
                        email=f"{uid[-2:]}@mate.local",
                        created_at=_now(),
                        last_seen_at=_now(),
                    )
                )
        for uid in (TEST_USER_ID, USER_B_ID):
            row = await session.get(UserSetting, (uid, MCP_EGRESS_CONSENT_KEY))
            if row is None:
                session.add(UserSetting(user_id=uid, key=MCP_EGRESS_CONSENT_KEY, value_json=True))
            else:
                row.value_json = True
        await session.commit()


async def _seed_team(*member_ids: str, name: str = "Analysts") -> str:
    sm = get_sessionmaker()
    async with sm() as session:
        team_id = uuid7_str()
        session.add(Team(id=team_id, name=name, created_at=_now()))
        await session.flush()
        for uid in member_ids:
            session.add(TeamMember(team_id=team_id, user_id=uid, created_at=_now()))
        await session.commit()
    return team_id


async def _seed_log(user_id: str, log_model: str = "case_centric") -> str:
    sm = get_sessionmaker()
    async with sm() as session:
        log_id = uuid7_str()
        session.add(
            EventLog(
                id=log_id,
                user_id=user_id,
                name=f"log-{log_id[-6:]}",
                status="ready",
                log_model=log_model,
                created_at=_now(),
            )
        )
        await session.commit()
    return log_id


async def _share_rows(dashboard_id: str) -> list[DashboardShare]:
    sm = get_sessionmaker()
    async with sm() as session:
        rows = await session.execute(
            select(DashboardShare).where(DashboardShare.dashboard_id == dashboard_id)
        )
        return list(rows.scalars().all())


# ── CRUD roundtrip ───────────────────────────────────────────────────────────


async def test_create_get_update_list_roundtrip(client: AsyncClient) -> None:
    await _seed_env()

    created = await tools.create_dashboard(
        _ctx(), name="  Ops board  ", description="daily ops", items=ITEMS, settings=SETTINGS
    )
    dash_id = created["id"]
    assert created["name"] == "Ops board"  # route validator strips
    assert created["is_owner"] is True
    assert len(created["items"]) == 1 and created["items"][0]["module_id"] == "performance"

    detail = await tools.get_dashboard(_ctx(), dash_id)
    assert detail["items"][0]["config"] == {"metric": "avg_duration"}
    # The legacy `granularity` was consumed to decode the geometry and is never
    # echoed back, so the board converges to v2 on its own.
    assert "granularity" not in detail["settings"]
    assert detail["settings"]["grid_version"] == 2
    # 40-col `w: 6` -> 12-col `w: 2`; x stays 0.
    assert (detail["items"][0]["x"], detail["items"][0]["w"]) == (0, 2)
    assert detail["settings"]["active_preset_id"] == "p1"
    assert detail["settings"]["presets"][0]["filters"][0]["value"] == "late"

    # items update replaces the card list but preserves the settings sibling.
    two_cards = [*ITEMS, {**ITEMS[0], "i": "card-2", "widget_id": "throughput", "x": 6}]
    updated = await tools.update_dashboard(_ctx(), dash_id, name="Renamed", items=two_cards)
    assert updated["name"] == "Renamed"
    assert updated["description"] == "daily ops"  # omitted → unchanged
    assert len(updated["items"]) == 2
    # Settings survive an items-only update, still without the legacy marker.
    assert updated["settings"]["grid_version"] == 2
    assert updated["settings"]["active_preset_id"] == "p1"

    only_desc = await tools.update_dashboard(_ctx(), dash_id, description="new text")
    assert only_desc["name"] == "Renamed" and only_desc["description"] == "new text"

    page = await tools.list_dashboards(_ctx())
    mine = next(i for i in page["items"] if i["id"] == dash_id)
    assert mine["card_count"] == 2 and "next_cursor" in page and "total" in page

    # Card catalog: empty modules env → a well-formed, empty catalog.
    catalog = await tools.get_dashboard_card_catalog(_ctx())
    assert catalog == {"cards": [], "count": 0}


# ── validation + log binding ─────────────────────────────────────────────────


async def test_create_validation_and_log_binding(client: AsyncClient) -> None:
    await _seed_env()
    log_a = await _seed_log(TEST_USER_ID)
    log_a_ocel = await _seed_log(TEST_USER_ID, log_model="object_centric")
    log_b = await _seed_log(USER_B_ID)

    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await tools.create_dashboard(_ctx(), name="   ")
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        # A widget card without widget_id fails the item schema.
        await tools.create_dashboard(_ctx(), name="X", items=[{"i": "c", "module_id": "m"}])
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await tools.create_dashboard(_ctx(), name="X", log_model="not_a_model")

    # Binding someone else's log: 404-on-foreign, indistinguishable from missing.
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await tools.create_dashboard(_ctx(), name="X", event_log_id=log_b)
    # Model mismatch between board and bound log.
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await tools.create_dashboard(_ctx(), name="X", event_log_id=log_a_ocel)

    board = await tools.create_dashboard(_ctx(), name="Bound", event_log_id=log_a)
    assert board["event_log_id"] == log_a

    # Rebinding on update revalidates ownership.
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await tools.update_dashboard(_ctx(), board["id"], event_log_id=log_b)


# ── tenant isolation + scopes ────────────────────────────────────────────────


async def test_tools_are_tenant_isolated(client: AsyncClient) -> None:
    await _seed_env()
    board = await tools.create_dashboard(_ctx(), name="Private board")
    dash_id = board["id"]

    for coro in (
        tools.get_dashboard(_ctx(USER_B_ID), dash_id),
        tools.update_dashboard(_ctx(USER_B_ID), dash_id, name="hijack"),
        tools.delete_dashboard(_ctx(USER_B_ID), dash_id, confirm=True),
        tools.export_dashboard(_ctx(USER_B_ID), dash_id),
        tools.list_dashboard_shares(_ctx(USER_B_ID), dash_id),
    ):
        with pytest.raises(MCPToolError, match=r"\[not_found\]"):
            await coro

    page_b = await tools.list_dashboards(_ctx(USER_B_ID))
    assert dash_id not in {i["id"] for i in page_b["items"]}
    # Nothing above deleted or renamed the board.
    still = await tools.get_dashboard(_ctx(), dash_id)
    assert still["name"] == "Private board"


async def test_scope_enforcement(client: AsyncClient) -> None:
    await _seed_env()
    read_only = _ctx(TEST_USER_ID, scopes=(SCOPE_DASHBOARDS_READ,))
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await tools.create_dashboard(read_only, name="Nope")

    write_only = _ctx(TEST_USER_ID, scopes=(SCOPE_DASHBOARDS_WRITE,))
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await tools.list_dashboards(write_only)


# ── sharing ──────────────────────────────────────────────────────────────────


async def test_share_target_validation(client: AsyncClient) -> None:
    await _seed_env()
    foreign_team = await _seed_team(USER_B_ID, name="Not mine")
    board = await tools.create_dashboard(_ctx(), name="Share validation")
    dash_id = board["id"]

    with pytest.raises(MCPToolError, match=r"\[invalid\]"):  # both targets
        await tools.share_dashboard(
            _ctx(), dash_id, target_user_id=USER_B_ID, target_team_id=foreign_team
        )
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):  # no target
        await tools.share_dashboard(_ctx(), dash_id)
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):  # self-share → route's 400
        await tools.share_dashboard(_ctx(), dash_id, target_user_id=TEST_USER_ID)
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):  # unknown user
        await tools.share_dashboard(_ctx(), dash_id, target_user_id=MISSING_ID)
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):  # unknown team
        await tools.share_dashboard(_ctx(), dash_id, target_team_id=MISSING_ID)
    with pytest.raises(MCPToolError, match=r"\[forbidden\]"):  # user outside my teams
        await tools.share_dashboard(_ctx(), dash_id, target_user_id=USER_C_ID)
    with pytest.raises(MCPToolError, match=r"\[forbidden\]"):  # team I don't belong to
        await tools.share_dashboard(_ctx(), dash_id, target_team_id=foreign_team)

    assert await _share_rows(dash_id) == []


async def test_share_flow_grant_read_only_and_revoke(client: AsyncClient) -> None:
    await _seed_env()
    team_id = await _seed_team(TEST_USER_ID, USER_B_ID)
    board = await tools.create_dashboard(_ctx(), name="Team review", items=ITEMS)
    dash_id = board["id"]

    # A's share picker offers the team and its co-member.
    targets = await tools.get_share_targets(_ctx())
    assert any(t["kind"] == "team" and t["id"] == team_id for t in targets)
    assert any(t["kind"] == "user" and t["id"] == USER_B_ID for t in targets)

    share = await tools.share_dashboard(_ctx(), dash_id, target_user_id=USER_B_ID)
    assert share["kind"] == "user" and share["target_id"] == USER_B_ID

    with pytest.raises(MCPToolError, match=r"\[conflict\]"):  # duplicate target
        await tools.share_dashboard(_ctx(), dash_id, target_user_id=USER_B_ID)

    # Recipient: read works (marked read-only), mutation 404s like the route.
    seen_by_b = await tools.get_dashboard(_ctx(USER_B_ID), dash_id)
    assert seen_by_b["is_owner"] is False and len(seen_by_b["items"]) == 1
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await tools.update_dashboard(_ctx(USER_B_ID), dash_id, name="hijack")
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):  # export stays owner-only
        await tools.export_dashboard(_ctx(USER_B_ID), dash_id)

    inbox = await tools.list_shared_with_me(_ctx(USER_B_ID))
    mine = next(d for d in inbox if d["id"] == dash_id)
    assert mine["owner_label"] and mine["card_count"] == 1

    listed = await tools.list_dashboard_shares(_ctx(), dash_id)
    assert [s["id"] for s in listed] == [share["id"]]

    revoked = await tools.revoke_dashboard_share(_ctx(), dash_id, share["id"])
    assert revoked["revoked"] is True
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await tools.get_dashboard(_ctx(USER_B_ID), dash_id)
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):  # unknown share id
        await tools.revoke_dashboard_share(_ctx(), dash_id, share["id"])

    # Team share re-grants access to B through membership.
    team_share = await tools.share_dashboard(_ctx(), dash_id, target_team_id=team_id)
    assert team_share["kind"] == "team" and team_share["target_id"] == team_id
    assert (await tools.get_dashboard(_ctx(USER_B_ID), dash_id))["is_owner"] is False


# ── destructive delete ───────────────────────────────────────────────────────


async def test_delete_preview_then_confirm_cascades_shares(client: AsyncClient) -> None:
    await _seed_env()
    await _seed_team(TEST_USER_ID, USER_B_ID, name="Cascade")
    board = await tools.create_dashboard(_ctx(), name="Doomed", items=ITEMS)
    dash_id = board["id"]
    await tools.share_dashboard(_ctx(), dash_id, target_user_id=USER_B_ID)

    preview = await tools.delete_dashboard(_ctx(), dash_id)  # no confirm → dry run
    assert preview["confirmed"] is False and "[confirm_required]" in preview["message"]
    assert preview["preview"]["name"] == "Doomed"
    assert preview["preview"]["card_count"] == 1
    assert preview["preview"]["share_count"] == 1
    # Nothing was deleted.
    assert (await tools.get_dashboard(_ctx(), dash_id))["id"] == dash_id
    assert len(await _share_rows(dash_id)) == 1

    done = await tools.delete_dashboard(_ctx(), dash_id, confirm=True)
    assert done == {"deleted": True, "dashboard_id": dash_id, "shares_revoked": 1}
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await tools.get_dashboard(_ctx(), dash_id)
    assert await _share_rows(dash_id) == []  # FK cascade wiped the share
    assert dash_id not in {d["id"] for d in await tools.list_shared_with_me(_ctx(USER_B_ID))}


# ── export / import ──────────────────────────────────────────────────────────


async def test_export_import_roundtrip(client: AsyncClient) -> None:
    await _seed_env()
    log_a = await _seed_log(TEST_USER_ID)
    log_b = await _seed_log(USER_B_ID)
    board = await tools.create_dashboard(
        _ctx(),
        name="Portable",
        description="take me",
        event_log_id=log_a,
        items=ITEMS,
        settings=SETTINGS,
    )

    snapshot = await tools.export_dashboard(_ctx(), board["id"])
    assert snapshot["kind"] == "mate.dashboard" and snapshot["version"] == 1
    assert "event_log_id" not in snapshot and "id" not in snapshot  # portable + id-free

    imported = await tools.import_dashboard(_ctx(), snapshot)
    assert imported["id"] != board["id"]
    assert imported["name"] == "Portable" and imported["description"] == "take me"
    assert imported["event_log_id"] is None  # the log binding does not travel
    assert imported["items"] == board["items"]
    assert imported["settings"] == board["settings"]

    # Import may rebind a log - only one of YOUR logs.
    rebound = await tools.import_dashboard(_ctx(), {**snapshot, "event_log_id": log_a})
    assert rebound["event_log_id"] == log_a
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await tools.import_dashboard(_ctx(), {**snapshot, "event_log_id": log_b})
    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await tools.import_dashboard(_ctx(), {**snapshot, "items": [{"i": "broken"}]})
