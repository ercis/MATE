"""`GET /api/v1/events` - topic-filtered platform-wide stream (§7.9.5).

Server-Sent Events, **not** WebSocket. The production proxy chain (uni edge
proxy → Caddy → api) carries HTTP streaming transparently but drops WebSocket
upgrades: a WS handshake reaches the API as a plain `GET` with the upgrade
headers stripped, so the WS-only route 404s and the live feed silently dies.
SSE rides the exact same path Mate AI streaming already uses successfully - see
``infra/caddy/Caddyfile`` and ``apps/api/src/mate/api/routes/ai.py``.

Query params:

  - `topic` (repeatable) - bus pattern(s) to subscribe to. Defaults to `*`.
    Examples: `?topic=job.*`, `?topic=job.completed&topic=job.failed`.

Auth is the standard ``Authorization: Bearer`` header (the browser client uses
``fetch`` streaming, which - unlike ``EventSource``/``WebSocket`` - can set
headers), so the token no longer rides in the URL where it would leak into the
access logs.

The frontend opens one of these per session for toasts + drawer updates; the
high-frequency per-job feed is the separate ``GET /jobs/{id}/stream`` next door.

Envelopes whose payload carries a ``user_id`` distinct from the connected
user's are filtered out - that's how per-user isolation is enforced.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from mate.api.auth import CurrentUserDep
from mate.api.events import get_event_bus

router = APIRouter(tags=["events"])

# Emit an SSE comment after this many idle seconds so intermediary proxies don't
# treat a quiet stream as dead and close it. A `job.progress` tick is usually
# far more frequent, but the stream can sit silent for minutes between jobs.
_HEARTBEAT_S = 15.0

# Headers that keep proxies from buffering the stream (mirrors the AI route).
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sse(envelope: dict[str, Any]) -> str:
    return f"data: {json.dumps(envelope, default=_json_default)}\n\n"


@router.get("/events")
async def stream_events(
    user: CurrentUserDep,
    topic: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    bus = get_event_bus()
    topics = topic or ["*"]

    async def _gen() -> AsyncIterator[str]:
        # `async with` lives inside the generator so the bus subscription is torn
        # down the moment the client disconnects (the generator is closed).
        async with bus.subscribe(topics) as stream:
            while True:
                try:
                    env = await asyncio.wait_for(anext(stream), _HEARTBEAT_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                except StopAsyncIteration:
                    return
                # Filter cross-user events. System-emitted envelopes (no
                # `user_id` in payload) are always forwarded - they're
                # operator-level, never user data.
                env_user = env.payload.get("user_id")
                if env_user is not None and env_user != user.id:
                    continue
                yield _sse(env.to_json())

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
