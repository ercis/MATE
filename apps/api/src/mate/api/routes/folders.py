"""CRUD + reorder endpoints for /api/v1/folders.

Folders are hierarchical (arbitrary nesting) and live alongside event logs
on the /processes overview. Reorder bulk-updates positions and parents for
both folders and event logs in one shot - DnD on the client commits
exactly once when the drag ends.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from mate.api.auth import CurrentUserDep, get_owned_folder
from mate.api.db.models import EventLog, Folder
from mate.api.db.session import SessionDep
from mate.api.jobs.runtime import JobRuntime, get_job_runtime
from mate.api.schemas.event_logs import (
    FolderCreate,
    FolderSummary,
    FolderUpdate,
    ReorderRequest,
)

# The cycle guard / sibling-position / cascade-delete bodies live in the
# service layer so the MCP toolset can reuse them; these routes are thin
# adapters over it.
from mate.api.services.log_aggregates import (
    cascade_delete_folder,
    next_folder_position,
)
from mate.api.services.log_aggregates import (
    ensure_no_folder_cycle as _ensure_no_cycle,
)
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/folders", tags=["folders"])


def _runtime_dep() -> JobRuntime:
    return get_job_runtime()


_RuntimeDep = Annotated[JobRuntime, Depends(_runtime_dep)]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.get("", response_model=list[FolderSummary])
async def list_folders(session: SessionDep, user: CurrentUserDep) -> list[FolderSummary]:
    stmt = (
        select(Folder)
        .where(Folder.user_id == user.id, Folder.deleted_at.is_(None))
        .order_by(Folder.position.asc(), Folder.created_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [FolderSummary.model_validate(r) for r in rows]


@router.post("", response_model=FolderSummary, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate, session: SessionDep, user: CurrentUserDep
) -> FolderSummary:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")

    if payload.parent_id is not None:
        await get_owned_folder(session, payload.parent_id, user.id)

    # Append: position = max(sibling positions) + 1.
    next_pos = await next_folder_position(session, user.id, payload.parent_id)

    folder = Folder(
        id=uuid7_str(),
        user_id=user.id,
        name=name,
        parent_id=payload.parent_id,
        position=next_pos,
        created_at=_utcnow(),
    )
    session.add(folder)
    await session.commit()
    log.info("folder.created", folder_id=folder.id, parent_id=folder.parent_id)
    return FolderSummary.model_validate(folder)


@router.patch("/{folder_id}", response_model=FolderSummary)
async def update_folder(
    folder_id: str,
    payload: FolderUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> FolderSummary:
    folder = await get_owned_folder(session, folder_id, user.id)

    if payload.name is not None:
        cleaned = payload.name.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Name cannot be empty.")
        if len(cleaned) > 255:
            raise HTTPException(status_code=422, detail="Name is too long (max 255).")
        folder.name = cleaned

    if "parent_id" in payload.model_fields_set:
        if payload.parent_id is not None:
            await get_owned_folder(session, payload.parent_id, user.id)
        await _ensure_no_cycle(session, folder_id, payload.parent_id, user.id)
        folder.parent_id = payload.parent_id

    if payload.position is not None:
        folder.position = payload.position

    await session.commit()
    return FolderSummary.model_validate(folder)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
) -> None:
    """Soft-delete a folder, all descendant folders, and every event log inside.

    On-disk data for each affected event log (parquet outputs + original upload)
    is also removed. The frontend confirms intent before calling this.
    """
    await get_owned_folder(session, folder_id, user.id)
    await cascade_delete_folder(session, runtime, user.id, folder_id)


@router.post("/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder(payload: ReorderRequest, session: SessionDep, user: CurrentUserDep) -> None:
    """Bulk-update parent + position for any mix of folders and logs.

    The frontend calls this exactly once at the end of a drag with the full
    new ordering - that keeps the DB consistent even if the client has been
    showing optimistic state.
    """
    for item in payload.items:
        if item.kind == "folder":
            row = await session.get(Folder, item.id)
            if row is None or row.user_id != user.id or row.deleted_at is not None:
                continue
            if item.parent_id is not None:
                await _ensure_no_cycle(session, item.id, item.parent_id, user.id)
            row.parent_id = item.parent_id
            row.position = item.position
        else:  # log
            row = await session.get(EventLog, item.id)
            if row is None or row.user_id != user.id or row.deleted_at is not None:
                continue
            row.folder_id = item.parent_id
            row.position = item.position
    await session.commit()
