"""Jobs-table retention (storage.module S3_OFFLOAD Phase 2.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Job, User
from mate.api.jobs.maintenance import count_prunable_jobs, prune_old_jobs


async def _a_user_id() -> str:
    async with get_sessionmaker()() as s:
        uid = await s.scalar(select(User.id).limit(1))
    assert uid is not None
    return uid


def _job(
    jid: str, uid: str, status: str, *, finished_days: int | None, created_days: int = 0
) -> Job:
    now = datetime.now(UTC).replace(tzinfo=None)
    return Job(
        id=jid,
        user_id=uid,
        type="test.job",
        title="t",
        status=status,
        created_at=now - timedelta(days=created_days),
        finished_at=(now - timedelta(days=finished_days)) if finished_days is not None else None,
    )


async def _cleanup(ids: list[str]) -> None:
    async with get_sessionmaker()() as s:
        for jid in ids:
            row = await s.get(Job, jid)
            if row is not None:
                await s.delete(row)
        await s.commit()


async def test_prune_removes_only_old_terminal_jobs() -> None:
    uid = await _a_user_id()
    ids = ["jm-old-done", "jm-old-failed", "jm-recent-done", "jm-old-running", "jm-old-queued"]
    async with get_sessionmaker()() as s:
        s.add_all(
            [
                _job("jm-old-done", uid, "completed", finished_days=40),
                _job("jm-old-failed", uid, "failed", finished_days=40),
                _job("jm-recent-done", uid, "completed", finished_days=1),
                _job("jm-old-running", uid, "running", finished_days=None, created_days=40),
                _job("jm-old-queued", uid, "queued", finished_days=None, created_days=40),
            ]
        )
        await s.commit()
    try:
        async with get_sessionmaker()() as s:
            removed = await prune_old_jobs(s, retention_days=30)
        assert removed >= 2
        async with get_sessionmaker()() as s:
            assert await s.get(Job, "jm-old-done") is None
            assert await s.get(Job, "jm-old-failed") is None
            assert await s.get(Job, "jm-recent-done") is not None  # within window
            assert await s.get(Job, "jm-old-running") is not None  # active, never pruned
            assert await s.get(Job, "jm-old-queued") is not None  # active, never pruned
    finally:
        await _cleanup(ids)


async def test_prune_disabled_is_noop() -> None:
    uid = await _a_user_id()
    async with get_sessionmaker()() as s:
        s.add(_job("jm-disabled", uid, "completed", finished_days=999))
        await s.commit()
    try:
        async with get_sessionmaker()() as s:
            assert await prune_old_jobs(s, retention_days=0) == 0
            assert await s.get(Job, "jm-disabled") is not None
    finally:
        await _cleanup(["jm-disabled"])


async def test_count_prunable_jobs() -> None:
    uid = await _a_user_id()
    async with get_sessionmaker()() as s:
        s.add(_job("jm-count", uid, "cancelled", finished_days=50))
        await s.commit()
    try:
        async with get_sessionmaker()() as s:
            assert await count_prunable_jobs(s, retention_days=30) >= 1
            assert await count_prunable_jobs(s, retention_days=0) == 0
    finally:
        await _cleanup(["jm-count"])
