"""Small helpers used by route handlers to enforce per-user ownership.

The pattern across every CRUD route is the same:

  - look up a row by id,
  - check it belongs to the current user,
  - 404 on mismatch (not 403 - avoids id enumeration).

These helpers wrap that pattern so the route code stays readable.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import EventLog, Folder, Job, WatchedFolder


def _404(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


async def get_owned_event_log(
    session: AsyncSession,
    log_id: str,
    user_id: str,
    *,
    allow_deleted: bool = False,
) -> EventLog:
    row = await session.get(EventLog, log_id)
    if row is None or row.user_id != user_id:
        raise _404("Event log not found.")
    if not allow_deleted and row.deleted_at is not None:
        raise _404("Event log not found.")
    return row


async def get_owned_folder(
    session: AsyncSession,
    folder_id: str,
    user_id: str,
    *,
    allow_deleted: bool = False,
) -> Folder:
    row = await session.get(Folder, folder_id)
    if row is None or row.user_id != user_id:
        raise _404("Folder not found.")
    if not allow_deleted and row.deleted_at is not None:
        raise _404("Folder not found.")
    return row


async def get_owned_job(session: AsyncSession, job_id: str, user_id: str) -> Job:
    row = await session.get(Job, job_id)
    if row is None or row.user_id != user_id:
        raise _404("Job not found.")
    return row


async def get_owned_watched_folder(
    session: AsyncSession,
    watch_id: str,
    user_id: str,
    *,
    allow_deleted: bool = False,
) -> WatchedFolder:
    row = await session.get(WatchedFolder, watch_id)
    if row is None or row.user_id != user_id:
        raise _404("Watched folder not found.")
    if not allow_deleted and row.deleted_at is not None:
        raise _404("Watched folder not found.")
    return row
