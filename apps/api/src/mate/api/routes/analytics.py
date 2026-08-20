"""/api/v1/usage - opt-in user behaviour tracking.

The single ``UserSetting`` row under key ``analytics.config`` is the source of
truth for whether capture is on. The frontend gate is a best-effort UX
shortcut; the server still rejects ``POST /events`` when disabled so a stale
client cannot leak.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import CurrentUserDep
from mate.api.config import get_settings
from mate.api.db.models import (
    AnalyticsEvent,
    AnalyticsSession,
    UserSetting,
)
from mate.api.db.session import SessionDep

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
    opted_in_at: datetime | None = None
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
) -> None:
    """Append a backend-emitted analytics event, gated by the user's consent.

    No-op when the user's tracking config is disabled (so it respects opt-out
    under ``on``/``off`` and is always-on under ``force``). Wrapped so a
    tracking failure can never break the request or job it describes. Commits
    on the passed session - callers should hand it a session they own.
    """
    try:
        cfg_row = await session.get(UserSetting, (user_id, ANALYTICS_CONFIG_KEY))
        cfg = _effective(_load_config(cfg_row))
        if not cfg.enabled:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        session.add(
            AnalyticsEvent(
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
        )
        await session.commit()
    except Exception:
        log.warning("server_event.record_failed", event_name=event_name, exc_info=True)
        with contextlib.suppress(Exception):
            await session.rollback()


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


EventType = Literal["page", "click", "custom", "error", "perf", "form"]


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
    cfg_row = await session.get(UserSetting, (user.id, ANALYTICS_CONFIG_KEY))
    cfg = _effective(_load_config(cfg_row))
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
    if sess_row is None:
        sess_row = AnalyticsSession(
            id=payload.session.id,
            user_id=user.id,
            anon_user_id=payload.session.anon_user_id,
            started_at=_naive(payload.session.started_at),
            last_seen_at=now,
            entry_path=payload.session.entry_path,
            event_count=len(payload.events),
        )
        session.add(sess_row)
    else:
        sess_row.last_seen_at = now
        sess_row.event_count = (sess_row.event_count or 0) + len(payload.events)

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
            properties=e.properties,
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
    oldest_event: datetime | None
    newest_event: datetime | None
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
        "occurred_at": ev.occurred_at.isoformat(),
        "server_received_at": ev.server_received_at.isoformat(),
    }


@router.get("/export")
async def export_events(session: SessionDep, user: CurrentUserDep) -> StreamingResponse:
    """NDJSON dump of every event row, oldest first."""
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
        total_removed += int(result.rowcount or 0)
    await session.commit()
    return total_removed
