"""Read + edit endpoints behind the Events / Variants / Settings tabs.

All paths are mounted under `/api/v1/event-logs/{log_id}` alongside the
existing CRUD routes. The hot-path queries push as much work as possible
into DuckDB so we don't materialise the whole log into Python.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.models import EventEdit, EventLog
from mate.api.db.session import SessionDep
from mate.api.events import get_event_bus
from mate.api.modules.event_editing import apply_bulk_fill, apply_cell_edit
from mate.api.modules.event_filters import (
    FILTER_OPS,
    build_filter_where,
    validate_filters,
)
from mate.api.modules.event_log_access import EventLogAccess, _quote_ident
from mate.api.schemas.event_log_data import (
    ActiveFilterResult,
    ActiveFilterUpdate,
    ActivitiesPage,
    ActivityRow,
    AttributeBreakdown,
    AttributeBreakdownEntry,
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
    VariantCase,
    VariantCasesPage,
    VariantDetail,
    VariantRow,
    VariantsPage,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/event-logs/{log_id}", tags=["event-logs"])

_VARIANT_SORTS = {"case_count", "avg_duration_seconds", "last_seen", "first_seen"}


# ── helpers ──────────────────────────────────────────────────────────────────


async def _require_ready(log_id: str, session: SessionDep, user_id: str) -> EventLog:
    row = await get_owned_event_log(session, log_id, user_id)
    if row.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Event log is {row.status!r}; data endpoints require status=ready.",
        )
    # Object-centric (OCEL) logs have no case_id / variants / activities - they
    # are served exclusively by the /ocel/* endpoints. Every case-centric data
    # endpoint funnels through here, so this one guard isolates them all.
    if row.log_model == "object_centric":
        raise HTTPException(
            status_code=409,
            detail=(
                "This is an object-centric (OCEL) log; use the /ocel/* endpoints. "
                "Case-centric endpoints (events/variants/activities/data-quality) "
                "do not apply."
            ),
        )
    return row


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


def _row_dict(values: tuple, columns: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col, val in zip(columns, values, strict=False):
        if val is None:
            out[col] = None
        elif isinstance(val, datetime):
            out[col] = val.isoformat()
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
        order_by = _parse_sort(sort, col_names)

        (total,) = (await access.duckdb_fetch(f"SELECT COUNT(*) FROM events{where}", where_params))[
            0
        ]

        cols, rows = await access.duckdb_fetch_with_columns(
            f"SELECT * FROM events{where} ORDER BY {order_by} LIMIT ? OFFSET ?",
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


def _iso(value: Any) -> str | None:
    """Render a DuckDB scalar (datetime or already-stringy) as ISO text."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


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
    await _require_ready(log_id, session, user.id)
    async with EventLogAccess(log_id, user.id) as access:
        specs = await access.column_specs()
        ts_spec = next(
            (s for s in specs if s.role == "timestamp"),
            next((s for s in specs if s.name == "timestamp"), None),
        )
        if ts_spec is None:
            return TimeBounds()
        ident = _quote_ident(ts_spec.name)
        rows = await access.duckdb_fetch(
            f"SELECT MIN({ident}), MAX({ident}) FROM events WHERE {ident} IS NOT NULL"
        )
    lo, hi = rows[0] if rows else (None, None)
    return TimeBounds(field=ts_spec.name, min_ts=_iso(lo), max_ts=_iso(hi))


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
    async with EventLogAccess(log_id, user.id) as access:
        specs = await access.column_specs()
        validate_filters(entries, {s.name for s in specs})

    log_row.active_filter = entries or None
    log_row.last_edited_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()

    # Re-publish the import-completed signal so modules reprocess the filtered
    # dataset - mirrors the payload the import pipeline emits (ingest/dispatch.py)
    # plus a `reapplied` marker so handlers can distinguish a refilter from a
    # first import if they care.
    retriggered = False
    try:
        bus = get_event_bus()
        await bus.publish(
            "log.imported",
            {
                "log_id": log_id,
                "user_id": user.id,
                "events_count": int(log_row.events_count or 0),
                "cases_count": int(log_row.cases_count or 0),
                "detected_schema": log_row.detected_schema,
                "fixed_columns": [],
                "reapplied": True,
            },
        )
        retriggered = True
    except Exception:
        log.exception("active_filter.republish_failed", log_id=log_id)

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
    rows, total = await _variant_rows(
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


async def _variant_rows(
    log_id: str,
    user_id: str,
    *,
    offset: int,
    limit: int,
    sort: str,
    activity_contains: str | None,
    min_case_count: int | None,
    total_cases: int,
    active_filter: list[dict[str, Any]] | None = None,
) -> tuple[list[VariantRow], int]:
    sort_col, _, direction = sort.partition(":")
    direction = (direction or "desc").lower()
    if sort_col not in _VARIANT_SORTS:
        raise HTTPException(status_code=422, detail=f"Unknown variants sort: {sort_col!r}.")
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="sort direction must be asc/desc.")

    extra_where: list[str] = []
    extra_params: list[Any] = []
    if min_case_count is not None:
        extra_where.append("case_count >= ?")
        extra_params.append(min_case_count)

    activity_filter = ""
    if activity_contains:
        activity_filter = "HAVING string_agg(activity, '→' ORDER BY timestamp) ILIKE ?"
        extra_params.append(f"%{activity_contains}%")

    sql = f"""
        WITH per_case AS (
            SELECT
                case_id,
                MIN(timestamp) AS case_start,
                MAX(timestamp) AS case_end,
                EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) AS duration_s,
                string_agg(activity, '→' ORDER BY timestamp) AS activities_str
            FROM events
            GROUP BY case_id
            {activity_filter}
        ),
        per_variant AS (
            SELECT
                activities_str,
                COUNT(*) AS case_count,
                AVG(duration_s) AS avg_duration_seconds,
                MEDIAN(duration_s) AS median_duration_seconds,
                MIN(case_start) AS first_seen,
                MAX(case_end) AS last_seen
            FROM per_case
            GROUP BY activities_str
        )
        SELECT * FROM per_variant
        {("WHERE " + " AND ".join(extra_where)) if extra_where else ""}
        ORDER BY {sort_col} {direction.upper()}
    """
    rows = await _duckdb_fetch_all(log_id, user_id, sql, extra_params, active_filter)
    total = len(rows)
    page = rows[offset : offset + limit]

    from mate.api.ingest.aggregation import variant_id_for

    out: list[VariantRow] = []
    for i, r in enumerate(page, start=offset + 1):
        activities_str, case_count, avg_d, med_d, first_seen, last_seen = r
        activities = activities_str.split("→") if activities_str else []
        case_pct = (float(case_count) / total_cases) if total_cases else 0.0
        out.append(
            VariantRow(
                rank=i,
                variant_id=variant_id_for(tuple(activities)),
                activities=activities,
                case_count=int(case_count),
                case_pct=case_pct,
                avg_duration_seconds=float(avg_d) if avg_d is not None else None,
                median_duration_seconds=float(med_d) if med_d is not None else None,
                first_seen=first_seen,
                last_seen=last_seen,
            )
        )
    return out, total


async def _duckdb_fetch_all(
    log_id: str,
    user_id: str,
    sql: str,
    params: list[Any],
    active_filter: list[dict[str, Any]] | None = None,
) -> list[tuple]:
    async with EventLogAccess(log_id, user_id, active_filter) as access:
        return await access.duckdb_fetch(sql, params)


@router.get("/variants/{variant_id}", response_model=VariantDetail)
async def get_variant(
    log_id: str,
    variant_id: str,
    session: SessionDep,
    user: CurrentUserDep,
) -> VariantDetail:
    log_row = await _require_ready(log_id, session, user.id)
    total_cases = int(log_row.cases_count or 0)

    # Pull all variants ordered by case_count desc to determine rank + activities.
    rows, _ = await _variant_rows(
        log_id,
        user.id,
        offset=0,
        limit=10**9,
        sort="case_count:desc",
        activity_contains=None,
        min_case_count=None,
        total_cases=total_cases,
        active_filter=log_row.active_filter,
    )
    target = next((v for v in rows if v.variant_id == variant_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Variant not found.")

    # Histogram + p90 + per-attribute breakdowns from the same DuckDB conn.
    async with EventLogAccess(log_id, user.id, log_row.active_filter) as access:
        # Compute case durations for this variant.
        sep = "→"
        durations_sql = f"""
            WITH per_case AS (
                SELECT case_id,
                       string_agg(activity, '{sep}' ORDER BY timestamp) AS activities_str,
                       EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) AS duration_s
                FROM events
                GROUP BY case_id
            )
            SELECT duration_s FROM per_case WHERE activities_str = ?
        """
        d_rows = await access.duckdb_fetch(durations_sql, [sep.join(target.activities)])
        durations = [float(r[0]) for r in d_rows if r[0] is not None]
        durations.sort()
        p90 = durations[int(len(durations) * 0.9)] if durations else None
        bins, edges = _histogram(durations)

        breakdowns = await _attribute_breakdowns(access, target.activities, log_row)

    return VariantDetail(
        rank=target.rank,
        variant_id=target.variant_id,
        activities=target.activities,
        case_count=target.case_count,
        case_pct=target.case_pct,
        avg_duration_seconds=target.avg_duration_seconds,
        median_duration_seconds=target.median_duration_seconds,
        p90_duration_seconds=p90,
        first_seen=target.first_seen,
        last_seen=target.last_seen,
        duration_histogram=bins,
        duration_bin_edges_seconds=edges,
        attribute_breakdowns=breakdowns,
    )


async def _attribute_breakdowns(
    access: EventLogAccess,
    activities: list[str],
    log_row: EventLog,
) -> list[AttributeBreakdown]:
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None
    specs = await access.column_specs(overrides)
    skip = {"case_id", "activity", "timestamp", "end_timestamp"}
    out: list[AttributeBreakdown] = []
    sep = "→"
    activities_str = sep.join(activities)
    for spec in specs:
        if spec.name in skip:
            continue
        ident = _quote_ident(spec.name)
        sql = f"""
            WITH per_case AS (
                SELECT case_id,
                       string_agg(activity, '{sep}' ORDER BY timestamp) AS activities_str
                FROM events
                GROUP BY case_id
            ),
            target_cases AS (
                SELECT case_id FROM per_case WHERE activities_str = ?
            )
            SELECT {ident} AS value, COUNT(*) AS n
            FROM events
            WHERE case_id IN (SELECT case_id FROM target_cases)
            GROUP BY {ident}
            ORDER BY n DESC
            LIMIT 5
        """
        rows = await access.duckdb_fetch(sql, [activities_str])
        out.append(
            AttributeBreakdown(
                column=spec.name,
                label=spec.label,
                top=[AttributeBreakdownEntry(value=r[0], count=int(r[1])) for r in rows],
            )
        )
    return out


def _histogram(values: list[float], bins: int = 12) -> tuple[list[int], list[float]]:
    if not values:
        return [], []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [len(values)], [lo, hi]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        i = min(int((v - lo) / width), bins - 1)
        counts[i] += 1
    edges = [lo + i * width for i in range(bins + 1)]
    return counts, edges


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
    async with EventLogAccess(log_id, user.id) as access:
        # Rebuild the variant's activity sequence on the fly so we can match cases.
        # The variant_id alone isn't enough since it's a hash - but we have the
        # cases.parquet which already stores variant_id per case (computed at
        # import / on every edit), so we join through that.
        if not access._paths.cases.exists():  # type: ignore[attr-defined]
            return VariantCasesPage(rows=[], total=0, offset=offset, limit=limit)

        (total,) = (
            await access.duckdb_fetch(
                "SELECT COUNT(*) FROM cases WHERE variant_id = ?",
                [variant_id],
            )
        )[0]
        rows = await access.duckdb_fetch(
            """
            SELECT case_id, case_start, case_end, case_duration_seconds, event_count
            FROM cases
            WHERE variant_id = ?
            ORDER BY case_start DESC NULLS LAST
            LIMIT ? OFFSET ?
            """,
            [variant_id, limit, offset],
        )

    return VariantCasesPage(
        rows=[
            VariantCase(
                case_id=str(r[0]),
                case_start=r[1],
                case_end=r[2],
                case_duration_seconds=float(r[3]) if r[3] is not None else None,
                event_count=int(r[4]),
            )
            for r in rows
        ],
        total=int(total),
        offset=offset,
        limit=limit,
    )


# ── data quality + edits ─────────────────────────────────────────────────────


@router.get("/data-quality", response_model=DataQuality)
async def get_data_quality(log_id: str, session: SessionDep, user: CurrentUserDep) -> DataQuality:
    log_row = await _require_ready(log_id, session, user.id)
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None
    async with EventLogAccess(log_id, user.id, log_row.active_filter) as access:
        specs = await access.column_specs(overrides)
        return await access.data_quality(specs)


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
    async with EventLogAccess(log_id, user.id, log_row.active_filter) as access:
        rows = await access.duckdb_fetch(
            "SELECT activity, COUNT(*) AS n FROM events GROUP BY activity ORDER BY n DESC, activity ASC"
        )
    return ActivitiesPage(
        rows=[ActivityRow(activity=str(r[0]), count=int(r[1])) for r in rows],
        total=len(rows),
    )
