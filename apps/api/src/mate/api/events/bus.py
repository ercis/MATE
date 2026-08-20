"""In-process pub/sub event bus.

Two consumers in the platform process:

  - The frontend, via `GET /api/v1/events` (SSE, topic-filtered) and per-job
    `GET /api/v1/jobs/{id}/stream` - both implemented as bus subscribers.
  - Modules, via `ctx.bus` (phase 5) - module authors publish/subscribe
    Pydantic-typed events (§5.7).

Each subscriber is fed via its own bounded `asyncio.Queue` so a slow consumer
can never block the publisher or other consumers. When the queue is full we
drop the oldest event - better to lose a `job.progress` tick than stall the
whole pipeline. Cumulative drops are logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

log = structlog.get_logger(__name__)

DEFAULT_QUEUE_MAXSIZE = 256


@dataclass(frozen=True)
class EventEnvelope:
    topic: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {"topic": self.topic, "payload": self.payload, "ts": self.ts}


def _topic_matches(pattern: str, topic: str) -> bool:
    if pattern == "*" or pattern == "**":
        return True
    p_segs = pattern.split(".")
    t_segs = topic.split(".")
    for idx, seg in enumerate(p_segs):
        if seg == "**":
            return True
        if idx >= len(t_segs):
            return False
        if seg == "*":
            continue
        if seg != t_segs[idx]:
            return False
    # `job.*` should match `job.queued` (3 vs 2 segments => OK because we want
    # prefix-matching with single-segment wildcard) and also `job.queued.x`.
    return len(t_segs) >= len(p_segs)


@dataclass
class _Subscription:
    patterns: tuple[str, ...]
    queue: asyncio.Queue[EventEnvelope]
    drops: int = 0


class EventSchemaError(ValueError):
    """Raised by `EventBus.publish` when a payload fails the registered
    Pydantic schema for its topic (§5.7a). Surfacing fast at the publish
    site keeps producer bugs out of subscriber queues - subscribers only
    ever see validated payloads.
    """


class EventBus:
    def __init__(self) -> None:
        self._subs: list[_Subscription] = []
        self._lock = asyncio.Lock()
        # Topic → Pydantic model. Modules register via `events.py` at load
        # time (see loader._register_module_events); the platform-emitted
        # `job.*` and `log.imported` topics are left unregistered so they
        # don't need to round-trip through Pydantic.
        self._schemas: dict[str, type[BaseModel]] = {}

    def register_schema(self, topic: str, model: type[BaseModel]) -> None:
        """Register a Pydantic model for `topic`. Exact-match only - wildcard
        patterns are deliberately unsupported here (they'd make multi-match
        ambiguous). Re-registering the same topic with a different model
        raises.
        """
        existing = self._schemas.get(topic)
        if existing is not None and existing is not model:
            raise EventSchemaError(
                f"Topic {topic!r} already has a schema registered: {existing!r}."
            )
        self._schemas[topic] = model

    # Platform routing keys that live alongside a module's domain payload.
    # They are preserved across schema validation so a module-authored schema
    # that doesn't model them can't strip them - the WS fan-out and the
    # loader's @on_event dispatch both key on `user_id` for tenant isolation.
    _RESERVED_KEYS = ("user_id", "log_id")

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        schema = self._schemas.get(topic)
        if schema is not None:
            reserved = {k: payload[k] for k in self._RESERVED_KEYS if k in payload}
            try:
                payload = schema.model_validate(payload).model_dump()
            except ValidationError as exc:
                raise EventSchemaError(
                    f"Event {topic!r} payload failed schema validation: {exc}"
                ) from exc
            # Re-attach without clobbering a value the schema legitimately set.
            for key, value in reserved.items():
                payload.setdefault(key, value)
        envelope = EventEnvelope(topic=topic, payload=payload)
        async with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if not any(_topic_matches(p, topic) for p in sub.patterns):
                continue
            try:
                sub.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                try:
                    _ = sub.queue.get_nowait()
                    sub.queue.put_nowait(envelope)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                sub.drops += 1
                if sub.drops == 1 or sub.drops % 100 == 0:
                    log.warning(
                        "event_bus.subscriber_dropping",
                        patterns=sub.patterns,
                        drops=sub.drops,
                    )

    @contextlib.asynccontextmanager
    async def subscribe(
        self,
        patterns: Iterable[str] = ("*",),
        *,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> AsyncIterator[AsyncIterator[EventEnvelope]]:
        """Yield an async iterator of envelopes matching any of `patterns`."""
        pat_tuple = tuple(patterns) or ("*",)
        sub = _Subscription(patterns=pat_tuple, queue=asyncio.Queue(maxsize=queue_maxsize))

        async with self._lock:
            self._subs.append(sub)

        async def _stream() -> AsyncIterator[EventEnvelope]:
            while True:
                yield await sub.queue.get()

        try:
            yield _stream()
        finally:
            async with self._lock:
                with contextlib.suppress(ValueError):
                    self._subs.remove(sub)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    if _bus is None:
        raise RuntimeError("Event bus is not initialised - startup did not run.")
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus
