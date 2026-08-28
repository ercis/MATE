"""Processes toolset: event-log discovery + lifecycle control.

Read tools return metadata and aggregates only - the raw-row endpoints
(events table, column values, cell edits, OCEL rows, file downloads) are
deliberately never mirrored here (the MCP data wall). Tool bodies reuse the
service layer (`mate.api.services.log_aggregates`) so semantics match the HTTP
routes exactly; route-layer ``HTTPException``s are translated to tool errors.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import EventLog, Folder
from mate.api.mcp.core import (
    MCPContext,
    authz,
    cap,
    confirm_preview,
    ensure_owned_log,
    guarded,
)
from mate.api.mcp.errors import CODE_INVALID, CODE_NOT_FOUND, from_http_exception, tool_error
from mate.api.mcp.pagination import clamp_limit, decode_cursor, page_envelope
from mate.api.mcp.registry import mcp_resource, mcp_tool
from mate.api.mcp.scopes import SCOPE_PROCESSES_READ, SCOPE_PROCESSES_WRITE
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.event_logs import RemapColumnRoles
from mate.api.services import log_aggregates as svc
from mate.api.uuid7 import uuid7_str

_STATUSES = ("importing", "processing", "ready", "failed")

# Keys whose values could carry raw cell contents. Today's importers persist
# only column names/roles/counts in `detected_schema`, but strip defensively so
# a future parser addition can't leak row values through this tool.
_SCHEMA_SAMPLE_KEYS = frozenset(
    {"samples", "sample", "sample_values", "examples", "example_values", "preview", "preview_rows"}
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _process_row(r: EventLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "status": r.status,
        "log_model": r.log_model,
        "cases_count": r.cases_count,
        "events_count": r.events_count,
        "variants_count": r.variants_count,
        "objects_count": r.objects_count,
        "object_types_count": r.object_types_count,
        "date_min": utc_isoformat(r.date_min) if r.date_min else None,
        "date_max": utc_isoformat(r.date_max) if r.date_max else None,
        "folder_id": r.folder_id,
        "mapping_needs_review": r.mapping_needs_review,
        "created_at": utc_isoformat(r.created_at),
    }


def _sanitized_schema(value: Any) -> Any:
    """``detected_schema`` minus any per-column sample/example values."""
    if isinstance(value, dict):
        return {k: _sanitized_schema(v) for k, v in value.items() if k not in _SCHEMA_SAMPLE_KEYS}
    if isinstance(value, list):
        return [_sanitized_schema(v) for v in value]
    return value


def _detail_dict(r: EventLog) -> dict[str, Any]:
    """EventLogDetail-shaped dict (aggregates + settings, no raw cell values)."""
    out = _process_row(r)
    out.update(
        {
            "error": r.error,
            "source_format": r.source_format,
            "source_filename": r.source_filename,
            "description": r.description,
            "column_roles": r.column_roles,
            "column_overrides": r.column_overrides,
            "active_filter": r.active_filter,
            "imported_at": utc_isoformat(r.imported_at) if r.imported_at else None,
            "last_edited_at": utc_isoformat(r.last_edited_at) if r.last_edited_at else None,
            "detected_schema": _sanitized_schema(r.detected_schema),
        }
    )
    return out


def _folder_row(f: Folder) -> dict[str, Any]:
    return {
        "id": f.id,
        "name": f.name,
        "parent_id": f.parent_id,
        "position": f.position,
        "created_at": utc_isoformat(f.created_at),
    }


async def _owned_folder(session: AsyncSession, folder_id: str, user_id: str) -> Folder:
    """Ownership gate for folders (404-shaped for missing AND foreign - no id
    enumeration), mirroring ``auth.ownership.get_owned_folder``."""
    row = await session.get(Folder, folder_id)
    if row is None or row.user_id != user_id or row.deleted_at is not None:
        raise tool_error(CODE_NOT_FOUND, "Folder not found.")
    return row


async def _ready_case_centric(session: AsyncSession, log_id: str, user_id: str) -> EventLog:
    """Ownership + ready + case-centric gate, as a tool error ([conflict] when
    the log is importing/processing/failed or object-centric)."""
    try:
        return await svc.require_ready_case_centric(log_id, session, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


async def _ready_ocel(session: AsyncSession, log_id: str, user_id: str) -> EventLog:
    try:
        return await svc.require_ready_ocel(log_id, session, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


def _job_runtime() -> Any:
    """The process-wide job runtime (started by the app lifespan)."""
    from mate.api.jobs.runtime import get_job_runtime

    return get_job_runtime()


async def _list_processes(
    user_id: str, *, offset: int, limit: int, status: str | None, q: str | None
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        conds = [EventLog.user_id == user_id, EventLog.deleted_at.is_(None)]
        if status:
            conds.append(EventLog.status == status)
        if q:
            conds.append(EventLog.name.ilike(f"%{q}%"))
        total = (
            await session.execute(select(func.count()).select_from(EventLog).where(*conds))
        ).scalar_one()
        rows = await session.execute(
            select(EventLog)
            .where(*conds)
            .order_by(desc(EventLog.created_at))
            .offset(offset)
            .limit(limit)
        )
        items = [_process_row(r) for r in rows.scalars().all()]
    return page_envelope(items, offset=offset, limit=limit, total=total)


@mcp_tool(toolset="processes", idempotent=True)
async def list_processes(
    ctx: MCPContext,
    cursor: str | None = None,
    limit: int | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """List your event logs (processes) with aggregate stats.

    Returns id, name, status (importing|processing|ready|failed), log_model and
    case/event/variant counts. Use a returned id as ``log_id`` for the other
    tools. ``status`` / ``q`` filter; ``cursor``/``limit`` paginate (pass back
    ``next_cursor``).
    """
    p = await authz(ctx, SCOPE_PROCESSES_READ)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)
    if status is not None and status not in _STATUSES:
        raise tool_error(CODE_INVALID, f"status must be one of {', '.join(_STATUSES)}")

    return await guarded(
        p,
        "list_processes",
        {"status": status or "", "q": q or ""},
        _list_processes(p.user.id, offset=offset, limit=size, status=status, q=q),
    )


# ── read tools ───────────────────────────────────────────────────────────────


@mcp_tool(toolset="processes", idempotent=True)
async def get_process(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Full metadata for one process (event log).

    Status/error, counts, dates, folder, description, column roles/overrides,
    the committed dataset filter and the detected schema (column names, roles
    and counts - never cell values).
    """
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await ensure_owned_log(session, log_id, p.user.id)
            return cap(_detail_dict(row))

    return await guarded(p, "get_process", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_activities(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Unique activities + per-activity event count, ordered by frequency.

    Case-centric, ready logs only ([conflict] otherwise). Counts respect the
    committed dataset filter.
    """
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            counts = await svc.activity_counts(row, p.user.id)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return cap(
            {
                "items": [{"activity": a, "count": n} for a, n in counts],
                "total": len(counts),
            }
        )

    return await guarded(p, "get_activities", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_activity(ctx: MCPContext, log_id: str, name: str) -> dict[str, Any]:
    """One activity in depth: event/case counts + shares, occurrences per case,
    start/end-of-case role, first/last seen, and the variants containing it
    (exact membership). ``name`` is the raw activity name."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            detail = await svc.activity_detail(row, p.user.id, name)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return cap(detail.model_dump(mode="json"))

    return await guarded(p, "get_activity", {"log_id": log_id, "name": name}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_variants(
    ctx: MCPContext,
    log_id: str,
    sort: str = "case_count",
    activity_contains: str | None = None,
    min_case_count: int | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Aggregate process variants: activity sequence, case count/pct, durations.

    ``sort``: case_count | avg_duration_seconds | first_seen | last_seen,
    optionally suffixed ``:asc``/``:desc`` (default desc). Filter with
    ``activity_contains`` / ``min_case_count``; paginate via ``cursor``/``limit``.
    """
    p = await authz(ctx, SCOPE_PROCESSES_READ)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            rows, total = await svc.variant_rows(
                log_id,
                p.user.id,
                offset=offset,
                limit=size,
                sort=sort,
                activity_contains=activity_contains,
                min_case_count=min_case_count,
                total_cases=int(row.cases_count or 0),
                active_filter=row.active_filter,
            )
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        items = [r.model_dump(mode="json") for r in rows]
        return cap(page_envelope(items, offset=offset, limit=size, total=total))

    return await guarded(p, "get_variants", {"log_id": log_id, "sort": sort}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_variant(ctx: MCPContext, log_id: str, variant_id: str) -> dict[str, Any]:
    """One variant in depth: rank, sequence, counts, avg/median/p90 durations,
    duration histogram and top-5 attribute breakdowns (aggregates only)."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            detail = await svc.variant_detail(row, p.user.id, variant_id)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return cap(detail.model_dump(mode="json"))

    return await guarded(p, "get_variant", {"log_id": log_id, "variant_id": variant_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_variant_cases(
    ctx: MCPContext,
    log_id: str,
    variant_id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Case-level metadata rows for one variant: case_id, start/end, duration
    seconds, event count. No event rows - case aggregates only."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ready_case_centric(session, log_id, p.user.id)
        try:
            page = await svc.variant_cases_page(
                log_id, p.user.id, variant_id, offset=offset, limit=size
            )
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        items = [r.model_dump(mode="json") for r in page.rows]
        return cap(page_envelope(items, offset=offset, limit=size, total=page.total))

    return await guarded(
        p, "get_variant_cases", {"log_id": log_id, "variant_id": variant_id}, _impl()
    )


@mcp_tool(toolset="processes", idempotent=True)
async def get_data_quality(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Per-column completeness metrics: null count/pct + distinct count."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            report = await svc.data_quality_report(row, p.user.id)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return cap(report.model_dump(mode="json"))

    return await guarded(p, "get_data_quality", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_time_bounds(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Earliest/latest timestamp in the log: {field, min_ts, max_ts}."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            bounds = await svc.time_bounds(row, p.user.id)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return bounds.model_dump(mode="json")

    return await guarded(p, "get_time_bounds", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_column_schema(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """The events table's column list: field, role (case_id/activity/timestamp/
    .../custom), label, required, type. Schema only - no cell values."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
        try:
            specs = await svc.column_specs_for(row, p.user.id)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        # Deliberately excludes ColumnSpec.enum_values - those are cell values.
        return cap(
            {
                "columns": [
                    {
                        "field": s.name,
                        "role": s.role,
                        "label": s.label,
                        "required": s.required,
                        "type": s.type,
                    }
                    for s in specs
                ]
            }
        )

    return await guarded(p, "get_column_schema", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_ocel_overview(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Object-centric (OCEL) log overview: event/object/relation counts, object
    types and activity names. OCEL logs only ([conflict] for case-centric)."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_ocel(session, log_id, p.user.id)
        return cap(svc.ocel_overview_payload(row).model_dump(mode="json"))

    return await guarded(p, "get_ocel_overview", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def get_ocel_object_types(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Object type → object count for an OCEL log ([conflict] for case-centric)."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ready_ocel(session, log_id, p.user.id)
        try:
            entries = await svc.ocel_object_type_counts(log_id, p.user.id)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return cap({"items": [e.model_dump(mode="json") for e in entries]})

    return await guarded(p, "get_ocel_object_types", {"log_id": log_id}, _impl())


@mcp_tool(toolset="processes", idempotent=True)
async def list_folders(ctx: MCPContext) -> dict[str, Any]:
    """List your process folders (id, name, parent_id, position)."""
    p = await authz(ctx, SCOPE_PROCESSES_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(Folder)
                        .where(Folder.user_id == p.user.id, Folder.deleted_at.is_(None))
                        .order_by(Folder.position.asc(), Folder.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            items = [_folder_row(f) for f in rows]
        return cap({"items": items, "total": len(items)})

    return await guarded(p, "list_folders", {}, _impl())


# ── write tools ──────────────────────────────────────────────────────────────


@mcp_tool(toolset="processes", write=True)
async def import_process_from_url(
    ctx: MCPContext,
    url: str,
    name: str | None = None,
    csv_mapping: dict[str, Any] | None = None,
    xml_mapping: dict[str, Any] | None = None,
    json_mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import a process from a public http(s) URL (.xes/.xes.gz/.csv/.xml/.json/OCEL).

    Optional csv/xml/json mapping objects mirror the upload API's column
    mappings. Returns {log_id, job_id}; poll the job until the log is ready.
    """
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)
    scheme = urlparse(url).scheme.lower()
    if scheme not in {"http", "https"}:
        raise tool_error(CODE_INVALID, "Only http(s) URLs can be imported.")

    async def _impl() -> dict[str, Any]:
        runtime = _job_runtime()
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                log_id, job_id = await svc.import_log_from_url(
                    session,
                    runtime,
                    p.user.id,
                    url=url,
                    name=name,
                    csv_mapping=json.dumps(csv_mapping) if csv_mapping is not None else None,
                    xml_mapping=json.dumps(xml_mapping) if xml_mapping is not None else None,
                    json_mapping=json.dumps(json_mapping) if json_mapping is not None else None,
                )
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"log_id": log_id, "job_id": job_id}

    return await guarded(p, "import_process_from_url", {"url": url}, _impl(), mutation=True)


@mcp_tool(toolset="processes", write=True, idempotent=True)
async def update_process(
    ctx: MCPContext,
    log_id: str,
    name: str | None = None,
    description: str | None = None,
    folder_id: str | None = None,
    clear_folder: bool = False,
) -> dict[str, Any]:
    """Rename a process, set/clear its description, or move it between folders.

    ``description=""`` clears the description; ``clear_folder=true`` moves the
    log to the root (mutually exclusive with ``folder_id``).
    """
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)
    if folder_id is not None and clear_folder:
        raise tool_error(CODE_INVALID, "Pass either folder_id or clear_folder, not both.")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await ensure_owned_log(session, log_id, p.user.id)
            if name is not None:
                cleaned = name.strip()
                if not cleaned:
                    raise tool_error(CODE_INVALID, "Name cannot be empty.")
                if len(cleaned) > 255:
                    raise tool_error(CODE_INVALID, "Name is too long (max 255 characters).")
                row.name = cleaned
            if description is not None:
                # Empty string clears; any non-empty value is stored verbatim.
                row.description = description.strip() or None
            if clear_folder:
                row.folder_id = None
            elif folder_id is not None:
                await _owned_folder(session, folder_id, p.user.id)
                row.folder_id = folder_id
            await session.commit()
            return cap(_detail_dict(row))

    return await guarded(p, "update_process", {"log_id": log_id}, _impl(), mutation=True)


@mcp_tool(toolset="processes", write=True)
async def duplicate_process(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Clone a ready process (data + settings) into a new log next to the
    source. Returns the new log's summary."""
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await ensure_owned_log(session, log_id, p.user.id)
            try:
                dup = await svc.duplicate_log(session, row, p.user.id)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
            return _process_row(dup)

    return await guarded(p, "duplicate_process", {"log_id": log_id}, _impl(), mutation=True)


@mcp_tool(toolset="processes", write=True, destructive=True)
async def set_committed_filter(
    ctx: MCPContext,
    log_id: str,
    filter: list[dict[str, Any]],
    confirm: bool = False,
) -> dict[str, Any]:
    """Commit a dataset filter as the process's applied filter; EVERY module
    then re-runs its precompute against the filtered rows (that's why this is
    destructive). An empty list clears the filter.

    Entries are {field, op, value?}; ops: contains | equals | gte | lte |
    is_null | is_not_null | in. Without confirm=true returns a dry-run preview
    (current vs new filter) and changes nothing.
    """
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)
    # Re-validate the shape at runtime: direct (non-FastMCP) callers can pass
    # anything, and a str entry would otherwise surface as an internal error.
    raw: Any = filter
    if not isinstance(raw, list) or any(not isinstance(e, dict) for e in raw):
        raise tool_error(CODE_INVALID, "filter must be a list of {field, op, value?} objects.")
    entries: list[dict[str, Any]] = [dict(e) for e in raw]

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ready_case_centric(session, log_id, p.user.id)
            if not confirm:
                # Validate in the preview path too, so a bad filter errors
                # instead of previewing.
                try:
                    await svc.validate_committed_filter(log_id, p.user.id, entries)
                except HTTPException as exc:
                    raise from_http_exception(exc) from exc
                return confirm_preview(
                    "set_committed_filter",
                    {
                        "log_id": log_id,
                        "name": row.name,
                        "current_filter": row.active_filter or [],
                        "new_filter": entries,
                        "warning": ("Committing re-runs every module's precompute for this log."),
                    },
                )
            try:
                retriggered = await svc.commit_active_filter(session, row, p.user.id, entries)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"active_filter": entries, "modules_retriggered": retriggered}

    return await guarded(
        p, "set_committed_filter", {"log_id": log_id, "confirm": confirm}, _impl(), mutation=True
    )


@mcp_tool(toolset="processes", write=True, destructive=True)
async def remap_columns(
    ctx: MCPContext,
    log_id: str,
    case_id: str,
    activity: str,
    timestamp: str,
    end_timestamp: str | None = None,
    resource: str | None = None,
    cost: str | None = None,
    role: str | None = None,
    lifecycle: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Force the column-role mapping (role → source column) and re-import the
    log from its retained original - all data + module results are rebuilt.

    case_id/activity/timestamp are required; the rest are optional roles.
    Without confirm=true returns a preview of current vs requested roles.
    Returns {log_id, job_id} once confirmed.
    """
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)
    try:
        body = RemapColumnRoles(
            case_id=case_id,
            activity=activity,
            timestamp=timestamp,
            end_timestamp=end_timestamp,
            resource=resource,
            cost=cost,
            role=role,
            lifecycle=lifecycle,
        )
    except ValidationError as exc:
        raise tool_error(CODE_INVALID, f"Invalid column roles: {exc}") from exc
    roles = body.as_roles()

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await ensure_owned_log(session, log_id, p.user.id)
            if not confirm:
                try:
                    svc.remap_preflight(row)
                    svc.validate_remap_roles(row, roles)
                except HTTPException as exc:
                    raise from_http_exception(exc) from exc
                return confirm_preview(
                    "remap_columns",
                    {
                        "log_id": log_id,
                        "name": row.name,
                        "current_column_roles": row.column_roles or {},
                        "requested_column_roles": roles,
                        "warning": (
                            "Re-imports the log from its original file; all module "
                            "results are recomputed."
                        ),
                    },
                )
            runtime = _job_runtime()
            try:
                job_id = await svc.remap_log(session, runtime, row, p.user.id, roles)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"log_id": log_id, "job_id": job_id}

    return await guarded(
        p, "remap_columns", {"log_id": log_id, "confirm": confirm}, _impl(), mutation=True
    )


@mcp_tool(toolset="processes", write=True, destructive=True)
async def reimport_process(ctx: MCPContext, log_id: str, confirm: bool = False) -> dict[str, Any]:
    """Re-run the import from the retained original upload - all data + module
    results are rebuilt. Without confirm=true returns a preview of the source
    file. Returns {log_id, job_id} once confirmed."""
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await ensure_owned_log(session, log_id, p.user.id)
            if not confirm:
                try:
                    svc.reimport_preflight(row)
                except HTTPException as exc:
                    raise from_http_exception(exc) from exc
                return confirm_preview(
                    "reimport_process",
                    {
                        "log_id": log_id,
                        "name": row.name,
                        "source_filename": row.source_filename,
                        "source_format": row.source_format,
                        "warning": (
                            "Rebuilds the log from its original file; all module "
                            "results are recomputed."
                        ),
                    },
                )
            runtime = _job_runtime()
            try:
                job_id = await svc.reimport_log(session, runtime, row, p.user.id)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"log_id": log_id, "job_id": job_id}

    return await guarded(
        p, "reimport_process", {"log_id": log_id, "confirm": confirm}, _impl(), mutation=True
    )


@mcp_tool(toolset="processes", write=True, destructive=True)
async def delete_process(ctx: MCPContext, log_id: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a process (event log): cancels its jobs, soft-deletes the row and
    removes its on-disk data. Irreversible. Without confirm=true returns a
    preview (name + counts) and deletes nothing."""
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await ensure_owned_log(session, log_id, p.user.id)
            if not confirm:
                return confirm_preview(
                    "delete_process",
                    {
                        "log_id": log_id,
                        "name": row.name,
                        "events_count": row.events_count,
                        "cases_count": row.cases_count,
                    },
                )
            runtime = _job_runtime()
            try:
                await svc.delete_log_and_data(session, runtime, row, p.user.id)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"deleted": True, "log_id": log_id}

    return await guarded(
        p, "delete_process", {"log_id": log_id, "confirm": confirm}, _impl(), mutation=True
    )


@mcp_tool(toolset="processes", write=True)
async def create_folder(ctx: MCPContext, name: str, parent_id: str | None = None) -> dict[str, Any]:
    """Create a process folder (optionally nested under ``parent_id``)."""
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)
    cleaned = name.strip()
    if not cleaned:
        raise tool_error(CODE_INVALID, "Name cannot be empty.")
    if len(cleaned) > 255:
        raise tool_error(CODE_INVALID, "Name is too long (max 255 characters).")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            if parent_id is not None:
                await _owned_folder(session, parent_id, p.user.id)
            next_pos = await svc.next_folder_position(session, p.user.id, parent_id)
            folder = Folder(
                id=uuid7_str(),
                user_id=p.user.id,
                name=cleaned,
                parent_id=parent_id,
                position=next_pos,
                created_at=_utcnow(),
            )
            session.add(folder)
            await session.commit()
            return _folder_row(folder)

    return await guarded(p, "create_folder", {"name": cleaned}, _impl(), mutation=True)


@mcp_tool(toolset="processes", write=True, idempotent=True)
async def update_folder(
    ctx: MCPContext,
    folder_id: str,
    name: str | None = None,
    parent_id: str | None = None,
    clear_parent: bool = False,
    position: int | None = None,
) -> dict[str, Any]:
    """Rename, move (``parent_id`` / ``clear_parent``) or reposition a folder.

    ``clear_parent=true`` moves the folder to the root (mutually exclusive with
    ``parent_id``). Moving under a descendant is rejected.
    """
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)
    if parent_id is not None and clear_parent:
        raise tool_error(CODE_INVALID, "Pass either parent_id or clear_parent, not both.")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            folder = await _owned_folder(session, folder_id, p.user.id)
            if name is not None:
                cleaned = name.strip()
                if not cleaned:
                    raise tool_error(CODE_INVALID, "Name cannot be empty.")
                if len(cleaned) > 255:
                    raise tool_error(CODE_INVALID, "Name is too long (max 255).")
                folder.name = cleaned
            if clear_parent:
                folder.parent_id = None
            elif parent_id is not None:
                await _owned_folder(session, parent_id, p.user.id)
                try:
                    await svc.ensure_no_folder_cycle(session, folder_id, parent_id, p.user.id)
                except HTTPException as exc:
                    raise from_http_exception(exc) from exc
                folder.parent_id = parent_id
            if position is not None:
                folder.position = position
            await session.commit()
            return _folder_row(folder)

    return await guarded(p, "update_folder", {"folder_id": folder_id}, _impl(), mutation=True)


@mcp_tool(toolset="processes", write=True, destructive=True)
async def delete_folder(ctx: MCPContext, folder_id: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a folder AND everything inside it: child folders and the event
    logs they contain (cascade, irreversible). Without confirm=true returns a
    preview listing what would be deleted and changes nothing."""
    p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            folder = await _owned_folder(session, folder_id, p.user.id)
            if not confirm:
                folder_ids, log_rows = await svc.folder_cascade_contents(
                    session, p.user.id, folder_id
                )
                return confirm_preview(
                    "delete_folder",
                    {
                        "folder_id": folder_id,
                        "name": folder.name,
                        "folders_deleted": len(folder_ids),
                        "logs_deleted": len(log_rows),
                        "log_names": [r.name for r in log_rows[:20]],
                        "log_names_truncated": len(log_rows) > 20,
                    },
                )
            runtime = _job_runtime()
            try:
                folders_deleted, logs_deleted = await svc.cascade_delete_folder(
                    session, runtime, p.user.id, folder_id
                )
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {
            "deleted": True,
            "folder_id": folder_id,
            "folders_deleted": folders_deleted,
            "logs_deleted": logs_deleted,
        }

    return await guarded(
        p, "delete_folder", {"folder_id": folder_id, "confirm": confirm}, _impl(), mutation=True
    )


# ── resources ────────────────────────────────────────────────────────────────


@mcp_resource("mate://processes", toolset="processes")
async def processes_resource() -> str:
    """The caller's processes as a JSON resource. Auth is enforced by the
    transport middleware; the principal rides the request context."""
    import json

    from mate.api.mcp.server import mcp

    ctx = mcp.get_context()
    p = await authz(ctx, SCOPE_PROCESSES_READ)  # type: ignore[arg-type]
    envelope = await guarded(
        p,
        "resource:processes",
        {},
        _list_processes(p.user.id, offset=0, limit=200, status=None, q=None),
    )
    return json.dumps(cap(envelope), default=str)
