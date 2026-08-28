"""Watched-folders toolset: persistent auto-import sources (CRUD + scan).

Mirrors :mod:`mate.api.routes.watched_folders` one-to-one - the ledger rollup
and destination-folder creation are imported from the route module so the two
surfaces can never drift. Validation reuses the route's Pydantic schemas;
delete is the same soft delete (source files and imported logs are kept).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import get_owned_folder, get_owned_watched_folder
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import WatchedFolder, WatchedFolderFile
from mate.api.ingest.source import default_source_path, ensure_managed_dir, list_source
from mate.api.ingest.watch import scan_watch
from mate.api.jobs.runtime import get_job_runtime
from mate.api.mcp.core import MCPContext, authz, cap, confirm_preview, guarded
from mate.api.mcp.errors import CODE_INVALID, from_http_exception, tool_error
from mate.api.mcp.registry import mcp_tool
from mate.api.mcp.scopes import SCOPE_WATCHED_READ, SCOPE_WATCHED_WRITE

# Aliased: the create tool has a ``create_dest_folder`` bool parameter that
# would otherwise shadow the helper inside its closure.
from mate.api.routes.watched_folders import create_dest_folder as create_dest_process_folder
from mate.api.routes.watched_folders import ledger_counts
from mate.api.schemas.watched_folders import (
    WatchedFileSummary,
    WatchedFolderCreate,
    WatchedFolderDetail,
    WatchedFolderSummary,
    WatchedFolderUpdate,
)
from mate.api.storage import s3
from mate.api.uuid7 import uuid7_str

# The detail tool caps its file-ledger list like the route does.
_FILES_LIMIT = 200


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _summary_dict(row: WatchedFolder, counts: dict[str, int]) -> dict[str, Any]:
    summary = WatchedFolderSummary.model_validate(row)
    summary.imported_count = counts.get("imported", 0)
    summary.failed_count = counts.get("failed", 0)
    return summary.model_dump(mode="json")


async def _ensure_owned_watch(
    session: AsyncSession, watched_folder_id: str, user_id: str
) -> WatchedFolder:
    """Ownership gate, translated to a tool error (404 for missing AND foreign)."""
    try:
        return await get_owned_watched_folder(session, watched_folder_id, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


@mcp_tool(toolset="watched", idempotent=True)
async def list_watched_folders(ctx: MCPContext) -> list[dict[str, Any]]:
    """List your watched import folders with imported/failed file counts."""
    p = await authz(ctx, SCOPE_WATCHED_READ)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(WatchedFolder)
                        .where(
                            WatchedFolder.user_id == p.user.id,
                            WatchedFolder.deleted_at.is_(None),
                        )
                        .order_by(WatchedFolder.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            counts = await ledger_counts(session, [r.id for r in rows])
        return [_summary_dict(r, counts.get(r.id, {})) for r in rows]

    return await guarded(p, "list_watched_folders", {}, _impl())


@mcp_tool(toolset="watched", idempotent=True)
async def get_watched_folder(ctx: MCPContext, watched_folder_id: str) -> dict[str, Any]:
    """One watched folder's detail plus its most recent file-ledger entries (max 200)."""
    p = await authz(ctx, SCOPE_WATCHED_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned_watch(session, watched_folder_id, p.user.id)
            counts = (await ledger_counts(session, [row.id])).get(row.id, {})
            files = (
                (
                    await session.execute(
                        select(WatchedFolderFile)
                        .where(WatchedFolderFile.watch_id == row.id)
                        .order_by(WatchedFolderFile.imported_at.desc())
                        .limit(_FILES_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
            detail = WatchedFolderDetail.model_validate(row)
            detail.imported_count = counts.get("imported", 0)
            detail.failed_count = counts.get("failed", 0)
            detail.files = [WatchedFileSummary.model_validate(f) for f in files]
        return cap(detail.model_dump(mode="json"))

    return await guarded(p, "get_watched_folder", {"watched_folder_id": watched_folder_id}, _impl())


@mcp_tool(toolset="watched", write=True)
async def create_watched_folder(
    ctx: MCPContext,
    name: str,
    source_path: str | None = None,
    dest_folder_id: str | None = None,
    create_dest_folder: bool = False,
    mode: str = "manual",
    interval_seconds: int | None = None,
    default_mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a watched import folder (a persistent, auto-scanned import source).

    Empty ``source_path`` uses a Mate-managed location. ``mode`` is
    manual|interval|continuous (``interval_seconds`` >= 30 required for
    interval). Pass ``dest_folder_id`` to land imports in an existing folder,
    or ``create_dest_folder=true`` to create one named after the watch.
    """
    p = await authz(ctx, SCOPE_WATCHED_WRITE, write=True)
    try:
        payload = WatchedFolderCreate.model_validate(
            {
                "name": name,
                "source_path": source_path,
                "mode": mode,
                "interval_seconds": interval_seconds,
                "default_mapping": default_mapping,
                "create_dest_folder": create_dest_folder,
                "dest_folder_id": dest_folder_id,
            }
        )
    except ValidationError as exc:
        raise tool_error(CODE_INVALID, f"Invalid watched-folder spec: {exc}") from exc

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            watch_id = uuid7_str()
            raw = (payload.source_path or "").strip()
            src = raw or default_source_path(p.user.id, watch_id)

            # Resolve / create the destination folder (ownership 404s on foreign).
            dest_id: str | None = None
            if payload.dest_folder_id is not None:
                try:
                    await get_owned_folder(session, payload.dest_folder_id, p.user.id)
                except HTTPException as exc:
                    raise from_http_exception(exc) from exc
                dest_id = payload.dest_folder_id
            elif payload.create_dest_folder:
                dest_id = await create_dest_process_folder(session, p.user.id, payload.name.strip())

            # Same reachability gate as the route: make the managed dir, then
            # confirm the source lists (surfaces S3 credential/path errors).
            try:
                await asyncio.to_thread(ensure_managed_dir, src)
                await asyncio.to_thread(list_source, src)
            except (s3.StorageError, OSError) as exc:
                raise tool_error(CODE_INVALID, f"Source not reachable: {exc}") from exc

            watch = WatchedFolder(
                id=watch_id,
                user_id=p.user.id,
                name=payload.name.strip(),
                dest_folder_id=dest_id,
                source_path=src,
                mode=payload.mode,
                interval_seconds=payload.interval_seconds,
                status="active",
                default_mapping=payload.default_mapping,
                created_at=_utcnow(),
            )
            session.add(watch)
            await session.commit()
            return _summary_dict(watch, {})

    return await guarded(
        p, "create_watched_folder", {"name": name, "mode": mode}, _impl(), mutation=True
    )


@mcp_tool(toolset="watched", write=True)
async def update_watched_folder(
    ctx: MCPContext,
    watched_folder_id: str,
    name: str | None = None,
    mode: str | None = None,
    interval_seconds: int | None = None,
    status: str | None = None,
    default_mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a watched folder. Omitted fields are left unchanged.

    ``status`` accepts active|paused (re-activating clears a prior error).
    Switching to interval mode requires ``interval_seconds`` (>= 30).
    """
    p = await authz(ctx, SCOPE_WATCHED_WRITE, write=True)
    provided = {
        key: value
        for key, value in {
            "name": name,
            "mode": mode,
            "interval_seconds": interval_seconds,
            "status": status,
            "default_mapping": default_mapping,
        }.items()
        if value is not None
    }
    try:
        payload = WatchedFolderUpdate.model_validate(provided)
    except ValidationError as exc:
        raise tool_error(CODE_INVALID, f"Invalid update: {exc}") from exc

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned_watch(session, watched_folder_id, p.user.id)
            if payload.name is not None:
                cleaned = payload.name.strip()
                if not cleaned:
                    raise tool_error(CODE_INVALID, "Name cannot be empty.")
                row.name = cleaned
            if payload.mode is not None:
                row.mode = payload.mode
            if payload.interval_seconds is not None:
                row.interval_seconds = payload.interval_seconds
            if payload.status is not None:
                # Re-activating clears a prior error state so the poller resumes.
                row.status = payload.status
                if payload.status == "active":
                    row.last_error = None
            if payload.default_mapping is not None:
                row.default_mapping = payload.default_mapping

            # Guard the manual→interval transition needing a cadence.
            if row.mode == "interval" and row.interval_seconds is None:
                raise tool_error(CODE_INVALID, "interval_seconds is required for interval mode.")

            await session.commit()
            counts = (await ledger_counts(session, [row.id])).get(row.id, {})
            return _summary_dict(row, counts)

    return await guarded(
        p,
        "update_watched_folder",
        {"watched_folder_id": watched_folder_id},
        _impl(),
        mutation=True,
    )


@mcp_tool(toolset="watched", write=True)
async def scan_watched_folder(ctx: MCPContext, watched_folder_id: str) -> dict[str, int]:
    """Scan a watched folder now: import any new/changed source files.

    Enqueues one import job per new/changed file and returns the scan counts
    ``{found, imported, skipped, failed}``. Track the imports via list_jobs /
    wait_for_job.
    """
    p = await authz(ctx, SCOPE_WATCHED_WRITE, write=True)

    async def _impl() -> dict[str, int]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned_watch(session, watched_folder_id, p.user.id)
            result = await scan_watch(row, session=session, runtime=get_job_runtime())
        return {
            "found": result.found,
            "imported": result.imported,
            "skipped": result.skipped,
            "failed": result.failed,
        }

    return await guarded(
        p,
        "scan_watched_folder",
        {"watched_folder_id": watched_folder_id},
        _impl(),
        mutation=True,
    )


@mcp_tool(toolset="watched", write=True, destructive=True)
async def delete_watched_folder(
    ctx: MCPContext, watched_folder_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Delete a watched folder (stops scanning). Dry-runs without ``confirm``.

    Soft delete, exactly like the UI: source files and already-imported event
    logs are left untouched.
    """
    p = await authz(ctx, SCOPE_WATCHED_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned_watch(session, watched_folder_id, p.user.id)
            file_count = (
                await session.execute(
                    select(func.count())
                    .select_from(WatchedFolderFile)
                    .where(WatchedFolderFile.watch_id == row.id)
                )
            ).scalar_one()
            if not confirm:
                return confirm_preview(
                    "delete_watched_folder",
                    {
                        "watched_folder_id": row.id,
                        "name": row.name,
                        "files_seen": int(file_count),
                        "note": "Soft delete - source files and imported logs are kept.",
                    },
                )
            row.deleted_at = _utcnow()
            await session.commit()
        return {"deleted": True, "watched_folder_id": watched_folder_id}

    return await guarded(
        p,
        "delete_watched_folder",
        {"watched_folder_id": watched_folder_id, "confirm": confirm},
        _impl(),
        mutation=True,
    )
