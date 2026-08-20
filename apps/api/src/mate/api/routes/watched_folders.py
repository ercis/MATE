"""CRUD + scan endpoints for /api/v1/watched-folders.

A watched folder is a persistent import *source*: a location in the active
storage backend that Mate scans on a cadence (or on demand), importing any
new/changed file through the normal ``event_log.import`` pipeline. See
``mate.api.ingest.watch`` for the scan engine and ``mate.api.ingest.source`` for
the local-disk vs S3 abstraction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from mate.api.auth import CurrentUserDep, get_owned_folder, get_owned_watched_folder
from mate.api.db.models import Folder, WatchedFolder, WatchedFolderFile
from mate.api.db.session import SessionDep
from mate.api.ingest.source import default_source_path, ensure_managed_dir, list_source
from mate.api.ingest.watch import scan_watch
from mate.api.jobs.runtime import JobRuntime, get_job_runtime
from mate.api.schemas.watched_folders import (
    ScanResponse,
    WatchedFileSummary,
    WatchedFolderCreate,
    WatchedFolderDetail,
    WatchedFolderSummary,
    WatchedFolderUpdate,
)
from mate.api.storage import s3
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/watched-folders", tags=["watched-folders"])


def _runtime_dep() -> JobRuntime:
    return get_job_runtime()


_RuntimeDep = Annotated[JobRuntime, Depends(_runtime_dep)]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _ledger_counts(session: SessionDep, watch_ids: list[str]) -> dict[str, dict[str, int]]:
    """{watch_id: {status: count}} rollup of the dedup ledger."""
    if not watch_ids:
        return {}
    rows = (
        await session.execute(
            select(
                WatchedFolderFile.watch_id,
                WatchedFolderFile.status,
                func.count().label("n"),
            )
            .where(WatchedFolderFile.watch_id.in_(watch_ids))
            .group_by(WatchedFolderFile.watch_id, WatchedFolderFile.status)
        )
    ).all()
    out: dict[str, dict[str, int]] = {}
    for watch_id, status_, n in rows:
        out.setdefault(watch_id, {})[status_] = int(n)
    return out


def _summary(row: WatchedFolder, counts: dict[str, int]) -> WatchedFolderSummary:
    summary = WatchedFolderSummary.model_validate(row)
    summary.imported_count = counts.get("imported", 0)
    summary.failed_count = counts.get("failed", 0)
    return summary


@router.get("", response_model=list[WatchedFolderSummary])
async def list_watched_folders(
    session: SessionDep, user: CurrentUserDep
) -> list[WatchedFolderSummary]:
    rows = (
        (
            await session.execute(
                select(WatchedFolder)
                .where(WatchedFolder.user_id == user.id, WatchedFolder.deleted_at.is_(None))
                .order_by(WatchedFolder.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    counts = await _ledger_counts(session, [r.id for r in rows])
    return [_summary(r, counts.get(r.id, {})) for r in rows]


@router.post("", response_model=WatchedFolderSummary, status_code=status.HTTP_201_CREATED)
async def create_watched_folder(
    payload: WatchedFolderCreate, session: SessionDep, user: CurrentUserDep
) -> WatchedFolderSummary:
    watch_id = uuid7_str()
    managed = not (payload.source_path and payload.source_path.strip())
    source_path = (
        default_source_path(user.id, watch_id) if managed else payload.source_path.strip()  # type: ignore[union-attr]
    )

    # Resolve / create the destination folder.
    dest_folder_id: str | None = None
    if payload.dest_folder_id is not None:
        await get_owned_folder(session, payload.dest_folder_id, user.id)
        dest_folder_id = payload.dest_folder_id
    elif payload.create_dest_folder:
        dest_folder_id = await _create_dest_folder(session, user.id, payload.name.strip())

    # Make the managed local dir so files can be dropped in immediately, then
    # confirm the source is reachable (surfaces S3 credential/connection errors).
    try:
        await asyncio.to_thread(ensure_managed_dir, source_path)
        await asyncio.to_thread(list_source, source_path)
    except s3.StorageError as exc:
        raise HTTPException(status_code=400, detail=f"Source not reachable: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Source not reachable: {exc}") from exc

    watch = WatchedFolder(
        id=watch_id,
        user_id=user.id,
        name=payload.name.strip(),
        dest_folder_id=dest_folder_id,
        source_path=source_path,
        mode=payload.mode,
        interval_seconds=payload.interval_seconds,
        status="active",
        default_mapping=payload.default_mapping,
        created_at=_utcnow(),
    )
    session.add(watch)
    await session.commit()
    log.info("watched_folder.created", watch_id=watch_id, mode=payload.mode, managed=managed)
    return _summary(watch, {})


async def _create_dest_folder(session: SessionDep, user_id: str, name: str) -> str:
    """Append a new root /processes folder (mirrors routes.folders.create_folder)."""
    sibling_positions = list(
        (
            await session.execute(
                select(Folder.position).where(
                    Folder.user_id == user_id,
                    Folder.deleted_at.is_(None),
                    Folder.parent_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    next_pos = (max(sibling_positions) + 1) if sibling_positions else 0
    folder = Folder(
        id=uuid7_str(),
        user_id=user_id,
        name=name,
        parent_id=None,
        position=next_pos,
        created_at=_utcnow(),
    )
    session.add(folder)
    await session.flush()
    return folder.id


@router.get("/{watch_id}", response_model=WatchedFolderDetail)
async def get_watched_folder(
    watch_id: str, session: SessionDep, user: CurrentUserDep
) -> WatchedFolderDetail:
    row = await get_owned_watched_folder(session, watch_id, user.id)
    counts = (await _ledger_counts(session, [watch_id])).get(watch_id, {})
    files = (
        (
            await session.execute(
                select(WatchedFolderFile)
                .where(WatchedFolderFile.watch_id == watch_id)
                .order_by(WatchedFolderFile.imported_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    detail = WatchedFolderDetail.model_validate(row)
    detail.imported_count = counts.get("imported", 0)
    detail.failed_count = counts.get("failed", 0)
    detail.files = [WatchedFileSummary.model_validate(f) for f in files]
    return detail


@router.patch("/{watch_id}", response_model=WatchedFolderSummary)
async def update_watched_folder(
    watch_id: str,
    payload: WatchedFolderUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> WatchedFolderSummary:
    row = await get_owned_watched_folder(session, watch_id, user.id)

    if payload.name is not None:
        cleaned = payload.name.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Name cannot be empty.")
        row.name = cleaned
    if payload.mode is not None:
        row.mode = payload.mode
    if "interval_seconds" in payload.model_fields_set:
        row.interval_seconds = payload.interval_seconds
    if payload.status is not None:
        # Re-activating clears a prior error state so the poller resumes.
        row.status = payload.status
        if payload.status == "active":
            row.last_error = None
    if "default_mapping" in payload.model_fields_set:
        row.default_mapping = payload.default_mapping

    # Guard the manual→interval transition needing a cadence.
    if row.mode == "interval" and row.interval_seconds is None:
        raise HTTPException(
            status_code=422, detail="interval_seconds is required for interval mode."
        )

    await session.commit()
    counts = (await _ledger_counts(session, [watch_id])).get(watch_id, {})
    return _summary(row, counts)


@router.delete("/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watched_folder(watch_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    """Soft-delete a watch (stops scanning). Source files and already-imported
    logs are left untouched."""
    row = await get_owned_watched_folder(session, watch_id, user.id)
    row.deleted_at = _utcnow()
    await session.commit()
    log.info("watched_folder.deleted", watch_id=watch_id)


@router.post("/{watch_id}/scan", response_model=ScanResponse)
async def scan_watched_folder(
    watch_id: str,
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
) -> ScanResponse:
    """Scan now - list the source and import any new/changed files immediately."""
    row = await get_owned_watched_folder(session, watch_id, user.id)
    result = await scan_watch(row, session=session, runtime=runtime)
    return ScanResponse(
        found=result.found,
        imported=result.imported,
        skipped=result.skipped,
        failed=result.failed,
    )
