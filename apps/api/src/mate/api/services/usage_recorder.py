"""Batched writer for server-emitted usage events.

The all-requests ``UsageTrackingMiddleware`` would cost one consent SELECT plus
one write transaction per API call if it recorded synchronously. Instead it
enqueues a draft here (pure in-memory, nothing on the request path) and a
single background task drains the queue every couple of seconds in one
transaction - mirroring ``main._job_event_recorder_loop``'s
fire-and-forget posture: tracking failures are logged and dropped, never
surfaced to the request they describe.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import AnalyticsEvent
from mate.api.services.analytics_objects import (
    ObjectRef,
    derive_server_objects,
    persist_event_objects,
)

log = structlog.get_logger("usage.recorder")

_MAX_PENDING = 10_000
_DRAIN_INTERVAL_SECONDS = 2.0
# Bound one drain transaction; leftovers are picked up by the next tick.
_DRAIN_BATCH = 500


@dataclass(frozen=True)
class ServerEventDraft:
    user_id: str
    event_type: str
    event_name: str
    path: str | None
    duration_ms: int | None
    properties: dict[str, Any] | None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


_pending: deque[ServerEventDraft] = deque(maxlen=_MAX_PENDING)
_wakeup = asyncio.Event()


def enqueue_server_event(draft: ServerEventDraft) -> None:
    """Queue one draft for the background writer. Sync, alloc-only, never raises."""
    _pending.append(draft)
    if len(_pending) >= _DRAIN_BATCH:
        _wakeup.set()


async def _drain_once() -> int:
    """Write up to ``_DRAIN_BATCH`` queued drafts in one transaction."""
    # Imported here (not at module top) to avoid a routes<->services cycle:
    # routes.analytics already imports services.analytics_objects.
    from mate.api.routes.analytics import cached_config

    batch: list[ServerEventDraft] = []
    while _pending and len(batch) < _DRAIN_BATCH:
        batch.append(_pending.popleft())
    if not batch:
        return 0

    sm = get_sessionmaker()
    written = 0
    async with sm() as session:
        rows: list[tuple[AnalyticsEvent, ServerEventDraft, str]] = []
        for draft in batch:
            cfg = await cached_config(session, draft.user_id)
            if not cfg.enabled:
                continue
            event = AnalyticsEvent(
                user_id=draft.user_id,
                session_id="server",
                anon_user_id=cfg.anon_user_id_seed,
                source="server",
                event_type=draft.event_type[:32],
                event_name=draft.event_name[:128],
                duration_ms=draft.duration_ms,
                path=(draft.path or None) and draft.path[:512],
                properties=draft.properties,
                occurred_at=draft.occurred_at,
                server_received_at=datetime.now(UTC).replace(tzinfo=None),
            )
            session.add(event)
            rows.append((event, draft, cfg.anon_user_id_seed))
        if not rows:
            return 0
        await session.flush()
        # Group the object upserts per user - persist_event_objects is
        # user-scoped like every analytics table.
        by_user: dict[str, list[tuple[int, list[ObjectRef]]]] = {}
        for event, draft, seed in rows:
            refs, _ = derive_server_objects(
                path=draft.path, properties=draft.properties, anon_user_id=seed
            )
            by_user.setdefault(draft.user_id, []).append((event.id, refs))
        for user_id, event_refs in by_user.items():
            await persist_event_objects(
                session, user_id=user_id, event_refs=event_refs, relations=set()
            )
        await session.commit()
        written = len(rows)
    return written


async def server_event_writer_loop() -> None:
    """Drain the draft queue every ``_DRAIN_INTERVAL_SECONDS`` (or when full)."""
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_wakeup.wait(), timeout=_DRAIN_INTERVAL_SECONDS)
        _wakeup.clear()
        try:
            while await _drain_once() > 0:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("usage.recorder.drain_failed", error=str(exc))


async def flush_pending_for_tests() -> None:
    """Drain everything synchronously - deterministic hook for the test suite."""
    while await _drain_once() > 0:
        pass
