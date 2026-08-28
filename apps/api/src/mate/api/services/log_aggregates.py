"""Aggregate + lifecycle logic for event logs and folders, shared by routes and MCP.

Extracted verbatim from ``routes/event_log_data.py``, ``routes/ocel_data.py``,
``routes/event_logs.py`` and ``routes/folders.py`` so the MCP "processes"
toolset can reuse the exact route semantics without going through HTTP.
Functions raise ``fastapi.HTTPException`` with the same status codes the routes
raised inline: routes let them propagate unchanged; MCP tools translate them
with ``mate.api.mcp.errors.from_http_exception``.
"""

from __future__ import annotations

import asyncio
import collections
import json
import shutil
import threading
from datetime import UTC, datetime
from typing import Any, NamedTuple
from urllib.parse import unquote, urlparse

import aiofiles
import httpx
import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import get_owned_event_log
from mate.api.config import get_settings
from mate.api.db.models import EventLog, Folder
from mate.api.events import get_event_bus
from mate.api.ingest.detect import detect_format, original_extension, sniff_format
from mate.api.ingest.dispatch import IMPORT_JOB_TYPE
from mate.api.ingest.storage import log_paths
from mate.api.jobs.runtime import JobRuntime
from mate.api.modules.event_filters import quote_ident as _quote_ident
from mate.api.modules.event_filters import validate_filters
from mate.api.modules.event_log_access import EventLogAccess, file_identity
from mate.api.modules.object_centric_log_access import ObjectCentricLogAccess
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.event_log_data import (
    ActivityCase,
    ActivityCasesPage,
    ActivityDetail,
    AttributeBreakdown,
    AttributeBreakdownEntry,
    ColumnSpec,
    DataQuality,
    TimeBounds,
    VariantCase,
    VariantCasesPage,
    VariantDetail,
    VariantRow,
)
from mate.api.schemas.event_logs import (
    CsvColumnMapping,
    JsonColumnMapping,
    XmlColumnMapping,
)
from mate.api.schemas.ocel_data import OcelObjectTypeEntry, OcelOverview
from mate.api.storage import sync as storage_sync
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

VARIANT_SORTS = {"case_count", "avg_duration_seconds", "last_seen", "first_seen"}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── readiness gates ──────────────────────────────────────────────────────────


async def require_ready_case_centric(log_id: str, session: AsyncSession, user_id: str) -> EventLog:
    """Ownership + ``status == ready`` + case-centric gate for data reads."""
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


async def require_ready_ocel(log_id: str, session: AsyncSession, user_id: str) -> EventLog:
    """Ownership + ``status == ready`` + object-centric gate for OCEL reads."""
    row = await get_owned_event_log(session, log_id, user_id)
    if row.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Event log is {row.status!r}; OCEL endpoints require status=ready.",
        )
    if row.log_model != "object_centric":
        raise HTTPException(
            status_code=409,
            detail=(
                "This is a case-centric log; the /ocel/* endpoints apply only to "
                "object-centric (OCEL) logs."
            ),
        )
    return row


# ── simple aggregates ────────────────────────────────────────────────────────


def _iso(value: Any) -> str | None:
    """Render a DuckDB scalar (datetime or already-stringy) as ISO text."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return utc_isoformat(value)
    return str(value)


async def time_bounds(log_row: EventLog, user_id: str) -> TimeBounds:
    """Earliest/latest timestamp in the log (raw dataset, ignores any filter)."""
    # `date_min`/`date_max` are maintained on the SQLite row for the canonical
    # `timestamp` column (set at import, refreshed on every edit) - serve them
    # without a per-request MIN/MAX scan. Logs without the stats fall through.
    if log_row.date_min is not None and log_row.date_max is not None:
        return TimeBounds(
            field="timestamp",
            min_ts=_iso(log_row.date_min),
            max_ts=_iso(log_row.date_max),
        )
    async with EventLogAccess(log_row.id, user_id) as access:
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


async def activity_counts(log_row: EventLog, user_id: str) -> list[tuple[str, int]]:
    """Unique activities + per-activity event count, ordered by frequency.

    Always returns raw activity names (display renames are client-side) so
    analytics keep operating on the canonical values.
    """
    async with EventLogAccess(log_row.id, user_id, log_row.active_filter) as access:
        rows = await access.duckdb_fetch(
            "SELECT activity, COUNT(*) AS n FROM events GROUP BY activity ORDER BY n DESC, activity ASC"
        )
    return [(str(r[0]), int(r[1])) for r in rows]


async def data_quality_report(log_row: EventLog, user_id: str) -> DataQuality:
    """Per-column null + distinct counts (through the applied filter overlay)."""
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None
    async with EventLogAccess(log_row.id, user_id, log_row.active_filter) as access:
        specs = await access.column_specs(overrides)
        return await access.data_quality(specs)


async def column_specs_for(log_row: EventLog, user_id: str) -> list[ColumnSpec]:
    """The events table's ``ColumnSpec`` list (schema-level; label overrides applied)."""
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None
    async with EventLogAccess(log_row.id, user_id) as access:
        return await access.column_specs(overrides)


# ── variants ─────────────────────────────────────────────────────────────────


class VariantEntry(NamedTuple):
    """One row of the cached per-variant aggregate table."""

    variant_id: str
    activities_str: str
    case_count: int
    avg_duration_seconds: float | None
    median_duration_seconds: float | None
    first_seen: datetime | None
    last_seen: datetime | None


# The variants aggregation scans + groups the whole log, but its result only
# changes when the Parquet file (or the applied filter) does. Cache the full
# table per file identity so page/sort changes and the variant-detail page
# reuse one computed table instead of re-aggregating per request. Rewrites go
# through tmp+os.replace, which changes the identity key. Pathologically
# variant-heavy logs are not pinned (row + byte caps) - they recompute as before.
_VARIANT_TABLE_CACHE: collections.OrderedDict[tuple[str, int, int, str], list[VariantEntry]] = (
    collections.OrderedDict()
)
_VARIANT_TABLE_CACHE_LOCK = threading.Lock()
_VARIANT_TABLE_CACHE_MAX = 8
_VARIANT_TABLE_CACHE_MAX_ROWS = 200_000
_VARIANT_TABLE_CACHE_MAX_BYTES = 32 * 1024 * 1024  # sum of activity-string bytes per entry

# Default rank order (case_count desc) is baked into the SQL with a
# deterministic tie-break, so pagination is stable across requests (the old
# per-request ORDER BY left ties nondeterministic) and the cached table needs
# no Python-side sort for the default view.
_VARIANTS_SQL = """
    WITH per_case AS (
        SELECT
            case_id,
            MIN(timestamp) AS case_start,
            MAX(timestamp) AS case_end,
            EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) AS duration_s,
            string_agg(activity, '→' ORDER BY timestamp) AS activities_str
        FROM events
        GROUP BY case_id
    )
    SELECT
        activities_str,
        COUNT(*) AS case_count,
        AVG(duration_s) AS avg_duration_seconds,
        MEDIAN(duration_s) AS median_duration_seconds,
        MIN(case_start) AS first_seen,
        MAX(case_end) AS last_seen
    FROM per_case
    GROUP BY activities_str
    ORDER BY case_count DESC, activities_str ASC
"""


async def variant_table(
    log_id: str,
    user_id: str,
    active_filter: list[dict[str, Any]] | None,
) -> list[VariantEntry]:
    """Full per-variant table for a log, sorted (case_count desc, activities asc)."""
    async with EventLogAccess(log_id, user_id, active_filter) as access:
        key: tuple[str, int, int, str] | None = None
        if get_settings().event_log_cache_entries > 0:
            identity = file_identity(access.events_path)
            if identity is not None:
                digest = (
                    json.dumps(active_filter, sort_keys=True, default=str) if active_filter else ""
                )
                key = (*identity, digest)
                with _VARIANT_TABLE_CACHE_LOCK:
                    hit = _VARIANT_TABLE_CACHE.get(key)
                    if hit is not None:
                        _VARIANT_TABLE_CACHE.move_to_end(key)
                        return hit
        raw = await access.duckdb_fetch(_VARIANTS_SQL)

    def _build() -> list[VariantEntry]:
        from mate.api.ingest.aggregation import variant_id_for_str

        entries: list[VariantEntry] = []
        for activities_str, case_count, avg_d, med_d, first_seen, last_seen in raw:
            entries.append(
                VariantEntry(
                    variant_id=variant_id_for_str(activities_str or ""),
                    activities_str=activities_str or "",
                    case_count=int(case_count),
                    avg_duration_seconds=float(avg_d) if avg_d is not None else None,
                    median_duration_seconds=float(med_d) if med_d is not None else None,
                    first_seen=first_seen,
                    last_seen=last_seen,
                )
            )
        return entries

    entries = await asyncio.to_thread(_build)
    if key is not None and len(entries) <= _VARIANT_TABLE_CACHE_MAX_ROWS:
        approx_bytes = sum(len(e.activities_str) for e in entries)
        if approx_bytes <= _VARIANT_TABLE_CACHE_MAX_BYTES:
            with _VARIANT_TABLE_CACHE_LOCK:
                _VARIANT_TABLE_CACHE[key] = entries
                _VARIANT_TABLE_CACHE.move_to_end(key)
                while len(_VARIANT_TABLE_CACHE) > _VARIANT_TABLE_CACHE_MAX:
                    _VARIANT_TABLE_CACHE.popitem(last=False)
    return entries


def _sort_variants(
    entries: list[VariantEntry], sort_col: str, direction: str
) -> list[VariantEntry]:
    """Sort on one column with NULLs last in both directions (DuckDB default)."""
    reverse = direction == "desc"
    if sort_col == "case_count" and reverse:
        return entries  # cache order
    non_null = [e for e in entries if getattr(e, sort_col) is not None]
    nulls = [e for e in entries if getattr(e, sort_col) is None]
    non_null.sort(key=lambda e: getattr(e, sort_col), reverse=reverse)
    return non_null + nulls


async def variant_rows(
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
    """One page of the variants listing + the (filtered) total."""
    sort_col, _, direction = sort.partition(":")
    direction = (direction or "desc").lower()
    if sort_col not in VARIANT_SORTS:
        raise HTTPException(status_code=422, detail=f"Unknown variants sort: {sort_col!r}.")
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="sort direction must be asc/desc.")

    entries = await variant_table(log_id, user_id, active_filter)
    if activity_contains:
        needle = activity_contains.lower()
        entries = [e for e in entries if needle in e.activities_str.lower()]
    if min_case_count is not None:
        entries = [e for e in entries if e.case_count >= min_case_count]
    entries = _sort_variants(entries, sort_col, direction)

    total = len(entries)
    page = entries[offset : offset + limit]

    out: list[VariantRow] = []
    for i, e in enumerate(page, start=offset + 1):
        activities = e.activities_str.split("→") if e.activities_str else []
        case_pct = (float(e.case_count) / total_cases) if total_cases else 0.0
        out.append(
            VariantRow(
                rank=i,
                variant_id=e.variant_id,
                activities=activities,
                case_count=e.case_count,
                case_pct=case_pct,
                avg_duration_seconds=e.avg_duration_seconds,
                median_duration_seconds=e.median_duration_seconds,
                first_seen=e.first_seen,
                last_seen=e.last_seen,
            )
        )
    return out, total


async def variant_detail(log_row: EventLog, user_id: str, variant_id: str) -> VariantDetail:
    """Rank, aggregates, duration histogram + attribute breakdowns for one variant."""
    total_cases = int(log_row.cases_count or 0)

    # Rank + aggregates come from the cached variant table (default order is
    # already case_count desc).
    entries = await variant_table(log_row.id, user_id, log_row.active_filter)
    rank = 0
    target: VariantEntry | None = None
    for i, e in enumerate(entries, start=1):
        if e.variant_id == variant_id:
            rank, target = i, e
            break
    if target is None:
        raise HTTPException(status_code=404, detail="Variant not found.")

    activities = target.activities_str.split("→") if target.activities_str else []
    case_pct = (float(target.case_count) / total_cases) if total_cases else 0.0

    # Histogram + p90 + per-attribute breakdowns from the same DuckDB conn.
    # `_variant_case_ids` (temp table on this conn) is materialised once and
    # shared by the durations query + every per-column breakdown - the old
    # code re-aggregated the whole log per breakdown column.
    async with EventLogAccess(log_row.id, user_id, log_row.active_filter) as access:
        if not log_row.active_filter and access.cases_path.exists():
            # cases.parquet already stores variant_id + duration per case
            # (kept coherent at import and on every edit) - no events scan.
            await access.duckdb_fetch(
                "CREATE TEMP TABLE _variant_case_ids AS "
                "SELECT case_id FROM cases WHERE variant_id = ?",
                [variant_id],
            )
            d_rows = await access.duckdb_fetch(
                "SELECT case_duration_seconds FROM cases WHERE variant_id = ?",
                [variant_id],
            )
        else:
            await access.duckdb_fetch(
                """
                CREATE TEMP TABLE _variant_case_ids AS
                SELECT case_id FROM (
                    SELECT case_id,
                           string_agg(activity, '→' ORDER BY timestamp) AS activities_str
                    FROM events
                    GROUP BY case_id
                )
                WHERE activities_str = ?
                """,
                [target.activities_str],
            )
            d_rows = await access.duckdb_fetch(
                """
                SELECT EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) AS duration_s
                FROM events
                WHERE case_id IN (SELECT case_id FROM _variant_case_ids)
                GROUP BY case_id
                """
            )
        durations = [float(r[0]) for r in d_rows if r[0] is not None]
        durations.sort()
        p90 = durations[int(len(durations) * 0.9)] if durations else None
        bins, edges = _histogram(durations)

        breakdowns = await _attribute_breakdowns(access, log_row)

    return VariantDetail(
        rank=rank,
        variant_id=target.variant_id,
        activities=activities,
        case_count=target.case_count,
        case_pct=case_pct,
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
    log_row: EventLog,
) -> list[AttributeBreakdown]:
    """Top-5 value breakdown per non-canonical column, restricted to the cases
    in the `_variant_case_ids` temp table the caller materialised on `access`."""
    overrides = log_row.column_overrides if isinstance(log_row.column_overrides, dict) else None
    specs = await access.column_specs(overrides)
    skip = {"case_id", "activity", "timestamp", "end_timestamp"}
    out: list[AttributeBreakdown] = []
    for spec in specs:
        if spec.name in skip:
            continue
        ident = _quote_ident(spec.name)
        sql = f"""
            SELECT {ident} AS value, COUNT(*) AS n
            FROM events
            WHERE case_id IN (SELECT case_id FROM _variant_case_ids)
            GROUP BY {ident}
            ORDER BY n DESC
            LIMIT 5
        """
        rows = await access.duckdb_fetch(sql)
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


async def variant_cases_page(
    log_id: str,
    user_id: str,
    variant_id: str,
    *,
    offset: int,
    limit: int,
) -> VariantCasesPage:
    """Case-level metadata rows (id, start/end, duration, event count) for a variant."""
    async with EventLogAccess(log_id, user_id) as access:
        # Rebuild the variant's activity sequence on the fly so we can match cases.
        # The variant_id alone isn't enough since it's a hash - but we have the
        # cases.parquet which already stores variant_id per case (computed at
        # import / on every edit), so we join through that.
        if not access.cases_path.exists():
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


# ── activity detail ──────────────────────────────────────────────────────────


async def activity_detail(log_row: EventLog, user_id: str, activity: str) -> ActivityDetail:
    """Frequency, case coverage, start/end role + containing variants for one activity."""
    total_cases = int(log_row.cases_count or 0)

    # Containing variants come from the cached variant table (default order is
    # already case_count desc, so rank is positional). Membership is exact -
    # the variants listing's `activity_contains` is a substring match, this
    # must not treat "ship" as contained in "ship extra".
    entries = await variant_table(log_row.id, user_id, log_row.active_filter)
    variant_count = 0
    top_variants: list[VariantRow] = []
    for i, e in enumerate(entries, start=1):
        activities = e.activities_str.split("→") if e.activities_str else []
        if activity not in activities:
            continue
        variant_count += 1
        if len(top_variants) < 10:
            top_variants.append(
                VariantRow(
                    rank=i,
                    variant_id=e.variant_id,
                    activities=activities,
                    case_count=e.case_count,
                    case_pct=(float(e.case_count) / total_cases) if total_cases else 0.0,
                    avg_duration_seconds=e.avg_duration_seconds,
                    median_duration_seconds=e.median_duration_seconds,
                    first_seen=e.first_seen,
                    last_seen=e.last_seen,
                )
            )

    async with EventLogAccess(log_row.id, user_id, log_row.active_filter) as access:
        total_events, distinct_cases = (
            await access.duckdb_fetch("SELECT COUNT(*), COUNT(DISTINCT case_id) FROM events")
        )[0]
        event_count, case_count, first_seen, last_seen = (
            await access.duckdb_fetch(
                "SELECT COUNT(*), COUNT(DISTINCT case_id), MIN(timestamp), MAX(timestamp) "
                "FROM events WHERE activity = ?",
                [activity],
            )
        )[0]
        if not event_count:
            raise HTTPException(status_code=404, detail="Activity not found.")
        (max_per_case,) = (
            await access.duckdb_fetch(
                "SELECT MAX(n) FROM ("
                "SELECT COUNT(*) AS n FROM events WHERE activity = ? GROUP BY case_id)",
                [activity],
            )
        )[0]
        if not log_row.active_filter and access.cases_path.exists():
            # cases.parquet stores first/last activity per case (kept coherent
            # at import and on every edit) - no events scan.
            start_cases, end_cases = (
                await access.duckdb_fetch(
                    "SELECT COUNT(*) FILTER (WHERE first_activity = ?), "
                    "COUNT(*) FILTER (WHERE last_activity = ?) FROM cases",
                    [activity, activity],
                )
            )[0]
        else:
            start_cases, end_cases = (
                await access.duckdb_fetch(
                    """
                    SELECT COUNT(*) FILTER (WHERE fa = ?), COUNT(*) FILTER (WHERE la = ?)
                    FROM (
                        SELECT arg_min(activity, timestamp) AS fa,
                               arg_max(activity, timestamp) AS la
                        FROM events
                        GROUP BY case_id
                    )
                    """,
                    [activity, activity],
                )
            )[0]

    event_count = int(event_count)
    case_count = int(case_count)
    total_events = int(total_events)
    distinct_cases = int(distinct_cases)
    return ActivityDetail(
        activity=activity,
        event_count=event_count,
        event_pct=(float(event_count) / total_events) if total_events else 0.0,
        case_count=case_count,
        case_pct=(float(case_count) / distinct_cases) if distinct_cases else 0.0,
        avg_occurrences_per_case=(float(event_count) / case_count) if case_count else None,
        max_occurrences_per_case=int(max_per_case) if max_per_case is not None else None,
        start_case_count=int(start_cases),
        start_case_pct=(float(start_cases) / distinct_cases) if distinct_cases else 0.0,
        end_case_count=int(end_cases),
        end_case_pct=(float(end_cases) / distinct_cases) if distinct_cases else 0.0,
        first_seen=first_seen,
        last_seen=last_seen,
        variant_count=variant_count,
        top_variants=top_variants,
    )


async def activity_cases_page(
    log_id: str,
    user_id: str,
    activity: str,
    *,
    offset: int,
    limit: int,
) -> ActivityCasesPage:
    """Case-level metadata rows for the cases containing an activity."""
    async with EventLogAccess(log_id, user_id) as access:
        # Case metadata (start/end/duration) lives in cases.parquet - mirror
        # variant_cases_page and serve an empty page when it's missing.
        if not access.cases_path.exists():
            return ActivityCasesPage(rows=[], total=0, offset=offset, limit=limit)

        (total,) = (
            await access.duckdb_fetch(
                "SELECT COUNT(DISTINCT case_id) FROM events WHERE activity = ?",
                [activity],
            )
        )[0]
        rows = await access.duckdb_fetch(
            """
            SELECT c.case_id, c.case_start, c.case_end, c.case_duration_seconds,
                   c.event_count, a.occurrences
            FROM cases c
            JOIN (
                SELECT case_id, COUNT(*) AS occurrences
                FROM events
                WHERE activity = ?
                GROUP BY case_id
            ) a USING (case_id)
            ORDER BY c.case_start DESC NULLS LAST
            LIMIT ? OFFSET ?
            """,
            [activity, limit, offset],
        )

    return ActivityCasesPage(
        rows=[
            ActivityCase(
                case_id=str(r[0]),
                case_start=r[1],
                case_end=r[2],
                case_duration_seconds=float(r[3]) if r[3] is not None else None,
                event_count=int(r[4]),
                occurrences=int(r[5]),
            )
            for r in rows
        ],
        total=int(total),
        offset=offset,
        limit=limit,
    )


# ── committed (applied) filter ───────────────────────────────────────────────


async def validate_committed_filter(
    log_id: str, user_id: str, entries: list[dict[str, Any]]
) -> None:
    """Validate filter entries (shape/op/field) against the log's columns (422)."""
    async with EventLogAccess(log_id, user_id) as access:
        specs = await access.column_specs()
        validate_filters(entries, {s.name for s in specs})


async def commit_active_filter(
    session: AsyncSession,
    log_row: EventLog,
    user_id: str,
    entries: list[dict[str, Any]],
) -> bool:
    """Commit the Events-tab filter as the *applied* dataset filter.

    Persists it on the log, then re-publishes ``log.imported`` so every
    installed module re-runs its import/processing against the now-filtered
    data (modules subscribe to that topic). An empty ``entries`` clears the
    overlay - back to the full dataset - and likewise re-runs modules.
    Returns whether the re-import signal was published (modules_retriggered).
    """
    await validate_committed_filter(log_row.id, user_id, entries)

    log_row.active_filter = entries or None
    log_row.last_edited_at = _utcnow()
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
                "log_id": log_row.id,
                "user_id": user_id,
                "events_count": int(log_row.events_count or 0),
                "cases_count": int(log_row.cases_count or 0),
                "detected_schema": log_row.detected_schema,
                "fixed_columns": [],
                "reapplied": True,
            },
        )
        retriggered = True
    except Exception:
        log.exception("active_filter.republish_failed", log_id=log_row.id)
    return retriggered


# ── OCEL aggregates ──────────────────────────────────────────────────────────


def ocel_overview_payload(row: EventLog) -> OcelOverview:
    """Counts + object types + activity names, from the row's detected schema."""
    schema = row.detected_schema if isinstance(row.detected_schema, dict) else {}
    object_types = [
        OcelObjectTypeEntry(type=str(e.get("type")), count=int(e.get("count", 0)))
        for e in (schema.get("object_types") or [])
    ]
    activities = [str(a) for a in (schema.get("activities") or [])]
    return OcelOverview(
        events_count=int(row.events_count or 0),
        objects_count=int(row.objects_count or 0),
        object_types_count=int(row.object_types_count or 0),
        relations_count=int(row.relations_count or 0),
        date_min=row.date_min,
        date_max=row.date_max,
        object_types=object_types,
        activities=activities,
    )


async def ocel_object_type_counts(log_id: str, user_id: str) -> list[OcelObjectTypeEntry]:
    """Object type → object count, from the objects table (descending)."""
    type_col = _quote_ident("ocel:type")
    async with ObjectCentricLogAccess(log_id, user_id) as access:
        rows = await access.duckdb_fetch(
            f"SELECT {type_col} AS t, COUNT(*) AS n FROM ocel_objects "
            f"GROUP BY {type_col} ORDER BY n DESC"
        )
    return [OcelObjectTypeEntry(type=str(t), count=int(n)) for t, n in rows]


# ── event-log lifecycle (import / reimport / remap / duplicate / delete) ────


async def import_log_from_url(
    session: AsyncSession,
    runtime: JobRuntime,
    user_id: str,
    *,
    url: str,
    name: str | None = None,
    csv_mapping: str | None = None,
    xml_mapping: str | None = None,
    json_mapping: str | None = None,
) -> tuple[str, str]:
    """Download a remote XES / XES.GZ / CSV / XML / JSON / OCEL and queue it.

    Mapping arguments are JSON-encoded strings (exactly the /from-url body
    shape). Returns ``(log_id, job_id)``.
    """
    # Derive a filename from the URL path so detect_format can sniff the extension.
    url_path = unquote(urlparse(url).path)
    filename = url_path.rsplit("/", 1)[-1] or "import"

    try:
        coarse_format = detect_format(filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=415,
            detail=f"Cannot determine file format from URL path ({filename!r}). "
            "Make sure the URL ends with .xes, .csv, .xml, .json, an OCEL extension, "
            "or a compressed variant (.gz/.bz2/.xz/.zip).",
        ) from exc

    parsed_mapping: CsvColumnMapping | None = None
    if csv_mapping:
        try:
            parsed_mapping = CsvColumnMapping.model_validate(json.loads(csv_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid csv_mapping: {exc}") from exc

    parsed_xml_mapping: XmlColumnMapping | None = None
    if xml_mapping:
        try:
            parsed_xml_mapping = XmlColumnMapping.model_validate(json.loads(xml_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid xml_mapping: {exc}") from exc

    parsed_json_mapping: JsonColumnMapping | None = None
    if json_mapping:
        try:
            parsed_json_mapping = JsonColumnMapping.model_validate(json.loads(json_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid json_mapping: {exc}") from exc

    # Download the remote file.
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=400,
                    detail=f"Remote server returned HTTP {resp.status_code} for the given URL.",
                )
            raw = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {exc}") from exc

    log_id = uuid7_str()
    paths = log_paths(log_id, user_id)
    paths.ensure()

    ext = original_extension(filename, coarse_format)
    original_path = paths.original_for(ext)

    async with aiofiles.open(original_path, "wb") as out:
        await out.write(raw)

    # Refine the coarse guess from the downloaded content (OCEL vs case-centric;
    # a bare .zip resolves to its single member's format - ValueError means an
    # empty/ambiguous archive).
    try:
        source_format, ocel_flavor = await asyncio.to_thread(
            sniff_format, original_path, coarse_format, filename=filename
        )
    except ValueError as exc:
        await asyncio.to_thread(paths.remove)
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    display_name = (name or filename).strip() or filename
    # Strip the extension (and a compression suffix) from auto-derived names.
    if not name:
        for comp in (".gz", ".gzip", ".bz2", ".xz", ".lzma", ".zip"):
            if display_name.lower().endswith(comp):
                display_name = display_name[: -len(comp)]
                break
        for suffix in (".xes.gz", ".xes", ".csv", ".xml", ".json", ".jsonocel", ".xmlocel"):
            if display_name.lower().endswith(suffix):
                display_name = display_name[: -len(suffix)]
                break

    session.add(
        EventLog(
            id=log_id,
            user_id=user_id,
            name=display_name,
            source_format=source_format,
            source_filename=filename,
            status="importing",
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await session.commit()

    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user_id,
        title=f"Import - {display_name}",
        subtitle=f"event_log.import · {source_format} (url)",
        payload={
            "log_id": log_id,
            "source_format": source_format,
            "ocel_flavor": ocel_flavor,
            "original_path": str(original_path),
            "csv_mapping": parsed_mapping.model_dump() if parsed_mapping else None,
            "xml_mapping": parsed_xml_mapping.model_dump() if parsed_xml_mapping else None,
            "json_mapping": parsed_json_mapping.model_dump() if parsed_json_mapping else None,
        },
    )

    log.info(
        "event_log.created_from_url",
        log_id=log_id,
        job_id=job_id,
        source_format=source_format,
        url=url,
    )
    return log_id, job_id


def reimport_preflight(row: EventLog) -> str:
    """The re-import 409 guards; returns the (narrowed) source format."""
    if row.status == "importing":
        raise HTTPException(status_code=409, detail="Import already in progress.")
    if not row.source_format:
        raise HTTPException(
            status_code=409, detail="No source format on record - cannot re-run import."
        )
    return row.source_format


async def reimport_log(
    session: AsyncSession, runtime: JobRuntime, row: EventLog, user_id: str
) -> str:
    """Re-run the import job using the original upload that's still on disk.

    The CSV mapping (when applicable) is recovered from the previous run's
    `meta.json` so column-mapped CSVs don't need to be re-mapped. Returns the
    new import job id.
    """
    source_format = reimport_preflight(row)
    log_id = row.id

    paths = log_paths(log_id, user_id)
    # The retained upload may live only in the S3 bucket on a cold cache - pull
    # the log dir back before locating it (no-op in local mode).
    await storage_sync.hydrate_log(user_id, log_id)
    await storage_sync.hydrate_original(user_id, log_id)
    # OCEL stores its upload under the real suffix (jsonocel/xmlocel/sqlite), not
    # original.ocel - locate by glob so re-import works for every format.
    original_path = paths.original_for(source_format)
    if not original_path.exists():
        located = paths.find_original()
        if located is None:
            raise HTTPException(
                status_code=409,
                detail="Original upload is missing on disk - cannot re-run import.",
            )
        original_path = located

    saved_mapping: dict[str, Any] | None = None
    # OCEL reader flavor is content-detected at first import; recover it from
    # meta so re-import picks the same pm4py reader (the .json/.xml suffix alone
    # is not enough to distinguish OCEL json from OCEL xml).
    ocel_flavor: str | None = None
    if paths.meta.exists():
        try:
            meta = json.loads(paths.meta.read_text())
            if isinstance(meta, dict):
                mapping = meta.get("mapping")
                saved_mapping = mapping if isinstance(mapping, dict) else None
                flavor = meta.get("ocel_flavor")
                ocel_flavor = flavor if isinstance(flavor, str) else None
        except (OSError, json.JSONDecodeError):
            saved_mapping = None

    csv_mapping_data = saved_mapping if source_format == "csv" else None
    xml_mapping_data = saved_mapping if source_format == "xml" else None
    json_mapping_data = saved_mapping if source_format == "json" else None

    # Reset derived state so the listing reflects "importing" while the worker
    # rebuilds events.parquet / cases.parquet / meta.json.
    row.status = "importing"
    row.error = None
    row.events_count = None
    row.cases_count = None
    row.variants_count = None
    row.objects_count = None
    row.object_types_count = None
    row.relations_count = None
    row.date_min = None
    row.date_max = None
    row.detected_schema = None
    row.imported_at = None
    await session.commit()

    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user_id,
        title=f"Re-import - {row.name}",
        subtitle=f"event_log.import · {source_format}",
        payload={
            "log_id": log_id,
            "source_format": source_format,
            "ocel_flavor": ocel_flavor,
            "original_path": str(original_path),
            "csv_mapping": csv_mapping_data,
            "xml_mapping": xml_mapping_data,
            "json_mapping": json_mapping_data,
        },
    )
    log.info("event_log.reimport_started", log_id=log_id, job_id=job_id)
    return job_id


def remap_preflight(row: EventLog) -> str:
    """The remap 409 guards; returns the (narrowed) source format."""
    if row.log_model == "object_centric":
        raise HTTPException(
            status_code=409,
            detail="Column-role remapping does not apply to object-centric (OCEL) logs.",
        )
    if row.status == "importing":
        raise HTTPException(status_code=409, detail="Import already in progress.")
    if not row.source_format:
        raise HTTPException(status_code=409, detail="No source format on record - cannot re-map.")
    return row.source_format


def validate_remap_roles(row: EventLog, roles: dict[str, str]) -> None:
    """Reject role → source-column choices that don't exist in the log (422).

    Validated against what the importer last saw, when we have that on record -
    a stale/typo'd column name would otherwise just silently fall through to
    autodetect.
    """
    schema = row.detected_schema if isinstance(row.detected_schema, dict) else {}
    known = schema.get("source_columns") or schema.get("columns")
    if isinstance(known, list) and known:
        unknown = sorted({c for c in roles.values() if c not in known})
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown column(s) for this log: {', '.join(unknown)}.",
            )


async def remap_log(
    session: AsyncSession,
    runtime: JobRuntime,
    row: EventLog,
    user_id: str,
    roles: dict[str, str],
) -> str:
    """Re-import the log from its retained original with the user's chosen
    column roles forced. Returns the new import job id.
    """
    source_format = remap_preflight(row)
    log_id = row.id

    paths = log_paths(log_id, user_id)
    # Pull the retained upload back from S3 if the local cache is cold.
    await storage_sync.hydrate_log(user_id, log_id)
    await storage_sync.hydrate_original(user_id, log_id)
    original_path = paths.original_for(source_format)
    if not original_path.exists():
        raise HTTPException(
            status_code=409, detail="Original upload is missing on disk - cannot re-map."
        )

    validate_remap_roles(row, roles)

    row.status = "importing"
    row.error = None
    row.events_count = None
    row.cases_count = None
    row.variants_count = None
    row.date_min = None
    row.date_max = None
    await session.commit()

    # The explicit `column_roles` override is authoritative - applied centrally
    # in dispatch over the freshly re-parsed columns - so we deliberately don't
    # pass the previous csv/xml mapping (which would re-trigger the parser's own
    # rename and fight the override).
    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user_id,
        title=f"Re-map - {row.name}",
        subtitle=f"event_log.import · {source_format}",
        payload={
            "log_id": log_id,
            "source_format": source_format,
            "original_path": str(original_path),
            "csv_mapping": None,
            "xml_mapping": None,
            "column_roles": roles,
        },
    )
    log.info("event_log.remap_started", log_id=log_id, job_id=job_id, roles=roles)
    return job_id


async def duplicate_log(session: AsyncSession, src: EventLog, user_id: str) -> EventLog:
    """Fast-clone an event log by copying its on-disk directory.

    Cheaper than re-importing because the parquet outputs already exist; we
    just clone the bytes into a fresh log id and persist a new metadata row
    in the same folder, immediately after the source log.
    """
    if src.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="Only ready event logs can be duplicated.",
        )

    src_paths = log_paths(src.id, user_id)
    # On a cold S3 cache the bytes live only in the bucket - pull them first.
    await storage_sync.hydrate_log(user_id, src.id)
    await storage_sync.hydrate_original(user_id, src.id)
    if not src_paths.exists():
        raise HTTPException(
            status_code=409,
            detail="Source data is missing on disk - cannot duplicate.",
        )

    new_id = uuid7_str()
    new_paths = log_paths(new_id, user_id)
    try:
        shutil.copytree(src_paths.root, new_paths.root)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Copy failed: {exc}") from exc

    # Sit the duplicate right after the source within the same folder.
    now = datetime.now(UTC).replace(tzinfo=None)
    duplicate = EventLog(
        id=new_id,
        user_id=user_id,
        name=f"{src.name} (copy)",
        source_format=src.source_format,
        source_filename=src.source_filename,
        log_model=src.log_model,
        status="ready",
        events_count=src.events_count,
        cases_count=src.cases_count,
        variants_count=src.variants_count,
        objects_count=src.objects_count,
        object_types_count=src.object_types_count,
        relations_count=src.relations_count,
        date_min=src.date_min,
        date_max=src.date_max,
        detected_schema=src.detected_schema,
        description=src.description,
        column_overrides=src.column_overrides,
        active_filter=src.active_filter,
        folder_id=src.folder_id,
        position=src.position + 1,
        created_at=now,
        imported_at=now,
    )
    session.add(duplicate)
    await session.commit()
    # Mirror the cloned dir to the S3 primary store (no-op in local mode).
    await storage_sync.persist_log(user_id, new_id)
    log.info("event_log.duplicated", source_log_id=src.id, new_log_id=new_id)
    return duplicate


async def delete_log_and_data(
    session: AsyncSession, runtime: JobRuntime, row: EventLog, user_id: str
) -> None:
    """Soft-delete a log after cancelling its jobs, then remove its on-disk data."""
    log_id = row.id
    # Terminate active jobs (import / re-import / module runs) before tearing
    # down the row + on-disk data so workers don't keep writing to a directory
    # we're about to rmtree.
    cancelled = await runtime.cancel_for_logs([log_id])
    if cancelled:
        log.info("event_log.jobs_cancelled", log_id=log_id, count=cancelled)
    row.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    paths = log_paths(log_id, user_id)
    if paths.exists():
        try:
            shutil.rmtree(paths.root)
        except OSError as exc:
            log.warning("event_log.cleanup_failed", log_id=log_id, error=str(exc))
    # Remove the mirrored copy from the S3 primary store too (no-op in local mode).
    await storage_sync.delete_log(user_id, log_id)


# ── folders ──────────────────────────────────────────────────────────────────


async def ensure_no_folder_cycle(
    session: AsyncSession, folder_id: str, candidate_parent_id: str | None, user_id: str
) -> None:
    """Walking up from the candidate parent must not land on the folder itself."""
    cur = candidate_parent_id
    while cur is not None:
        if cur == folder_id:
            raise HTTPException(
                status_code=422,
                detail="Cannot move a folder into one of its descendants.",
            )
        parent = await session.get(Folder, cur)
        if parent is None or parent.user_id != user_id or parent.deleted_at is not None:
            return
        cur = parent.parent_id


async def next_folder_position(session: AsyncSession, user_id: str, parent_id: str | None) -> int:
    """Append position among siblings: max(sibling positions) + 1."""
    sib_max_stmt = select(Folder.position).where(
        Folder.user_id == user_id,
        Folder.deleted_at.is_(None),
        Folder.parent_id.is_(parent_id) if parent_id is None else Folder.parent_id == parent_id,
    )
    sibling_positions = list((await session.execute(sib_max_stmt)).scalars().all())
    return (max(sibling_positions) + 1) if sibling_positions else 0


async def folder_cascade_contents(
    session: AsyncSession, user_id: str, folder_id: str
) -> tuple[list[str], list[EventLog]]:
    """Every descendant folder id (including the target itself) + the live
    event logs contained anywhere in that subtree."""
    folder_ids: list[str] = []
    stack: list[str] = [folder_id]
    while stack:
        cur = stack.pop()
        folder_ids.append(cur)
        descendants = (
            (
                await session.execute(
                    select(Folder.id).where(
                        Folder.user_id == user_id,
                        Folder.parent_id == cur,
                        Folder.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        stack.extend(descendants)

    log_rows = (
        (
            await session.execute(
                select(EventLog).where(
                    EventLog.user_id == user_id,
                    EventLog.folder_id.in_(folder_ids),
                    EventLog.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return folder_ids, list(log_rows)


async def cascade_delete_folder(
    session: AsyncSession, runtime: JobRuntime, user_id: str, folder_id: str
) -> tuple[int, int]:
    """Soft-delete a folder, all descendant folders, and every event log inside.

    On-disk data for each affected event log (parquet outputs + original upload)
    is also removed. Returns ``(folders_deleted, logs_deleted)``.
    """
    now = _utcnow()

    folder_ids, log_rows = await folder_cascade_contents(session, user_id, folder_id)

    deleted_log_ids: list[str] = []
    for row in log_rows:
        row.deleted_at = now
        deleted_log_ids.append(row.id)

    for fid in folder_ids:
        f = await session.get(Folder, fid)
        if f is not None and f.user_id == user_id and f.deleted_at is None:
            f.deleted_at = now

    await session.commit()

    # Terminate any in-flight or queued jobs tied to the affected logs before
    # we delete their on-disk directories, matching delete_event_log.
    cancelled_jobs = await runtime.cancel_for_logs(deleted_log_ids)

    for log_id in deleted_log_ids:
        paths = log_paths(log_id, user_id)
        if paths.exists():
            try:
                shutil.rmtree(paths.root)
            except OSError as exc:
                log.warning("event_log.cleanup_failed", log_id=log_id, error=str(exc))

    log.info(
        "folder.deleted",
        folder_id=folder_id,
        cascade_folders=len(folder_ids),
        cascade_logs=len(deleted_log_ids),
        cancelled_jobs=cancelled_jobs,
    )
    return len(folder_ids), len(deleted_log_ids)
