"""Tests for the MCP admin toolset: OAuth-only gating (PAT can never pass),
cross-user user/log/job reads, worker-pool + MCP-config control, teams
lifecycle, destructive previews that must not mutate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import EventLog, Job, SystemSetting, Team, User, UserSetting
from mate.api.jobs.runtime import MAX_WORKERS, MIN_WORKERS, get_job_runtime
from mate.api.mcp import limits
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.governance import MCP_READ_ONLY_KEY, reset_enabled_cache
from mate.api.mcp.scopes import READ_SCOPES, SCOPE_ADMIN
from mate.api.mcp.toolsets import admin as admin_tools
from mate.api.uuid7 import uuid7_str

from .conftest import TEST_USER_EMAIL, TEST_USER_ID

USER_B_ID = "00000000-0000-7000-8000-0000000000b4"
USER_B_EMAIL = "b4@mate.local"


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


def _principal(
    user_id: str,
    scopes: tuple[str, ...] = (SCOPE_ADMIN,),
    *,
    auth_type: str = "pat",
    roles: tuple[str, ...] = (),
) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=roles)
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type=auth_type)


def _ctx(principal: MCPPrincipal) -> Any:
    return _FakeCtx(principal)


def _admin_ctx(user_id: str = TEST_USER_ID) -> Any:
    """A principal that passes ``authz_admin``: OAuth + admin scope + admin role."""
    return _FakeCtx(_principal(user_id, scopes=(SCOPE_ADMIN,), auth_type="oauth", roles=("admin",)))


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
                User(id=USER_B_ID, email=USER_B_EMAIL, created_at=_now(), last_seen_at=_now())
            )
            await session.commit()


async def _seed_job(
    user_id: str,
    *,
    status: str = "completed",
    type_: str = "test.admin_op",
    title: str = "Admin test job",
) -> str:
    job_id = uuid7_str()
    now = _now()
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            Job(
                id=job_id,
                user_id=user_id,
                type=type_,
                title=title,
                payload_json={},
                status=status,
                created_at=now,
                started_at=None if status == "queued" else now,
                finished_at=now if status in ("completed", "failed", "cancelled") else None,
            )
        )
        await session.commit()
    return job_id


# ── gating: OAuth-only, scope + role required ────────────────────────────────


async def test_admin_tools_refuse_non_admin_principals(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)

    # A PAT can never reach admin tools, even with a forged scope AND role.
    pat = _principal(TEST_USER_ID, scopes=(SCOPE_ADMIN,), auth_type="pat", roles=("admin",))
    with pytest.raises(MCPToolError, match=r"\[forbidden\]"):
        await admin_tools.admin_list_users(_ctx(pat))

    # OAuth with the scope but without the realm role.
    oauth_no_role = _principal(TEST_USER_ID, scopes=(SCOPE_ADMIN,), auth_type="oauth")
    with pytest.raises(MCPToolError, match=r"\[forbidden\]"):
        await admin_tools.admin_list_users(_ctx(oauth_no_role))

    # OAuth with the role but without the admin scope.
    oauth_no_scope = _principal(
        TEST_USER_ID, scopes=READ_SCOPES, auth_type="oauth", roles=("admin",)
    )
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await admin_tools.admin_list_users(_ctx(oauth_no_scope))


async def test_admin_list_users_happy_path(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    users = await admin_tools.admin_list_users(_admin_ctx())
    row = next((u for u in users if u["id"] == TEST_USER_ID), None)
    assert row is not None
    assert row["email"] == TEST_USER_EMAIL
    assert row["created_at"] and row["last_seen_at"]


# ── worker pool ──────────────────────────────────────────────────────────────


async def test_admin_worker_pool_get_set_roundtrip(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    before = await admin_tools.admin_get_worker_pool(_admin_ctx())
    original = int(before["worker_concurrency"])
    assert before["min"] == MIN_WORKERS and before["max"] == MAX_WORKERS

    target = 2 if original != 2 else 3
    try:
        updated = await admin_tools.admin_set_worker_pool(_admin_ctx(), target)
        assert updated["worker_concurrency"] == target
        after = await admin_tools.admin_get_worker_pool(_admin_ctx())
        assert after["worker_concurrency"] == target

        with pytest.raises(MCPToolError, match=r"\[invalid\]"):
            await admin_tools.admin_set_worker_pool(_admin_ctx(), MAX_WORKERS + 1)
        with pytest.raises(MCPToolError, match=r"\[invalid\]"):
            await admin_tools.admin_set_worker_pool(_admin_ctx(), MIN_WORKERS - 1)
    finally:
        await admin_tools.admin_set_worker_pool(_admin_ctx(), original)


# ── MCP governance config ────────────────────────────────────────────────────


async def test_admin_mcp_config_roundtrip(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    sm = get_sessionmaker()
    try:
        # Invalid mint policy refuses before touching anything (and before the
        # read-only flip below would lock writes out).
        with pytest.raises(MCPToolError, match=r"\[invalid\]"):
            await admin_tools.admin_set_mcp_config(_admin_ctx(), mint_policy="bogus")

        cfg = await admin_tools.admin_set_mcp_config(_admin_ctx(), read_only=True)
        assert cfg["read_only"] is True
        got = await admin_tools.admin_get_mcp_config(_admin_ctx())
        assert got["read_only"] is True

        # The live flag now blocks every mutating tool, including this one.
        with pytest.raises(MCPToolError, match=r"\[read_only\]"):
            await admin_tools.admin_set_mcp_config(_admin_ctx(), read_only=False)
    finally:
        async with sm() as session:
            row = await session.get(SystemSetting, MCP_READ_ONLY_KEY)
            if row is not None:
                await session.delete(row)
                await session.commit()
        reset_enabled_cache()


# ── teams lifecycle ──────────────────────────────────────────────────────────


async def test_admin_teams_create_member_list_delete(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    created = await admin_tools.admin_create_team(_admin_ctx(), "  MCP Admin Team  ")
    team_id = created["id"]
    assert created["name"] == "MCP Admin Team"

    member = await admin_tools.admin_add_team_member(_admin_ctx(), team_id, TEST_USER_ID)
    assert member["user_id"] == TEST_USER_ID

    teams = await admin_tools.admin_list_teams(_admin_ctx())
    row = next(t for t in teams if t["id"] == team_id)
    assert row["member_count"] == 1

    # Delete preview: nothing removed.
    preview = await admin_tools.admin_delete_team(_admin_ctx(), team_id)
    assert preview["confirmed"] is False
    assert preview["preview"]["member_count"] == 1
    assert preview["preview"]["share_count"] == 0
    sm = get_sessionmaker()
    async with sm() as session:
        assert await session.get(Team, team_id) is not None

    done = await admin_tools.admin_delete_team(_admin_ctx(), team_id, confirm=True)
    assert done["confirmed"] is True and done["deleted"] is True
    async with sm() as session:
        assert await session.get(Team, team_id) is None
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await admin_tools.admin_delete_team(_admin_ctx(), team_id)


# ── cross-user event-log metadata ────────────────────────────────────────────


async def test_admin_list_event_logs_shows_other_users_metadata(client: AsyncClient) -> None:
    await _ensure_user_b()
    await _set_consent(TEST_USER_ID, True)
    log_id = uuid7_str()
    log_name = f"adm-mcp-log-{log_id[:8]}"
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            EventLog(id=log_id, user_id=USER_B_ID, name=log_name, status="ready", created_at=_now())
        )
        await session.commit()

    page = await admin_tools.admin_list_event_logs(_admin_ctx(), q=log_name)
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == log_id
    assert item["owner_id"] == USER_B_ID
    assert item["owner_email"] == USER_B_EMAIL
    assert item["status"] == "ready"
    # Metadata only - no event rows, no file contents.
    assert "events" not in item and "path" not in item


# ── cross-user jobs: list, logs, previews, queue control ────────────────────


async def test_admin_jobs_list_logs_and_kill_preview_only(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="running", type_="test.admin_kill")

    page = await admin_tools.admin_list_jobs(_admin_ctx(), type="test.admin_kill")
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == job_id and item["owner_email"] == TEST_USER_EMAIL
    assert any(s["label"] == "running" for s in page["summary"]["by_status"])
    assert page["summary"]["active_total"] >= 1

    logs = await admin_tools.admin_get_job_logs(_admin_ctx(), job_id)
    assert logs["job_id"] == job_id and logs["lines"] == []

    # Kill preview: describes the job, mutates nothing.
    preview = await admin_tools.admin_kill_job(_admin_ctx(), job_id)
    assert preview["confirmed"] is False
    assert preview["preview"]["type"] == "test.admin_kill"
    assert preview["preview"]["title"] == "Admin test job"
    assert preview["preview"]["owner_email"] == TEST_USER_EMAIL
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await admin_tools.admin_kill_job(_admin_ctx(), "no-such-job")

    # Cancel-all preview: counts only, mutates nothing.
    preview_all = await admin_tools.admin_cancel_all(_admin_ctx(), user_id=TEST_USER_ID)
    assert preview_all["confirmed"] is False
    assert preview_all["preview"]["running"] >= 1
    assert preview_all["preview"]["total"] >= 1

    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(Job, job_id)
        assert row is not None and row.status == "running"


async def test_admin_pause_resume_user_queue(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    runtime = get_job_runtime()

    paused = await admin_tools.admin_pause_user_queue(_admin_ctx(), TEST_USER_ID)
    assert paused["paused"] is True
    assert runtime.is_paused(TEST_USER_ID) is True

    resumed = await admin_tools.admin_resume_user_queue(_admin_ctx(), TEST_USER_ID)
    assert resumed["paused"] is False
    assert runtime.is_paused(TEST_USER_ID) is False

    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await admin_tools.admin_pause_user_queue(_admin_ctx(), "no-such-user")


# ── insights + token/share surfaces ──────────────────────────────────────────


async def test_admin_insights_sections_and_invalid(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)

    overview = await admin_tools.admin_insights(_admin_ctx(), "overview")
    assert overview["days"] == 30
    assert overview["kpis"]["user_count"] >= 1

    storage = await admin_tools.admin_insights(_admin_ctx(), "storage")
    assert storage["disk_included"] is False  # never the expensive walk

    jobs = await admin_tools.admin_insights(_admin_ctx(), "jobs", days=7)
    assert jobs["days"] == 7 and "runtime" in jobs

    with pytest.raises(MCPToolError, match=r"\[invalid\]"):
        await admin_tools.admin_insights(_admin_ctx(), "bogus")


async def test_admin_token_and_share_tools_not_found_paths(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)

    tokens = await admin_tools.admin_list_api_tokens(_admin_ctx())
    assert isinstance(tokens, list)
    shares = await admin_tools.admin_list_dashboard_shares(_admin_ctx())
    assert isinstance(shares, list)

    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await admin_tools.admin_revoke_api_token(_admin_ctx(), "no-such-token")
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await admin_tools.admin_revoke_dashboard_share(_admin_ctx(), "no-such-share")
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await admin_tools.admin_remove_team_member(_admin_ctx(), "no-team", TEST_USER_ID)
