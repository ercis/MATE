"""Worker-concurrency live update (§7.6.1).

Two layers:
  - The ``JobRuntime.set_concurrency`` pool resize in isolation (own ``Settings``
    so the global singleton is never mutated by the test).
  - The ``/system/jobs`` GET/PUT surface: readable by anyone, writable only by an
    admin, and the change reflected on the live runtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_EMAIL, TEST_USER_ID


async def _settle(predicate, *, tries: int = 100, delay: float = 0.01) -> None:
    """Yield the loop until ``predicate()`` holds - graceful scale-down retires
    workers asynchronously (they consume a queue sentinel)."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(delay)
    assert predicate(), "condition never settled"


@pytest.mark.asyncio
async def test_set_concurrency_scales_pool() -> None:
    from mate.api.config import Settings
    from mate.api.jobs.runtime import JobRuntime

    # Own Settings instance - keeps the global get_settings() singleton (and any
    # other test) untouched by the live mutation set_concurrency performs.
    rt = JobRuntime(Settings())
    try:
        await rt.start()
        # The boot count comes from WORKER_CONCURRENCY (1 in the test env); don't
        # hard-code it - assert the pool matches whatever the target is.
        assert rt._live_worker_count() == rt.concurrency()

        # Scale up - workers spawn immediately.
        assert await rt.set_concurrency(5) == 5
        assert rt.concurrency() == 5
        assert rt._live_worker_count() == 5

        # Scale down - idle workers retire via the sentinel without orphaning.
        assert await rt.set_concurrency(2) == 2
        assert rt.concurrency() == 2
        await _settle(lambda: rt._live_worker_count() == 2)

        # Out-of-range values clamp to [MIN_WORKERS, MAX_WORKERS].
        assert await rt.set_concurrency(99) == 8
        await _settle(lambda: rt._live_worker_count() == 8)
        assert await rt.set_concurrency(0) == 1
        await _settle(lambda: rt._live_worker_count() == 1)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_get_jobs_config_non_admin(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/system/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["min"] == 1
    assert body["max"] == 8
    assert body["is_admin"] is False
    assert 1 <= body["worker_concurrency"] <= 8


@pytest.mark.asyncio
async def test_put_jobs_config_requires_admin(client: AsyncClient) -> None:
    resp = await client.put("/api/v1/system/jobs", json={"worker_concurrency": 4})
    assert resp.status_code == 403


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    from mate.api.auth.dependencies import CurrentUser, get_current_user
    from mate.api.main import create_app

    app = create_app()
    admin = CurrentUser(
        id=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        preferred_username="test",
        name="Test User",
        roles=("user", "admin"),
    )

    async def _admin_user() -> CurrentUser:
        return admin

    app.dependency_overrides[get_current_user] = _admin_user
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as c,
        app.router.lifespan_context(app),
    ):
        yield c


@pytest.mark.asyncio
async def test_admin_put_updates_runtime_and_persists(admin_client: AsyncClient) -> None:
    from mate.api.jobs.runtime import (
        WORKER_CONCURRENCY_KEY,
        get_job_runtime,
        load_persisted_concurrency,
    )

    try:
        resp = await admin_client.put("/api/v1/system/jobs", json={"worker_concurrency": 3})
        assert resp.status_code == 200
        assert resp.json()["worker_concurrency"] == 3

        # GET reflects the new value, and the live runtime was actually resized.
        assert (await admin_client.get("/api/v1/system/jobs")).json()["worker_concurrency"] == 3
        assert get_job_runtime().concurrency() == 3
        # Persisted so a restart re-applies it.
        assert await load_persisted_concurrency() == 3
    finally:
        # Restore the default so the shared session DB + global settings don't
        # leak a non-default value into later tests.
        await admin_client.put("/api/v1/system/jobs", json={"worker_concurrency": 2})
        from mate.api.db.engine import get_sessionmaker
        from mate.api.db.models import SystemSetting

        sm = get_sessionmaker()
        async with sm() as session:
            row = await session.get(SystemSetting, WORKER_CONCURRENCY_KEY)
            if row is not None:
                await session.delete(row)
                await session.commit()


@pytest.mark.asyncio
async def test_admin_put_out_of_range_422(admin_client: AsyncClient) -> None:
    # Body validation (Field le=8) rejects before the handler runs - no resize,
    # no persistence, so this stays hermetic.
    resp = await admin_client.put("/api/v1/system/jobs", json={"worker_concurrency": 99})
    assert resp.status_code == 422
