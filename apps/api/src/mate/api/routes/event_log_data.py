"""Read + edit endpoints behind the Events / Variants / Settings tabs.

All paths are mounted under `/api/v1/event-logs/{log_id}` alongside the
existing CRUD routes. The hot-path queries push as much work as possible
into DuckDB so we don't materialise the whole log into Python.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select

from mate.api.auth import CurrentUserDep
from mate.api.db.models import EventEdit, EventLog
from mate.api.db.session import SessionDep
from mate.api.modules.event_editing import apply_bulk_fill, apply_cell_edit
from mate.api.modules.event_filters import (
    FILTER_OPS,
    build_filter_where,
)
from mate.api.modules.event_log_access import EventLogAccess, _quote_ident
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.event_log_data import (
    ActiveFilterResult,
    ActiveFilterUpdate,
    ActivitiesPage,
    ActivityCasesPage,
    ActivityDetail,
    ActivityRow,
    BulkFillBody,
    BulkFillResult,
    CellPatch,
    CellPatchResult,
    ColumnValueEntry,
    ColumnValuesPage,
    DataQuality,
    EventEditEntry,
    EventEditsPage,
    EventsHeader,
    EventsPage,
    TimeBounds,
    VariantCasesPage,
    VariantDetail,
    VariantsPage,
)

# The aggregate/lifecycle bodies behind variants / activities / data-quality /
# time-bounds / active-filter live in the service layer so the MCP toolset can
# reuse them; these routes are thin adapters over it.
from mate.api.services import log_aggregates
from mate.api.services.log_aggregates import require_ready_case_centric as _require_ready

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/event-logs/{log_id}", tags=["event-logs"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_filter_param(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid filter JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=422, detail="filter must be a JSON array.")
    out: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=422, detail="filter entries must be objects.")
        op = entry.get("op")
        if op not in FILTER_OPS:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported filter op {op!r}; allowed: {sorted(FILTER_OPS)}",
            )
        if not isinstance(entry.get("field"), str):
            raise HTTPException(status_code=422, detail="filter.field must be a string.")
        out.append(entry)
    return out


def _build_where(
    filters: list[dict[str, Any]],
    column_names: set[str],
    q: str | None,
    missing_only: bool,
    case_id: str | None,
    required_columns: list[str],
) -> tuple[str, list[Any]]:
    """Build a parameterised SQL `WHERE` clause for the events view.

    All identifiers are column names from the parquet schema - checked
    against `column_names` before being interpolated. Values always go
    through `?` parameter binding.
    """
    clauses, params = build_filter_where(filters, column_names)

    if q:
        like = f"%{q}%"
        string_cols = [c for c in column_names if c]  # all columns; cast at runtime
        ors = " OR ".join(f"CAST({_quote_ident(c)} AS VARCHAR) ILIKE ?" for c in string_cols)
        if ors:
            clauses.append(f"({ors})")
            params.extend([like] * len(string_cols))

    if missing_only and required_columns:
        ors = " OR ".join(f"{_quote_ident(c)} IS NULL" for c in required_columns)
        clauses.append(f"({ors})")

    if case_id is not None:
        clauses.append(f"{_quote_ident('case_id')} = ?")
        params.append(case_id)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _parse_sort(raw: str | None, column_names: set[str]) -> str:
    if not raw:
        return f"{_quote_ident('case_id')} ASC, {_quote_ident('timestamp')} ASC"
    parts: list[str] = []
    for token in raw.split(","):
        if ":" in token:
            col, direction = token.split(":", 1)
        else:
            col, direction = token, "asc"
        col = col.strip()
        direction = direction.strip().lower()
        if col not in column_names:
            raise HTTPException(status_code=422, detail=f"Unknown sort column: {col!r}.")
        if direction not in {"asc", "desc"}:
            raise HTTPException(
                status_code=422,
                detail=f"sort direction must be asc/desc, got {direction!r}.",
            )
        parts.append(f"{_quote_ident(col)} {direction.upper()}")
    return ", ".join(parts)


def _row_dict(values: tuple[Any, ...], columns: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col, val in zip(columns, values, strict=False):
        if val is None:
            out[col] = None
        elif isinstance(val, datetime):
            # Stored naive UTC (ingest normalizes) - attach the offset so the
            # client parses the true instant instead of local wall-clock.
            out[col] = utc_isoformat(val)
        elif isinstance(val, float) and math.isnan(val):
            out[col] = None
        else:
            out[col] = val
    return out


async def _events_header(access: EventLogAccess, log_row: EventLog) -> EventsHeader:
    return EventsHeader(
        events_count=int(log_row.events_count or 0),
        cases_count=int(log_row.cases_count or 0),
        variants_count=int(log_row.variants_count or 0),
        date_min=log_row.date_min,
        date_max=log_row.date_max,
    )


# ── events ───────────────────────────────────────────────────────────────────


@router.get("/events", response_model=EventsPage)
async def list_events(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    sort: Annotated[str | None, Query()] = None,
    filter: Annotated[str | None, Query(alias="filter")] = None,
    q: Annotated[str | None, Query()] = None,
    missing_only: Annotated[bool, Query()] = False,
    case_id: Annotated[str | None, Query()] = None,
) -> EventsPage:
    log_row = await _require_ready(log_id, session, user.id)
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None

    async with EventLogAccess(log_id, user.id) as access:
        specs = await access.column_specs(overrides)
        col_names = {s.name for s in specs}
        required = [s.name for s in specs if s.required]

        filters = _parse_filter_param(filter)
        where, where_params = _build_where(filters, col_names, q, missing_only, case_id, required)
        # events.parquet is written sorted by (case_id, timestamp) - both the
        # importer and the editor sort before writing - so the default order
        # needs no ORDER BY: DuckDB then preserves file order and pushes the
        # LIMIT into the Parquet scan instead of top-N-sorting the whole log
        # for every page. (File order is also what `row_index` edits address.)
        order_by = _parse_sort(sort, col_names) if sort else None
        order_clause = f" ORDER BY {order_by}" if order_by else ""

        if where:
            (total,) = (
                await access.duckdb_fetch(f"SELECT COUNT(*) FROM events{where}", where_params)
            )[0]
        elif log_row.events_count is not None:
            # Unfiltered total is maintained on the SQLite row (import + every
            # edit) - skip the per-request COUNT(*) over the Parquet file.
            total = int(log_row.events_count)
        else:
            (total,) = (await access.duckdb_fetch("SELECT COUNT(*) FROM events"))[0]

        cols, rows = await access.duckdb_fetch_with_columns(
            f"SELECT * FROM events{where}{order_clause} LIMIT ? OFFSET ?",
            [*where_params, limit, offset],
        )
        dicts = [_row_dict(r, cols) for r in rows]

        # Synthetic columns the table needs for highlighting / linking.
        for d in dicts:
            d["_has_missing"] = any(d.get(c) is None for c in required)

    return EventsPage(
        rows=dicts,
        total=int(total),
        offset=offset,
        limit=limit,
        columns=specs,
        header=await _events_header(access, log_row),
    )


_COLUMN_VALUES_LIMIT = 500


@router.get("/columns/{field}/values", response_model=ColumnValuesPage)
async def list_column_values(
    log_id: str,
    field: str,
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = _COLUMN_VALUES_LIMIT,
) -> ColumnValuesPage:
    """Distinct values + counts for one column - backs the filter checklist.

    Always reads the *raw* dataset (ignores any applied filter) so the user can
    pick from every value, not just the ones currently surviving the filter.
    """
    await _require_ready(log_id, session, user.id)
    async with EventLogAccess(log_id, user.id) as access:
        specs = await access.column_specs()
        if field not in {s.name for s in specs}:
            raise HTTPException(status_code=422, detail=f"Unknown column: {field!r}.")
        ident = _quote_ident(field)
        params: list[Any] = []
        search = ""
        if q:
            search = f" AND CAST({ident} AS VARCHAR) ILIKE ?"
            params.append(f"%{q}%")
        (total_distinct,) = (
            await access.duckdb_fetch(
                f"SELECT COUNT(DISTINCT {ident}) FROM events WHERE {ident} IS NOT NULL{search}",
                params,
            )
        )[0]
        rows = await access.duckdb_fetch(
            f"""
            SELECT CAST({ident} AS VARCHAR) AS v, COUNT(*) AS n
            FROM events
            WHERE {ident} IS NOT NULL{search}
            GROUP BY v
            ORDER BY n DESC, v ASC
            LIMIT ?
            """,
            [*params, limit],
        )

    values = [ColumnValueEntry(value=str(r[0]), count=int(r[1])) for r in rows]
    return ColumnValuesPage(
        field=field,
        values=values,
        total_distinct=int(total_distinct or 0),
        truncated=int(total_distinct or 0) > len(values),
    )


@router.get("/time-bounds", response_model=TimeBounds)
async def get_time_bounds(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
) -> TimeBounds:
    """Earliest/latest timestamp in the log - seeds the dashboard time-range
    slider. Reads the *raw* dataset (ignores any applied filter) so the slider
    spans the full window. The canonical ``timestamp`` column is preferred;
    if it's absent or non-temporal the bounds come back ``null``.
    """
    log_row = await _require_ready(log_id, session, user.id)
    return await log_aggregates.time_bounds(log_row, user.id)


@router.put("/active-filter", response_model=ActiveFilterResult)
async def put_active_filter(
    log_id: str,
    payload: ActiveFilterUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> ActiveFilterResult:
    """Commit the Events-tab filter as the *applied* dataset filter.

    Persists it on the log, then re-publishes ``log.imported`` so every
    installed module re-runs its import/processing against the now-filtered
    data (modules subscribe to that topic). An empty ``filter`` clears the
    overlay - back to the full dataset - and likewise re-runs modules.
    """
    log_row = await _require_ready(log_id, session, user.id)
    entries = [e.model_dump() for e in payload.filter]
    retriggered = await log_aggregates.commit_active_filter(session, log_row, user.id, entries)
    return ActiveFilterResult(
        active_filter=payload.filter,
        modules_retriggered=retriggered,
    )


@router.patch("/events/{row_index}", response_model=CellPatchResult)
async def patch_event(
    log_id: str,
    row_index: int,
    payload: CellPatch,
    session: SessionDep,
    user: CurrentUserDep,
) -> CellPatchResult:
    log_row = await _require_ready(log_id, session, user.id)
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None

    async with EventLogAccess(log_id, user.id) as access:
        specs = await access.column_specs(overrides)

    try:
        outcome = await apply_cell_edit(
            log_id, row_index, payload.field, payload.value, specs, session, user.id
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CellPatchResult(
        row=outcome.row,
        row_index=outcome.old_row_index,
        new_row_index=outcome.new_row_index,
        header=outcome.header,
    )


@router.post("/events/bulk-fill", response_model=BulkFillResult)
async def bulk_fill_events(
    log_id: str,
    payload: BulkFillBody,
    session: SessionDep,
    user: CurrentUserDep,
) -> BulkFillResult:
    log_row = await _require_ready(log_id, session, user.id)
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None

    async with EventLogAccess(log_id, user.id) as access:
        specs = await access.column_specs(overrides)

    try:
        outcome = await apply_bulk_fill(
            log_id, payload.row_indices, payload.field, payload.value, specs, session, user.id
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return BulkFillResult(updated=outcome.updated, header=outcome.header)


# ── variants ─────────────────────────────────────────────────────────────────


@router.get("/variants", response_model=VariantsPage)
async def list_variants(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    sort: Annotated[str, Query()] = "case_count:desc",
    activity_contains: Annotated[str | None, Query()] = None,
    min_case_count: Annotated[int | None, Query(ge=1)] = None,
) -> VariantsPage:
    log_row = await _require_ready(log_id, session, user.id)
    rows, total = await log_aggregates.variant_rows(
        log_id,
        user.id,
        offset=offset,
        limit=limit,
        sort=sort,
        activity_contains=activity_contains,
        min_case_count=min_case_count,
        total_cases=int(log_row.cases_count or 0),
        active_filter=log_row.active_filter,
    )
    return VariantsPage(rows=rows, total=total, offset=offset, limit=limit)


@router.get("/variants/{variant_id}", response_model=VariantDetail)
async def get_variant(
    log_id: str,
    variant_id: str,
    session: SessionDep,
    user: CurrentUserDep,
) -> VariantDetail:
    log_row = await _require_ready(log_id, session, user.id)
    return await log_aggregates.variant_detail(log_row, user.id, variant_id)


@router.get("/variants/{variant_id}/cases", response_model=VariantCasesPage)
async def list_variant_cases(
    log_id: str,
    variant_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> VariantCasesPage:
    await _require_ready(log_id, session, user.id)
    return await log_aggregates.variant_cases_page(
        log_id, user.id, variant_id, offset=offset, limit=limit
    )


# ── data quality + edits ─────────────────────────────────────────────────────


@router.get("/data-quality", response_model=DataQuality)
async def get_data_quality(log_id: str, session: SessionDep, user: CurrentUserDep) -> DataQuality:
    log_row = await _require_ready(log_id, session, user.id)
    return await log_aggregates.data_quality_report(log_row, user.id)


@router.get("/edits", response_model=EventEditsPage)
async def list_edits(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventEditsPage:
    await _require_ready(log_id, session, user.id)
    total = (
        await session.execute(
            select(func.count())
            .select_from(EventEdit)
            .where(EventEdit.user_id == user.id, EventEdit.log_id == log_id)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(EventEdit)
                .where(EventEdit.user_id == user.id, EventEdit.log_id == log_id)
                .order_by(desc(EventEdit.edited_at))
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return EventEditsPage(
        rows=[EventEditEntry.model_validate(r) for r in rows],
        total=int(total),
        offset=offset,
        limit=limit,
    )


# ── activities ───────────────────────────────────────────────────────────────


@router.get("/activities", response_model=ActivitiesPage)
async def list_activities(log_id: str, session: SessionDep, user: CurrentUserDep) -> ActivitiesPage:
    """Unique activities + per-activity event count, ordered by frequency.

    The display-name overrides users set in the Activities tab live in
    `EventLog.column_overrides.activity_labels` and are applied client-side;
    this endpoint always returns raw activity names so analytics modules
    keep operating on the canonical values.
    """
    log_row = await _require_ready(log_id, session, user.id)
    counts = await log_aggregates.activity_counts(log_row, user.id)
    return ActivitiesPage(
        rows=[ActivityRow(activity=a, count=n) for a, n in counts],
        total=len(counts),
    )


# The activity name travels as a query param, not a path segment: names are
# arbitrary strings (may contain `/`, `%`, unicode) and the prod proxy chain
# can normalize percent-escapes inside path segments; query strings pass
# through untouched.


@router.get("/activities/detail", response_model=ActivityDetail)
async def get_activity_detail(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    name: Annotated[str, Query(min_length=1)],
) -> ActivityDetail:
    log_row = await _require_ready(log_id, session, user.id)
    return await log_aggregates.activity_detail(log_row, user.id, name)


@router.get("/activities/cases", response_model=ActivityCasesPage)
async def list_activity_cases(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    name: Annotated[str, Query(min_length=1)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ActivityCasesPage:
    await _require_ready(log_id, session, user.id)
    return await log_aggregates.activity_cases_page(
        log_id, user.id, name, offset=offset, limit=limit
    )
