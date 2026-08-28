"""Tests for the MCP jobs toolset: list/get ownership, wait_for_job (immediate
return, timeout, bus-event path), cancel/retry conflicts, cancel-all preview,
queue pause/resume, scope denial.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Job, User, UserSetting
from mate.api.events import get_event_bus
from mate.api.jobs.runtime import get_job_runtime
from mate.api.mcp import limits
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.scopes import ALL_SCOPES, SCOPE_JOBS_READ
from mate.api.mcp.toolsets import jobs as jobs_tools
from mate.api.uuid7 import uuid7_str

from .conftest import TEST_USER_ID

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
                User(id=USER_B_ID, email="b3@mate.local", created_at=_now(), last_seen_at=_now())
            )
            await session.commit()


async def _seed_job(
    user_id: str,
    *,
    status: str = "completed",
    type_: str = "test.op",
    title: str = "Test job",
    payload: dict[str, Any] | None = None,
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
                payload_json=payload or {},
                status=status,
                created_at=now,
                started_at=None if status == "queued" else now,
                finished_at=now if status in ("completed", "failed", "cancelled") else None,
            )
        )
        await session.commit()
    return job_id


# ── list / get: ownership isolation ──────────────────────────────────────────


async def test_list_and_get_jobs_are_tenant_isolated(client: AsyncClient) -> None:
    await _ensure_user_b()
    await _set_consent(TEST_USER_ID, True)
    await _set_consent(USER_B_ID, True)
    job_a = await _seed_job(TEST_USER_ID, type_="test.iso")
    job_b = await _seed_job(USER_B_ID, type_="test.iso")

    page = await jobs_tools.list_jobs(_ctx(), type="test.iso")
    ids = {j["id"] for j in page["items"]}
    assert job_a in ids and job_b not in ids

    page_b = await jobs_tools.list_jobs(_ctx(USER_B_ID), type="test.iso")
    ids_b = {j["id"] for j in page_b["items"]}
    assert job_b in ids_b and job_a not in ids_b

    got = await jobs_tools.get_job(_ctx(), job_a)
    assert got["id"] == job_a and got["status"] == "completed"

    # Foreign job id → 404, indistinguishable from missing.
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await jobs_tools.get_job(_ctx(), job_b)
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await jobs_tools.get_job(_ctx(), "no-such-job")


async def test_list_jobs_status_filter_and_payload(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    plan = {"precompute_plan": [{"module_id": "discovery", "job_id": "x"}]}
    failed_id = await _seed_job(TEST_USER_ID, status="failed", type_="test.filter", payload=plan)
    await _seed_job(TEST_USER_ID, status="completed", type_="test.filter")

    page = await jobs_tools.list_jobs(_ctx(), status="failed", type="test.filter")
    assert [j["id"] for j in page["items"]] == [failed_id]
    # JobDetail shape: payload_json (incl. precompute_plan) rides along.
    assert page["items"][0]["payload_json"]["precompute_plan"] == plan["precompute_plan"]
    assert page["total"] == 1


# ── wait_for_job ─────────────────────────────────────────────────────────────


async def test_wait_for_job_returns_immediately_on_terminal_row(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="completed")

    t0 = time.monotonic()
    res = await jobs_tools.wait_for_job(_ctx(), job_id, timeout_seconds=20)
    assert time.monotonic() - t0 < 2
    assert res["timed_out"] is False
    assert res["id"] == job_id and res["status"] == "completed"


async def test_wait_for_job_times_out_with_running_row(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="running")

    t0 = time.monotonic()
    res = await jobs_tools.wait_for_job(_ctx(), job_id, timeout_seconds=1)
    elapsed = time.monotonic() - t0
    assert elapsed < 5, f"wait_for_job took {elapsed:.1f}s for a 1s budget"
    # A hit deadline is a payload, not an error.
    assert res["timed_out"] is True
    assert res["status"] == "running"


async def test_wait_for_job_wakes_on_bus_terminal_event(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="running")

    async def _finish() -> None:
        await asyncio.sleep(0.3)
        sm = get_sessionmaker()
        async with sm() as session:
            row = await session.get(Job, job_id)
            assert row is not None
            row.status = "completed"
            row.finished_at = _now()
            await session.commit()
        # Same ordering as the runtime: row committed, then event published.
        await get_event_bus().publish(
            "job.completed", {"id": job_id, "user_id": TEST_USER_ID, "type": "test.op"}
        )

    finisher = asyncio.create_task(_finish())
    t0 = time.monotonic()
    res = await jobs_tools.wait_for_job(_ctx(), job_id, timeout_seconds=20)
    elapsed = time.monotonic() - t0
    await finisher
    assert res["timed_out"] is False and res["status"] == "completed"
    # Woken by the event (~0.3s), not the 2s idle-tick row poll or the deadline.
    assert elapsed < 1.5, f"event-driven wait took {elapsed:.1f}s"


async def test_wait_for_job_foreign_job_is_not_found(client: AsyncClient) -> None:
    await _ensure_user_b()
    await _set_consent(TEST_USER_ID, True)
    job_b = await _seed_job(USER_B_ID, status="running")
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await jobs_tools.wait_for_job(_ctx(), job_b, timeout_seconds=1)


# ── cancel / retry / cancel-all / queue control ──────────────────────────────


async def test_cancel_job_on_terminal_row_conflicts(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="completed")
    with pytest.raises(MCPToolError, match=r"\[conflict\]"):
        await jobs_tools.cancel_job(_ctx(), job_id)


async def test_cancel_job_requires_control_scope(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="queued")
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await jobs_tools.cancel_job(_ctx(scopes=(SCOPE_JOBS_READ,)), job_id)


async def test_retry_job_on_completed_conflicts(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    job_id = await _seed_job(TEST_USER_ID, status="completed")
    with pytest.raises(MCPToolError, match=r"\[conflict\]"):
        await jobs_tools.retry_job(_ctx(), job_id)


async def test_cancel_all_jobs_preview_then_confirm(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    queued_id = await _seed_job(TEST_USER_ID, status="queued", type_="test.cancel_all")

    sm = get_sessionmaker()
    async with sm() as session:
        active = (
            (
                await session.execute(
                    select(Job.id).where(
                        Job.user_id == TEST_USER_ID, Job.status.in_(("queued", "running"))
                    )
                )
            )
            .scalars()
            .all()
        )

    preview = await jobs_tools.cancel_all_jobs(_ctx())
    assert preview["confirmed"] is False
    assert preview["preview"]["total"] == len(active)
    async with sm() as session:
        row = await session.get(Job, queued_id)
        assert row is not None and row.status == "queued"  # dry run mutated nothing

    result = await jobs_tools.cancel_all_jobs(_ctx(), confirm=True)
    assert result["confirmed"] is True and result["cancelled"] >= 1
    async with sm() as session:
        row = await session.get(Job, queued_id)
        assert row is not None and row.status == "cancelled"


async def test_pause_and_resume_my_queue(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    runtime = get_job_runtime()
    assert (await jobs_tools.pause_my_queue(_ctx()))["paused"] is True
    assert runtime.is_paused(TEST_USER_ID) is True
    assert (await jobs_tools.resume_my_queue(_ctx()))["paused"] is False
    assert runtime.is_paused(TEST_USER_ID) is False
