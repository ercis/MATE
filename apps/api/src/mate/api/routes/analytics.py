"""/api/v1/usage - opt-in user behaviour tracking.

The single ``UserSetting`` row under key ``analytics.config`` is the source of
truth for whether capture is on. The frontend gate is a best-effort UX
shortcut; the server still rejects ``POST /events`` when disabled so a stale
client cannot leak.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from mate.api.auth import CurrentUserDep
from mate.api.config import get_settings
from mate.api.db.models import (
    AnalyticsEvent,
    AnalyticsObject,
    AnalyticsObjectRelation,
    AnalyticsSession,
    UserSetting,
)
from mate.api.db.session import SessionDep
from mate.api.schemas.common import UtcDateTime, utc_isoformat
from mate.api.services.analytics_objects import (
    ObjectRef,
    derive_client_objects,
    derive_server_objects,
    persist_event_objects,
)

log = structlog.get_logger(__name__)
# Path deliberately neutral (`/usage` instead of `/analytics`) so default
# ad-blocker filter lists (EasyPrivacy etc.) don't drop our requests with
# `net::ERR_BLOCKED_BY_CLIENT`.
router = APIRouter(prefix="/usage", tags=["usage"])

ANALYTICS_CONFIG_KEY = "analytics.config"

MAX_BATCH_EVENTS = 500
MAX_BATCH_BYTES = 256 * 1024


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


OnboardingMode = Literal["force", "on", "off"]


class AnalyticsConfigPayload(BaseModel):
    enabled: bool = False
    retention_days: int | None = None
    capture_clicks: bool = True
    capture_perf: bool = True
    capture_errors: bool = True
    # UI-log capture kinds (Abb & Rehse reference model). Input values are
    # always hard-redacted for password/opted-out fields regardless of the
    # flag; keyboard capture is combos + special keys only, never plain typing.
    capture_inputs: bool = True
    capture_keyboard: bool = True
    capture_pointer: bool = True
    opted_in_at: UtcDateTime | None = None
    anon_user_id_seed: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Read-only: surfaced from the USER_TRACKING_ONBOARDING env var so the
    # frontend can pick the onboarding default and hide the privacy step/tab
    # under `force`. Never persisted (excluded in ``_save_config``) and any
    # client-supplied value on PUT is ignored (overwritten by ``_effective``).
    onboarding_mode: OnboardingMode = "on"


def _load_config(row: UserSetting | None) -> AnalyticsConfigPayload:
    if row is None or not isinstance(row.value_json, dict):
        return AnalyticsConfigPayload()
    return AnalyticsConfigPayload.model_validate(row.value_json)


def _effective(cfg: AnalyticsConfigPayload) -> AnalyticsConfigPayload:
    """Overlay the server tracking policy onto a stored/loaded config.

    ``onboarding_mode`` always reflects ``USER_TRACKING_ONBOARDING`` rather
    than anything the client stored. Under ``force`` tracking is enabled
    unconditionally - the user cannot opt out, so the stored ``enabled`` flag
    is irrelevant and we report (and gate ingestion on) ``True``.
    """
    mode = get_settings().user_tracking_onboarding
    update: dict[str, Any] = {"onboarding_mode": mode}
    if mode == "force":
        update["enabled"] = True
    return cfg.model_copy(update=update)


async def _save_config(
    session: SessionDep, cfg: AnalyticsConfigPayload, user_id: str
) -> AnalyticsConfigPayload:
    row = await session.get(UserSetting, (user_id, ANALYTICS_CONFIG_KEY))
    # ``onboarding_mode`` is server policy, not user state - never persist it.
    data = cfg.model_dump(mode="json", exclude={"onboarding_mode"})
    if row is None:
        session.add(UserSetting(user_id=user_id, key=ANALYTICS_CONFIG_KEY, value_json=data))
    else:
        row.value_json = data
    await session.commit()
    _consent_cache.pop(user_id, None)
    return cfg


# Short-lived per-user consent cache so the all-requests usage middleware and
# the batched server-event writer don't pay one ``UserSetting`` SELECT per
# recorded event. Invalidated on every config save/wipe; the TTL bounds
# staleness for cross-process changes (single-process deployment in practice).
_CONSENT_TTL_SECONDS = 60.0
_consent_cache: dict[str, tuple[AnalyticsConfigPayload, float]] = {}


async def cached_config(session: AsyncSession, user_id: str) -> AnalyticsConfigPayload:
    """Effective analytics config for ``user_id`` with a small TTL cache."""
    now = time.monotonic()
    hit = _consent_cache.get(user_id)
    if hit is not None and hit[1] > now:
        return hit[0]
    row = await session.get(UserSetting, (user_id, ANALYTICS_CONFIG_KEY))
    cfg = _effective(_load_config(row))
    _consent_cache[user_id] = (cfg, now + _CONSENT_TTL_SECONDS)
    return cfg


@router.get("/config", response_model=AnalyticsConfigPayload)
async def get_config(session: SessionDep, user: CurrentUserDep) -> AnalyticsConfigPayload:
    row = await session.get(UserSetting, (user.id, ANALYTICS_CONFIG_KEY))
    cfg = _load_config(row)
    # Persist the lazily-generated seed so the anon id is stable across calls.
    if row is None:
        await _save_config(session, cfg, user.id)
    return _effective(cfg)


@router.put("/config", response_model=AnalyticsConfigPayload)
async def put_config(
    payload: AnalyticsConfigPayload, session: SessionDep, user: CurrentUserDep
) -> AnalyticsConfigPayload:
    if payload.enabled and payload.opted_in_at is None:
        payload = payload.model_copy(update={"opted_in_at": datetime.now(UTC).replace(tzinfo=None)})
    saved = await _save_config(session, payload, user.id)
    return _effective(saved)


# --------------------------------------------------------------------------
# Server-side events (business-op timings, job outcomes)
# --------------------------------------------------------------------------


async def record_server_event(
    session: AsyncSession,
    *,
    user_id: str,
    event_name: str,
    event_type: str = "operation",
    path: str | None = None,
    duration_ms: int | None = None,
    properties: dict[str, Any] | None = None,
    objects: list[ObjectRef] | None = None,
) -> None:
    """Append a backend-emitted analytics event, gated by the user's consent.

    No-op when the user's tracking config is disabled (so it respects opt-out
    under ``on``/``off`` and is always-on under ``force``). Wrapped so a
    tracking failure can never break the request or job it describes. Commits
    on the passed session - callers should hand it a session they own.

    ``objects`` lets callers attach extra OCEL object refs (e.g. the job a
    ``job`` event describes) beyond the ones derived from path/properties.
    """
    try:
        cfg = await cached_config(session, user_id)
        if not cfg.enabled:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        event = AnalyticsEvent(
            user_id=user_id,
            # Backend events have no browser session; a sentinel keeps the
            # NOT NULL column satisfied and lets queries filter them out.
            session_id="server",
            anon_user_id=cfg.anon_user_id_seed,
            source="server",
            event_type=event_type[:32],
            event_name=event_name[:128],
            duration_ms=duration_ms,
            path=(path or None) and path[:512],
            properties=properties,
            occurred_at=now,
            server_received_at=now,
        )
        session.add(event)
        await session.flush()
        refs, relations = derive_server_objects(
            path=path,
            properties=properties,
            anon_user_id=cfg.anon_user_id_seed,
            extra=objects,
        )
        await persist_event_objects(
            session,
            user_id=user_id,
            event_refs=[(event.id, refs)],
            relations=relations,
        )
        await session.commit()
    except Exception:
        log.warning("server_event.record_failed", event_name=event_name, exc_info=True)
        with contextlib.suppress(Exception):
            await session.rollback()


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


EventType = Literal[
    "page",
    "click",
    "custom",
    "error",
    "perf",
    "form",
    # UI-log action families (Abb & Rehse model): committed input values,
    # keyboard combos, sampled pointer traces/scrolls, clipboard ops,
    # drag/drop, and viewport/visibility changes.
    "input",
    "key",
    "pointer",
    "clipboard",
    "drag",
    "view",
]

# Server-side re-truncation caps - the client already caps these, but the
# ingest endpoint is authenticated-public so the server enforces its own
# bounds. Keys not listed fall back to the generic cap.
_PROP_CAPS = {"selector": 400, "input_value": 256, "text": 160, "activity": 200}
_PROP_GENERIC_CAP = 2000
_PROP_MAX_POINTS = 60


def _truncate_props(props: dict[str, Any] | None) -> dict[str, Any] | None:
    """Defensively bound every string (and the pointer-trace point list)."""
    if not isinstance(props, dict):
        return None

    def _bound(key: str, value: Any) -> Any:
        if isinstance(value, str):
            return value[: _PROP_CAPS.get(key, _PROP_GENERIC_CAP)]
        if isinstance(value, dict):
            return {k: _bound(k, v) for k, v in value.items()}
        if isinstance(value, list):
            capped = value[:_PROP_MAX_POINTS] if key == "points" else value[:100]
            return [_bound(key, v) for v in capped]
        return value

    return {k: _bound(k, v) for k, v in props.items()}


class IngestEvent(BaseModel):
    event_type: EventType
    event_name: str
    occurred_at: datetime | None = None
    path: str | None = None
    referrer: str | None = None
    properties: dict[str, Any] | None = None


class IngestSession(BaseModel):
    id: str
    anon_user_id: str
    started_at: datetime
    entry_path: str | None = None
    viewport_w: int | None = None
    viewport_h: int | None = None
    ua_class: str | None = None
    locale: str | None = None
    tz: str | None = None


class IngestPayload(BaseModel):
    session: IngestSession
    events: list[IngestEvent]


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(request: Request, session: SessionDep, user: CurrentUserDep) -> Response:
    """Append a batch of events.

    Accepts ``application/json`` and ``text/plain`` (so ``navigator.sendBeacon``
    works without triggering a CORS preflight). Rejects with 204 if analytics
    is disabled - this is the privacy safety net independent of the client.
    """
    cfg = await cached_config(session, user.id)
    if not cfg.enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body")
    if len(raw) > MAX_BATCH_BYTES:
        raise HTTPException(status_code=413, detail="Batch too large")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    payload = IngestPayload.model_validate(body)
    if not payload.events:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    if len(payload.events) > MAX_BATCH_EVENTS:
        raise HTTPException(status_code=413, detail=f"Batch exceeds {MAX_BATCH_EVENTS} events")

    # Reject events claiming a different anon id than the configured seed -
    # prevents replay from clients with stale state after a wipe.
    if payload.session.anon_user_id != cfg.anon_user_id_seed:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    now = datetime.now(UTC).replace(tzinfo=None)

    sess_row = await session.get(AnalyticsSession, payload.session.id)
    if sess_row is not None and sess_row.user_id != user.id:
        # Another user's session id collision - refuse silently.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Pointer traces are excluded from the session's event_count so "avg
    # events per session" stays a measure of discrete interactions, not of
    # how long the mouse moved.
    counted = sum(1 for e in payload.events if e.event_type != "pointer")
    if sess_row is None:
        sess_row = AnalyticsSession(
            id=payload.session.id,
            user_id=user.id,
            anon_user_id=payload.session.anon_user_id,
            started_at=_naive(payload.session.started_at),
            last_seen_at=now,
            entry_path=payload.session.entry_path,
            event_count=counted,
        )
        session.add(sess_row)
    else:
        sess_row.last_seen_at = now
        sess_row.event_count = (sess_row.event_count or 0) + counted

    rows = [
        AnalyticsEvent(
            user_id=user.id,
            session_id=payload.session.id,
            anon_user_id=payload.session.anon_user_id,
            source="client",
            event_type=e.event_type,
            event_name=e.event_name[:128],
            path=(e.path or None) and e.path[:512],
            referrer=(e.referrer or None) and e.referrer[:512],
            properties=_truncate_props(e.properties),
            viewport_w=payload.session.viewport_w,
            viewport_h=payload.session.viewport_h,
            ua_class=payload.session.ua_class,
            locale=payload.session.locale,
            tz=payload.session.tz,
            occurred_at=_naive(e.occurred_at) if e.occurred_at else now,
            server_received_at=now,
        )
        for e in payload.events
    ]
    session.add_all(rows)
    # Materialise the OCEL object layer (Abb & Rehse model): flush for event
    # ids, then upsert ui_element/ui_group/application/system/user/task rows,
    # their part_of hierarchy, and the event->object links.
    await session.flush()
    event_refs: list[tuple[int, list[ObjectRef]]] = []
    all_relations: set[tuple[str, str, str]] = set()
    for row in rows:
        refs, relations = derive_client_objects(
            path=row.path,
            properties=row.properties,
            anon_user_id=payload.session.anon_user_id,
            ua_class=payload.session.ua_class,
        )
        event_refs.append((row.id, refs))
        all_relations |= relations
    await persist_event_objects(
        session, user_id=user.id, event_refs=event_refs, relations=all_relations
    )
    await session.commit()
    return Response(status_code=status.HTTP_202_ACCEPTED)


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


# --------------------------------------------------------------------------
# Summary / export / wipe
# --------------------------------------------------------------------------


class TypeCount(BaseModel):
    event_type: str
    count: int


class AnalyticsSummary(BaseModel):
    enabled: bool
    total_events: int
    total_sessions: int
    sessions_last_30d: int
    oldest_event: UtcDateTime | None
    newest_event: UtcDateTime | None
    by_type: list[TypeCount]


@router.get("/summary", response_model=AnalyticsSummary)
async def get_summary(session: SessionDep, user: CurrentUserDep) -> AnalyticsSummary:
    cfg_row = await session.get(UserSetting, (user.id, ANALYTICS_CONFIG_KEY))
    cfg = _effective(_load_config(cfg_row))

    total_events = (
        await session.scalar(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.user_id == user.id)
        )
    ) or 0
    total_sessions = (
        await session.scalar(
            select(func.count())
            .select_from(AnalyticsSession)
            .where(AnalyticsSession.user_id == user.id)
        )
    ) or 0
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    sessions_30d = (
        await session.scalar(
            select(func.count())
            .select_from(AnalyticsSession)
            .where(
                AnalyticsSession.user_id == user.id,
                AnalyticsSession.last_seen_at >= cutoff,
            )
        )
    ) or 0

    oldest = await session.scalar(
        select(func.min(AnalyticsEvent.occurred_at)).where(AnalyticsEvent.user_id == user.id)
    )
    newest = await session.scalar(
        select(func.max(AnalyticsEvent.occurred_at)).where(AnalyticsEvent.user_id == user.id)
    )

    by_type_rows = (
        await session.execute(
            select(AnalyticsEvent.event_type, func.count())
            .where(AnalyticsEvent.user_id == user.id)
            .group_by(AnalyticsEvent.event_type)
            .order_by(func.count().desc())
        )
    ).all()

    return AnalyticsSummary(
        enabled=cfg.enabled,
        total_events=int(total_events),
        total_sessions=int(total_sessions),
        sessions_last_30d=int(sessions_30d),
        oldest_event=oldest,
        newest_event=newest,
        by_type=[TypeCount(event_type=t, count=int(c)) for t, c in by_type_rows],
    )


class WipeResponse(BaseModel):
    deleted_events: int
    deleted_sessions: int
    new_anon_user_id_seed: str


@router.delete("/sync", response_model=WipeResponse)
async def wipe_events(session: SessionDep, user: CurrentUserDep) -> WipeResponse:
    events_deleted = (
        await session.scalar(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.user_id == user.id)
        )
    ) or 0
    sessions_deleted = (
        await session.scalar(
            select(func.count())
            .select_from(AnalyticsSession)
            .where(AnalyticsSession.user_id == user.id)
        )
    ) or 0
    await session.execute(delete(AnalyticsEvent).where(AnalyticsEvent.user_id == user.id))
    await session.execute(delete(AnalyticsSession).where(AnalyticsSession.user_id == user.id))
    # E2O rows cascade with their events; the object registry + O2O hierarchy
    # are user-keyed and need explicit deletes.
    await session.execute(delete(AnalyticsObject).where(AnalyticsObject.user_id == user.id))
    await session.execute(
        delete(AnalyticsObjectRelation).where(AnalyticsObjectRelation.user_id == user.id)
    )

    cfg_row = await session.get(UserSetting, (user.id, ANALYTICS_CONFIG_KEY))
    cfg = _load_config(cfg_row)
    cfg = cfg.model_copy(update={"anon_user_id_seed": str(uuid.uuid4())})
    await _save_config(session, cfg, user.id)

    return WipeResponse(
        deleted_events=int(events_deleted),
        deleted_sessions=int(sessions_deleted),
        new_anon_user_id_seed=cfg.anon_user_id_seed,
    )


def event_to_dict(ev: AnalyticsEvent) -> dict[str, Any]:
    """Flatten one ``AnalyticsEvent`` row into a JSON-serialisable dict.

    The single source of truth for the export row shape - reused by the per-user
    NDJSON dump here and the admin cross-user NDJSON/CSV exports
    (``routes/admin.py``) so the two never drift. ``user_id`` is included because
    admin exports span users; the per-user export simply emits its own id.
    """
    return {
        "id": ev.id,
        "user_id": ev.user_id,
        "session_id": ev.session_id,
        "anon_user_id": ev.anon_user_id,
        "source": ev.source,
        "event_type": ev.event_type,
        "event_name": ev.event_name,
        "duration_ms": ev.duration_ms,
        "path": ev.path,
        "referrer": ev.referrer,
        "properties": ev.properties,
        "viewport_w": ev.viewport_w,
        "viewport_h": ev.viewport_h,
        "ua_class": ev.ua_class,
        "locale": ev.locale,
        "tz": ev.tz,
        "occurred_at": utc_isoformat(ev.occurred_at),
        "server_received_at": utc_isoformat(ev.server_received_at),
    }


ExportFormat = Literal["ndjson", "ocel-json", "ocel-sqlite", "ocel-xml"]


@router.get("/export")
async def export_events(
    session: SessionDep, user: CurrentUserDep, format: ExportFormat = "ndjson"
) -> Response:
    """Dump the caller's UI log - flat NDJSON (default) or object-centric OCEL 2.0.

    The OCEL variants (Abb & Rehse reference model, ocel-standard.org 2.0
    JSON/SQLite/XML) are written by pm4py to a private temp file that is
    deleted once the download finishes; the same library reads them back, so an
    export re-imports into Mate as an OCEL event log.
    """
    if format != "ndjson":
        from mate.api.services import analytics_ocel

        fmt = format.removeprefix("ocel-")
        assert fmt in ("json", "sqlite", "xml")
        filters: list[ColumnElement[bool]] = [AnalyticsEvent.user_id == user.id]
        frame = await analytics_ocel.load_ui_log(session, filters)
        if not frame.events:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No events to export")
        path = await run_in_threadpool(
            lambda: analytics_ocel.write_ocel_tmp(analytics_ocel.build_ocel(frame), fmt)
        )
        return FileResponse(
            path,
            media_type=analytics_ocel.media_type(fmt),
            filename=analytics_ocel.download_name(fmt),
            background=BackgroundTask(_unlink_quiet, path),
        )

    rows = (
        (
            await session.execute(
                select(AnalyticsEvent)
                .where(AnalyticsEvent.user_id == user.id)
                .order_by(AnalyticsEvent.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )

    def _gen() -> Any:
        for r in rows:
            yield json.dumps(event_to_dict(r), default=str) + "\n"

    return StreamingResponse(
        _gen(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="analytics-export.ndjson"'},
    )


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


# --------------------------------------------------------------------------
# Retention sweeper - called from main.py lifespan loop
# --------------------------------------------------------------------------


async def prune_expired(session: SessionDep) -> int:
    """Delete events + sessions older than each user's configured retention.

    Returns the number of event rows removed across all users. Called from the
    daily sweeper task in ``main.py``; safe to invoke ad-hoc from tests too.
    """
    cfg_rows = (
        (await session.execute(select(UserSetting).where(UserSetting.key == ANALYTICS_CONFIG_KEY)))
        .scalars()
        .all()
    )
    total_removed = 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for cfg_row in cfg_rows:
        cfg = _load_config(cfg_row)
        if not cfg.retention_days or cfg.retention_days <= 0:
            continue
        cutoff = now - timedelta(days=cfg.retention_days)
        result = await session.execute(
            delete(AnalyticsEvent).where(
                AnalyticsEvent.user_id == cfg_row.user_id,
                AnalyticsEvent.occurred_at < cutoff,
            )
        )
        await session.execute(
            delete(AnalyticsSession).where(
                AnalyticsSession.user_id == cfg_row.user_id,
                AnalyticsSession.last_seen_at < cutoff,
            )
        )
        # Objects unseen for the whole window carry no events any more (their
        # E2O rows cascaded away with the pruned events) - drop them plus any
        # hierarchy rows that now reference a vanished object.
        await session.execute(
            delete(AnalyticsObject).where(
                AnalyticsObject.user_id == cfg_row.user_id,
                AnalyticsObject.last_seen_at < cutoff,
            )
        )
        alive = select(AnalyticsObject.object_id).where(AnalyticsObject.user_id == cfg_row.user_id)
        await session.execute(
            delete(AnalyticsObjectRelation).where(
                AnalyticsObjectRelation.user_id == cfg_row.user_id,
                AnalyticsObjectRelation.src_object_id.not_in(alive),
            )
        )
        total_removed += int(result.rowcount or 0)
    await session.commit()
    return total_removed
