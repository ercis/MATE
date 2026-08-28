"""Admin jobs - cross-user job monitoring + control gated by the ``admin`` role.

Mirrors ``test_admin_insights``: the default test user holds only ``user`` (403
path), ``admin_client`` re-overrides ``get_current_user`` with an admin (200
path). Jobs are inserted directly so the tests don't depend on a real import.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_EMAIL, TEST_USER_ID


def _naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    """Like the shared ``client`` fixture but the current user is an admin."""
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


async def _insert_job(
    *, status: str, type_: str = "event_log.import", payload: dict | None = None
) -> str:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job

    job_id = str(uuid.uuid4())
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            Job(
                id=job_id,
                user_id=TEST_USER_ID,
                type=type_,
                title=f"Test {status}",
                payload_json=payload or {},
                status=status,
                created_at=_naive_now(),
            )
        )
        await session.commit()
    return job_id


async def _delete_jobs(*job_ids: str) -> None:
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job

    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(Job).where(Job.id.in_(job_ids)))
        await session.commit()


@pytest.mark.asyncio
async def test_admin_jobs_require_admin(client: AsyncClient) -> None:
    # A plain ``user`` is forbidden from the listing and every control route.
    assert (await client.get("/api/v1/admin/jobs")).status_code == 403
    assert (await client.post("/api/v1/admin/jobs/cancel-all", json={})).status_code == 403
    assert (await client.post("/api/v1/admin/jobs/x/kill")).status_code == 403
    assert (
        await client.post("/api/v1/admin/jobs/queue/pause", json={"user_id": TEST_USER_ID})
    ).status_code == 403


@pytest.mark.asyncio
async def test_admin_jobs_lists_with_owner_and_log(admin_client: AsyncClient) -> None:
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    log_id = str(uuid.uuid4())
    marker = f"admin-jobs-{log_id[:8]}"
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            EventLog(
                id=log_id,
                user_id=TEST_USER_ID,
                name=marker,
                status="ready",
                created_at=_naive_now(),
            )
        )
        await session.commit()

    job_id = await _insert_job(status="running", payload={"log_id": log_id})
    try:
        resp = await admin_client.get("/api/v1/admin/jobs?status=running")
        assert resp.status_code == 200
        body = resp.json()
        row = next((r for r in body["items"] if r["id"] == job_id), None)
        assert row is not None
        assert row["owner_id"] == TEST_USER_ID
        assert row["owner_email"] == TEST_USER_EMAIL
        assert row["owner_username"] == "test"
        assert row["log_id"] == log_id
        assert row["log_name"] == marker
        # Summary carries the status breakdown + an active count covering it.
        assert any(s["label"] == "running" for s in body["summary"]["by_status"])
        assert body["summary"]["active_total"] >= 1

        # The owner's email is searchable.
        by_owner = await admin_client.get(f"/api/v1/admin/jobs?q={TEST_USER_EMAIL}")
        assert any(r["id"] == job_id for r in by_owner.json()["items"])
    finally:
        await _delete_jobs(job_id)
        async with sm() as session:
            await session.execute(delete(EventLog).where(EventLog.id == log_id))
            await session.commit()


@pytest.mark.asyncio
async def test_admin_cancel_queued_then_conflict(admin_client: AsyncClient) -> None:
    job_id = await _insert_job(status="queued")
    try:
        first = await admin_client.post(f"/api/v1/admin/jobs/{job_id}/cancel")
        assert first.status_code == 204
        # Already terminal → 409 on a second attempt.
        again = await admin_client.post(f"/api/v1/admin/jobs/{job_id}/cancel")
        assert again.status_code == 409
    finally:
        await _delete_jobs(job_id)


@pytest.mark.asyncio
async def test_admin_kill_queued_then_conflict(admin_client: AsyncClient) -> None:
    job_id = await _insert_job(status="queued")
    try:
        first = await admin_client.post(f"/api/v1/admin/jobs/{job_id}/kill")
        assert first.status_code == 204
        # Already terminal → 409 on a second attempt.
        again = await admin_client.post(f"/api/v1/admin/jobs/{job_id}/kill")
        assert again.status_code == 409
    finally:
        await _delete_jobs(job_id)


@pytest.mark.asyncio
async def test_admin_retry_only_failed(admin_client: AsyncClient) -> None:
    job_id = await _insert_job(status="completed")
    try:
        rejected = await admin_client.post(f"/api/v1/admin/jobs/{job_id}/retry")
        assert rejected.status_code == 409
    finally:
        await _delete_jobs(job_id)


@pytest.mark.asyncio
async def test_admin_pause_resume_reflected_in_summary(admin_client: AsyncClient) -> None:
    try:
        paused = await admin_client.post(
            "/api/v1/admin/jobs/queue/pause", json={"user_id": TEST_USER_ID}
        )
        assert paused.status_code == 204

        listing = await admin_client.get("/api/v1/admin/jobs")
        assert TEST_USER_ID in listing.json()["summary"]["paused_users"]
    finally:
        resumed = await admin_client.post(
            "/api/v1/admin/jobs/queue/resume", json={"user_id": TEST_USER_ID}
        )
        assert resumed.status_code == 204

    after = await admin_client.get("/api/v1/admin/jobs")
    assert TEST_USER_ID not in after.json()["summary"]["paused_users"]
