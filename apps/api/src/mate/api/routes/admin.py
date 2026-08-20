"""/api/v1/admin - cross-user operations gated by the Keycloak ``admin`` role.

Capabilities here are deliberately admin-only (they read every user's data -
emails, usernames, behaviour-tracking events, process metadata):

* download a consistent snapshot of the whole metadata SQLite database;
* download analytics events (filtered) as XES / NDJSON / CSV for process mining;
* preview + facet the behaviour-event set to drive the export filter UI.

The event-bus ``user_id`` tenant-isolation invariant does not apply: these are
deliberately cross-user, admin-gated REST reads (mirrors ``admin_insights.py``).

See ``apps/web/app/(platform)/admin/export`` for the UI.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy import case as sa_case
from sqlalchemy.engine import make_url
from sqlalchemy.sql import Select
from starlette.background import BackgroundTask

from mate.api.auth import ADMIN_ROLE, AdminUserDep, CurrentUserDep
from mate.api.config import get_settings
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import AnalyticsEvent, User
from mate.api.db.session import SessionDep
from mate.api.routes.analytics import event_to_dict

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

EventSource = Literal["client", "server"]


# --------------------------------------------------------------------------
# Shared behaviour-event filter
# --------------------------------------------------------------------------


def _event_filters(
    *,
    user_id: str | None = None,
    source: EventSource | None = None,
    event_type: str | None = None,
    event_name: str | None = None,
    path_prefix: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session_id: str | None = None,
) -> list[ColumnElement[bool]]:
    """Build SQLAlchemy predicates for the ``AnalyticsEvent`` export filters.

    Every param is optional; an absent param adds no predicate. ``start``/``end``
    form a half-open ``[start, end)`` window on ``occurred_at`` (stored naive-UTC,
    so callers pass naive-UTC). ``path_prefix`` matches the path head
    case-insensitively. Mirrors the filter style in ``admin_insights.py``.
    """
    filters: list[ColumnElement[bool]] = []
    if user_id:
        filters.append(AnalyticsEvent.user_id == user_id)
    if source:
        filters.append(AnalyticsEvent.source == source)
    if event_type:
        filters.append(AnalyticsEvent.event_type == event_type)
    if event_name:
        filters.append(AnalyticsEvent.event_name == event_name)
    if path_prefix:
        filters.append(AnalyticsEvent.path.ilike(f"{path_prefix}%"))
    if start is not None:
        filters.append(AnalyticsEvent.occurred_at >= _naive(start))
    if end is not None:
        filters.append(AnalyticsEvent.occurred_at < _naive(end))
    if session_id:
        filters.append(AnalyticsEvent.session_id == session_id)
    return filters


def _naive(dt: datetime) -> datetime:
    """Coerce an aware datetime to naive-UTC (the column's storage form)."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _filtered(stmt: Select[Any], filters: Sequence[ColumnElement[bool]]) -> Select[Any]:
    return stmt.where(*filters) if filters else stmt


def _log_filters(
    *,
    user_id: str | None,
    source: str | None,
    event_type: str | None,
    event_name: str | None,
    path_prefix: str | None,
    start: datetime | None,
    end: datetime | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Compact dict of the active (non-None) filters for the audit log line."""
    raw = {
        "user_id": user_id,
        "source": source,
        "event_type": event_type,
        "event_name": event_name,
        "path_prefix": path_prefix,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "session_id": session_id,
    }
    return {k: v for k, v in raw.items() if v is not None}


def _db_path() -> Path:
    """Resolve the on-disk SQLite file backing ``database_url``.

    ``make_url`` turns ``sqlite+aiosqlite:////app/data/metadata.db`` into the
    absolute ``/app/data/metadata.db`` and the dev ``...:///data/metadata.db``
    into the CWD-relative ``data/metadata.db``.
    """
    url = make_url(get_settings().database_url)
    database = url.database
    if not database:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Database is not file-backed; export is only supported for SQLite.",
        )
    return Path(database)


def _snapshot_db(src_path: Path) -> Path:
    """Copy ``src_path`` to a fresh temp file via SQLite's online backup API.

    Safe to call while the app is writing: the backup yields a transactionally
    consistent snapshot of committed data (WAL-aware), unlike a naive file copy.
    The caller owns the returned temp file and must delete it.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="metadata-export-", suffix=".db")
    os.close(fd)
    dst_path = Path(tmp_name)
    dst_path.chmod(0o600)
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dst_path


def _unlink(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


class ExportInfo(BaseModel):
    is_admin: bool
    # Populated only for admins - a non-admin learns nothing about the data.
    user_count: int | None = None
    event_count: int | None = None
    db_size_bytes: int | None = None


@router.get("/export-info", response_model=ExportInfo)
async def export_info(user: CurrentUserDep, session: SessionDep) -> ExportInfo:
    """Whether the caller may export, plus a size/scope preview for admins.

    Guarded by ``CurrentUserDep`` (not admin) so the export page can render a
    "you need the admin role" state instead of a hard 403 for normal users.
    """
    if ADMIN_ROLE not in user.roles:
        return ExportInfo(is_admin=False)

    user_count = await session.scalar(select(func.count()).select_from(User)) or 0
    event_count = await session.scalar(select(func.count()).select_from(AnalyticsEvent)) or 0
    src = _db_path()
    size = src.stat().st_size if src.exists() else None
    return ExportInfo(
        is_admin=True,
        user_count=int(user_count),
        event_count=int(event_count),
        db_size_bytes=size,
    )


@router.get("/export/metadata-db")
async def export_metadata_db(user: AdminUserDep) -> FileResponse:
    """Stream a consistent snapshot of the full metadata database.

    Admin-only. The snapshot is written to a private temp file and deleted once
    the response finishes streaming.
    """
    src = _db_path()
    if not src.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database file not found")

    snapshot = await run_in_threadpool(_snapshot_db, src)
    log.info("admin_db_export", admin_id=user.id, bytes=snapshot.stat().st_size)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        snapshot,
        media_type="application/x-sqlite3",
        filename=f"metadata-{ts}.db",
        background=BackgroundTask(_unlink, snapshot),
    )


# --------------------------------------------------------------------------
# XES event-log export
# --------------------------------------------------------------------------

_XES_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<log xes.version="1.0" xes.features="nested-attributes" '
    'xmlns="http://www.xes-standard.org/">\n'
    '  <extension name="Concept" prefix="concept" '
    'uri="http://www.xes-standard.org/concept.xesext"/>\n'
    '  <extension name="Time" prefix="time" '
    'uri="http://www.xes-standard.org/time.xesext"/>\n'
    '  <extension name="Lifecycle" prefix="lifecycle" '
    'uri="http://www.xes-standard.org/lifecycle.xesext"/>\n'
    '  <classifier name="Activity" keys="concept:name"/>\n'
)


def _xes_q(value: object) -> str:
    """Quote a string for use as an XES XML attribute value."""
    return '"' + escape(str(value), {'"': "&quot;"}) + '"'


def _xes_attr(key: str, value: Any) -> str:
    """Render one typed XES attribute, or "" for ``None`` (omitted)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<boolean key={_xes_q(key)} value="{"true" if value else "false"}"/>'
    if isinstance(value, int):
        return f'<int key={_xes_q(key)} value="{value}"/>'
    if isinstance(value, float):
        return f'<float key={_xes_q(key)} value="{value}"/>'
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str)
    return f"<string key={_xes_q(key)} value={_xes_q(value)}/>"


def _xes_date(key: str, dt: datetime | None) -> str:
    if dt is None:
        return ""
    # Stored naive in UTC - stamp the zone so XES parsers read it correctly.
    return f"<date key={_xes_q(key)} value={_xes_q(dt.replace(tzinfo=UTC).isoformat())}/>"


# Top-level event columns surfaced as XES attributes (besides the standard
# concept:name / time:timestamp / lifecycle:transition). The variable
# per-event ``properties`` dict is flattened alongside under a ``prop:`` prefix.
_EVENT_COLS = (
    "source",
    "event_type",
    "user_id",
    "anon_user_id",
    "session_id",
    "path",
    "referrer",
    "duration_ms",
    "viewport_w",
    "viewport_h",
    "ua_class",
    "locale",
    "tz",
)


def _event_xml(ev: AnalyticsEvent) -> str:
    parts = [
        "    <event>\n",
        f"      {_xes_attr('concept:name', ev.event_name)}\n",
        f"      {_xes_date('time:timestamp', ev.occurred_at)}\n",
        f"      {_xes_attr('lifecycle:transition', 'complete')}\n",
    ]
    for col in _EVENT_COLS:
        attr = _xes_attr(col, getattr(ev, col))
        if attr:
            parts.append(f"      {attr}\n")
    received = _xes_date("server_received_at", ev.server_received_at)
    if received:
        parts.append(f"      {received}\n")
    if isinstance(ev.properties, dict):
        for key, value in ev.properties.items():
            attr = _xes_attr(f"prop:{key}", value)
            if attr:
                parts.append(f"      {attr}\n")
    parts.append("    </event>\n")
    return "".join(parts)


@router.get("/export/event-log.xes")
async def export_event_log_xes(
    user: AdminUserDep,
    case: Literal["session", "user"] = "session",
    user_id: str | None = None,
    source: EventSource | None = None,
    event_type: str | None = None,
    event_name: str | None = None,
    path_prefix: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session_id: str | None = None,
) -> StreamingResponse:
    """Stream the (filtered) analytics events across all users as an XES log.

    Admin-only and deliberately cross-user. The trace (case) is the browser
    session by default, or the user with ``?case=user``; server-side events (no
    browser session) fall back to the user as their case. Activity is the event
    name, the timestamp is when it occurred, and every column plus the per-event
    ``properties`` is emitted as an XES attribute. The optional filter params
    (``user_id``/``source``/``event_type``/``event_name``/``path_prefix``/
    ``start``/``end``/``session_id``) narrow the event set. The stream is ordered
    by case then time so traces are contiguous, and a fresh DB session is held
    open for the whole download.
    """
    case_key = (
        AnalyticsEvent.user_id
        if case == "user"
        else sa_case(
            (AnalyticsEvent.source == "server", AnalyticsEvent.user_id),
            else_=AnalyticsEvent.session_id,
        )
    )
    filters = _event_filters(
        user_id=user_id,
        source=source,
        event_type=event_type,
        event_name=event_name,
        path_prefix=path_prefix,
        start=start,
        end=end,
        session_id=session_id,
    )
    stmt = _filtered(select(AnalyticsEvent, case_key.label("case_key")), filters).order_by(
        case_key, AnalyticsEvent.occurred_at, AnalyticsEvent.id
    )

    async def _stream() -> AsyncIterator[str]:
        yield _XES_HEADER
        sm = get_sessionmaker()
        current: str | None = None
        open_trace = False
        async with sm() as session:
            result = await session.stream(stmt)
            async for ev, case_id in result:
                if case_id != current:
                    if open_trace:
                        yield "  </trace>\n"
                    current = case_id
                    open_trace = True
                    yield f"  <trace>\n    {_xes_attr('concept:name', case_id)}\n"
                yield _event_xml(ev)
            if open_trace:
                yield "  </trace>\n"
        yield "</log>\n"

    log.info(
        "admin_xes_export",
        admin_id=user.id,
        case=case,
        filters=_log_filters(
            user_id=user_id,
            source=source,
            event_type=event_type,
            event_name=event_name,
            path_prefix=path_prefix,
            start=start,
            end=end,
            session_id=session_id,
        ),
    )
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        _stream(),
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="events-{ts}.xes"'},
    )


# --------------------------------------------------------------------------
# NDJSON / CSV event export (filtered)
# --------------------------------------------------------------------------

# Column order for the CSV export. ``properties`` is the variable per-event dict;
# it's flattened to a single JSON-string column so the header stays fixed.
_CSV_COLUMNS = (
    "id",
    "user_id",
    "session_id",
    "anon_user_id",
    "source",
    "event_type",
    "event_name",
    "duration_ms",
    "path",
    "referrer",
    "viewport_w",
    "viewport_h",
    "ua_class",
    "locale",
    "tz",
    "occurred_at",
    "server_received_at",
    "properties",
)


def _ordered_filtered_stmt(filters: Sequence[ColumnElement[bool]]) -> Select[Any]:
    """Select all matching events, oldest first, with a stable id tiebreak."""
    return _filtered(select(AnalyticsEvent), filters).order_by(
        AnalyticsEvent.occurred_at.asc(), AnalyticsEvent.id.asc()
    )


@router.get("/export/events.ndjson")
async def export_events_ndjson(
    user: AdminUserDep,
    user_id: str | None = None,
    source: EventSource | None = None,
    event_type: str | None = None,
    event_name: str | None = None,
    path_prefix: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session_id: str | None = None,
) -> StreamingResponse:
    """Stream the filtered analytics events as NDJSON (one JSON object per line).

    Admin-only, deliberately cross-user. Row shape comes from the shared
    ``event_to_dict`` builder so it matches the per-user ``/usage/export`` dump.
    A fresh DB session is held open for the whole stream.
    """
    filters = _event_filters(
        user_id=user_id,
        source=source,
        event_type=event_type,
        event_name=event_name,
        path_prefix=path_prefix,
        start=start,
        end=end,
        session_id=session_id,
    )
    stmt = _ordered_filtered_stmt(filters)

    async def _stream() -> AsyncIterator[str]:
        sm = get_sessionmaker()
        async with sm() as session:
            result = await session.stream(stmt)
            async for (ev,) in result:
                yield json.dumps(event_to_dict(ev), default=str) + "\n"

    log.info(
        "admin_ndjson_export",
        admin_id=user.id,
        filters=_log_filters(
            user_id=user_id,
            source=source,
            event_type=event_type,
            event_name=event_name,
            path_prefix=path_prefix,
            start=start,
            end=end,
            session_id=session_id,
        ),
    )
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="events-{ts}.ndjson"'},
    )


@router.get("/export/events.csv")
async def export_events_csv(
    user: AdminUserDep,
    user_id: str | None = None,
    source: EventSource | None = None,
    event_type: str | None = None,
    event_name: str | None = None,
    path_prefix: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session_id: str | None = None,
) -> StreamingResponse:
    """Stream the filtered analytics events as CSV.

    Admin-only, deliberately cross-user. Uses ``csv.writer`` against an in-memory
    buffer that's drained per row so the response streams; ``properties`` is
    flattened to a single JSON-string column. Same filters as the NDJSON/XES
    exports.
    """
    filters = _event_filters(
        user_id=user_id,
        source=source,
        event_type=event_type,
        event_name=event_name,
        path_prefix=path_prefix,
        start=start,
        end=end,
        session_id=session_id,
    )
    stmt = _ordered_filtered_stmt(filters)

    def _row(ev: AnalyticsEvent) -> list[str]:
        d = event_to_dict(ev)
        out: list[str] = []
        for col in _CSV_COLUMNS:
            value = d.get(col)
            if col == "properties":
                out.append("" if value is None else json.dumps(value, default=str))
            else:
                out.append("" if value is None else str(value))
        return out

    async def _stream() -> AsyncIterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_COLUMNS)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        sm = get_sessionmaker()
        async with sm() as session:
            result = await session.stream(stmt)
            async for (ev,) in result:
                writer.writerow(_row(ev))
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

    log.info(
        "admin_csv_export",
        admin_id=user.id,
        filters=_log_filters(
            user_id=user_id,
            source=source,
            event_type=event_type,
            event_name=event_name,
            path_prefix=path_prefix,
            start=start,
            end=end,
            session_id=session_id,
        ),
    )
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="events-{ts}.csv"'},
    )


# --------------------------------------------------------------------------
# Export preview + facets (drive the filter UI)
# --------------------------------------------------------------------------


class ExportTypeCount(BaseModel):
    label: str
    count: int


class ExportPreview(BaseModel):
    matched_events: int
    matched_sessions: int
    distinct_users: int
    date_min: datetime | None
    date_max: datetime | None
    event_types: list[ExportTypeCount]


@router.get("/export/preview", response_model=ExportPreview)
async def export_preview(
    user: AdminUserDep,
    session: SessionDep,
    user_id: str | None = None,
    source: EventSource | None = None,
    event_type: str | None = None,
    event_name: str | None = None,
    path_prefix: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session_id: str | None = None,
) -> ExportPreview:
    """Counts + span for the current filter set, to preview an export.

    Admin-only, deliberately cross-user. Pure aggregation - no rows leave the
    server. ``matched_sessions`` counts the distinct session ids in the matched
    events (so it tracks the filter, unlike the all-time sessions table).
    """
    filters = _event_filters(
        user_id=user_id,
        source=source,
        event_type=event_type,
        event_name=event_name,
        path_prefix=path_prefix,
        start=start,
        end=end,
        session_id=session_id,
    )

    matched_events = int(
        await session.scalar(_filtered(select(func.count()).select_from(AnalyticsEvent), filters))
        or 0
    )
    matched_sessions = int(
        await session.scalar(
            _filtered(select(func.count(func.distinct(AnalyticsEvent.session_id))), filters)
        )
        or 0
    )
    distinct_users = int(
        await session.scalar(
            _filtered(select(func.count(func.distinct(AnalyticsEvent.user_id))), filters)
        )
        or 0
    )
    date_min = await session.scalar(
        _filtered(select(func.min(AnalyticsEvent.occurred_at)), filters)
    )
    date_max = await session.scalar(
        _filtered(select(func.max(AnalyticsEvent.occurred_at)), filters)
    )
    type_rows = (
        await session.execute(
            _filtered(select(AnalyticsEvent.event_type, func.count()), filters)
            .group_by(AnalyticsEvent.event_type)
            .order_by(func.count().desc())
        )
    ).all()

    return ExportPreview(
        matched_events=matched_events,
        matched_sessions=matched_sessions,
        distinct_users=distinct_users,
        date_min=date_min,
        date_max=date_max,
        event_types=[ExportTypeCount(label=str(t), count=int(c)) for t, c in type_rows],
    )


class ExportUserOption(BaseModel):
    id: str
    email: str | None
    preferred_username: str | None


class ExportFacets(BaseModel):
    users: list[ExportUserOption]
    event_types: list[str]
    event_names: list[ExportTypeCount]
    paths: list[ExportTypeCount]


@router.get("/export/facets", response_model=ExportFacets)
async def export_facets(user: AdminUserDep, session: SessionDep) -> ExportFacets:
    """Dropdown options for the export filter UI.

    Admin-only, deliberately cross-user. Returns the users that have any
    behaviour events, the distinct event types, and the top event names / paths
    by frequency (capped) so the filter selects stay bounded.
    """
    user_rows = (
        await session.execute(
            select(User.id, User.email, User.preferred_username)
            .where(User.id.in_(select(func.distinct(AnalyticsEvent.user_id))))
            .order_by(User.preferred_username, User.email, User.id)
        )
    ).all()
    type_rows = (
        await session.execute(
            select(AnalyticsEvent.event_type)
            .group_by(AnalyticsEvent.event_type)
            .order_by(AnalyticsEvent.event_type)
        )
    ).all()
    name_rows = (
        await session.execute(
            select(AnalyticsEvent.event_name, func.count())
            .group_by(AnalyticsEvent.event_name)
            .order_by(func.count().desc())
            .limit(50)
        )
    ).all()
    path_rows = (
        await session.execute(
            select(AnalyticsEvent.path, func.count())
            .where(AnalyticsEvent.path.is_not(None))
            .group_by(AnalyticsEvent.path)
            .order_by(func.count().desc())
            .limit(50)
        )
    ).all()

    return ExportFacets(
        users=[
            ExportUserOption(id=str(uid), email=email, preferred_username=username)
            for uid, email, username in user_rows
        ],
        event_types=[str(t) for (t,) in type_rows],
        event_names=[ExportTypeCount(label=str(n), count=int(c)) for n, c in name_rows],
        paths=[ExportTypeCount(label=str(p), count=int(c)) for p, c in path_rows],
    )
