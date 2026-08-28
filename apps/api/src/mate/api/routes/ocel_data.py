"""Read endpoints behind the object-centric (OCEL) process tabs.

Mounted under ``/api/v1/event-logs/{log_id}/ocel/*`` alongside the case-centric
data routes. Every endpoint is gated to ``log_model == "object_centric"`` logs;
the case-centric endpoints reject OCEL logs symmetrically (see
``event_log_data._require_ready``), so no endpoint ever serves both models.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Query

from mate.api.auth import CurrentUserDep
from mate.api.db.session import SessionDep
from mate.api.modules.object_centric_log_access import (
    ObjectCentricLogAccess,
    _quote_ident,
)
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.ocel_data import (
    OcelEventsPage,
    OcelObjectsPage,
    OcelObjectTypeEntry,
    OcelOverview,
    OcelRelationsPage,
)

# The overview / object-type aggregate bodies live in the service layer so the
# MCP toolset can reuse them; these routes are thin adapters over it.
from mate.api.services import log_aggregates
from mate.api.services.log_aggregates import require_ready_ocel as _require_ocel_ready

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/event-logs/{log_id}", tags=["ocel"])

_MAX_LIMIT = 500


def _row_dict(values: tuple[Any, ...], columns: list[str]) -> dict[str, Any]:
    """Map a DuckDB row tuple to a JSON-safe dict (datetimes → ISO, NaN → None).
    Mirrors the case-centric helper in ``event_log_data``."""
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


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_LIMIT))


async def _page(
    access: ObjectCentricLogAccess,
    view: str,
    *,
    offset: int,
    limit: int,
    where: str,
    params: list[Any],
) -> tuple[list[dict[str, Any]], list[str], int]:
    (total,) = (await access.duckdb_fetch(f"SELECT COUNT(*) FROM {view}{where}", params))[0]
    cols, rows = await access.duckdb_fetch_with_columns(
        f"SELECT * FROM {view}{where} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return [_row_dict(r, cols) for r in rows], cols, int(total)


@router.get("/ocel/overview", response_model=OcelOverview)
async def ocel_overview(log_id: str, session: SessionDep, user: CurrentUserDep) -> OcelOverview:
    row = await _require_ocel_ready(log_id, session, user.id)
    return log_aggregates.ocel_overview_payload(row)


@router.get("/ocel/object-types", response_model=list[OcelObjectTypeEntry])
async def ocel_object_types(
    log_id: str, session: SessionDep, user: CurrentUserDep
) -> list[OcelObjectTypeEntry]:
    await _require_ocel_ready(log_id, session, user.id)
    return await log_aggregates.ocel_object_type_counts(log_id, user.id)


@router.get("/ocel/objects", response_model=OcelObjectsPage)
async def ocel_objects(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 100,
    object_type: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
) -> OcelObjectsPage:
    await _require_ocel_ready(log_id, session, user.id)
    limit = _clamp_limit(limit)
    clauses: list[str] = []
    params: list[Any] = []
    if object_type is not None:
        clauses.append(f"{_quote_ident('ocel:type')} = ?")
        params.append(object_type)
    if q:
        clauses.append(f"CAST({_quote_ident('ocel:oid')} AS VARCHAR) ILIKE ?")
        params.append(f"%{q}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    async with ObjectCentricLogAccess(log_id, user.id) as access:
        rows, cols, total = await _page(
            access, "ocel_objects", offset=offset, limit=limit, where=where, params=params
        )
    return OcelObjectsPage(rows=rows, columns=cols, total=total, offset=offset, limit=limit)


@router.get("/ocel/events", response_model=OcelEventsPage)
async def ocel_events(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 100,
    activity: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
) -> OcelEventsPage:
    await _require_ocel_ready(log_id, session, user.id)
    limit = _clamp_limit(limit)
    clauses: list[str] = []
    params: list[Any] = []
    if activity is not None:
        clauses.append(f"{_quote_ident('ocel:activity')} = ?")
        params.append(activity)
    if q:
        clauses.append(f"CAST({_quote_ident('ocel:eid')} AS VARCHAR) ILIKE ?")
        params.append(f"%{q}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    async with ObjectCentricLogAccess(log_id, user.id) as access:
        rows, cols, total = await _page(
            access, "ocel_events", offset=offset, limit=limit, where=where, params=params
        )
    return OcelEventsPage(rows=rows, columns=cols, total=total, offset=offset, limit=limit)


@router.get("/ocel/relationships", response_model=OcelRelationsPage)
async def ocel_relationships(
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 100,
    object_type: Annotated[str | None, Query()] = None,
    activity: Annotated[str | None, Query()] = None,
) -> OcelRelationsPage:
    await _require_ocel_ready(log_id, session, user.id)
    limit = _clamp_limit(limit)
    clauses: list[str] = []
    params: list[Any] = []
    if object_type is not None:
        clauses.append(f"{_quote_ident('ocel:type')} = ?")
        params.append(object_type)
    if activity is not None:
        clauses.append(f"{_quote_ident('ocel:activity')} = ?")
        params.append(activity)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    async with ObjectCentricLogAccess(log_id, user.id) as access:
        rows, cols, total = await _page(
            access, "ocel_relations", offset=offset, limit=limit, where=where, params=params
        )
    return OcelRelationsPage(rows=rows, columns=cols, total=total, offset=offset, limit=limit)
