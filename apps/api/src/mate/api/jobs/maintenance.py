"""Jobs-table retention (S3_OFFLOAD.md Phase 2.2).

The ``jobs`` table grows one row per job forever; on a busy deployment it is a
top driver of metadata.db size. This prunes *terminal* jobs older than a
configured window. Active jobs (queued/running) are never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import Job

log = structlog.get_logger(__name__)

# Statuses a job can rest in once it's done - only these are eligible for pruning.
_TERMINAL = ("completed", "failed", "cancelled")


async def prune_old_jobs(session: AsyncSession, retention_days: int) -> int:
    """Delete terminal jobs older than ``retention_days``. Returns rows removed.

    No-op when ``retention_days <= 0`` (retention disabled / keep forever). Age is
    measured by ``finished_at``, falling back to ``created_at`` for any terminal
    row that never recorded a finish time. ``parent_job_id`` is ``ON DELETE SET
    NULL``, so deleting a parent before its children is safe.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
    age = func.coalesce(Job.finished_at, Job.created_at)
    where = (Job.status.in_(_TERMINAL), age < cutoff)
    # Count then delete (same txn) rather than reading the DML rowcount - one
    # cheap COUNT a day, and it keeps the return type clean.
    removed = int(await session.scalar(select(func.count()).select_from(Job).where(*where)) or 0)
    if removed:
        await session.execute(delete(Job).where(*where))
        await session.commit()
        log.info("jobs.retention.pruned", removed=removed, retention_days=retention_days)
    return removed


async def count_prunable_jobs(session: AsyncSession, retention_days: int) -> int:
    """How many jobs ``prune_old_jobs`` would remove now (for an admin preview)."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
    age = func.coalesce(Job.finished_at, Job.created_at)
    return int(
        await session.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(_TERMINAL), age < cutoff)
        )
        or 0
    )


__all__ = ["count_prunable_jobs", "prune_old_jobs"]
