"""Server-side usage tracking for a curated set of business operations.

Implemented as a *pure ASGI* middleware rather than Starlette's
``BaseHTTPMiddleware`` on purpose: BaseHTTPMiddleware buffers the response body,
which breaks the streaming AI endpoints (``/ai/chat``, ``/ai/guidance/.../stream``).
The pure-ASGI form is transparent to streaming and, because ``await self.app(...)``
only returns once the whole response (including a streamed body) has been sent,
the measured duration covers the *entire* operation - e.g. a full AI completion.

We only record a curated allowlist of meaningful, synchronous operations.
Long-running work (imports, module runs, installs) goes through the job runtime
and is captured separately as ``job`` events with their real runtime duration -
see ``main._job_event_recorder_loop``.
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog

from mate.api.db.engine import get_sessionmaker
from mate.api.routes.analytics import record_server_event

log = structlog.get_logger(__name__)

# (HTTP method, path regex, operation name). Matched against the raw request
# path (no query string). Keep this list to genuinely meaningful actions -
# every match is one row kept for the user's full retention window.
_BUSINESS_OPS: list[tuple[str, re.Pattern[str], str]] = [
    ("POST", re.compile(r"^/api/v1/ai/chat$"), "ai_chat"),
    ("POST", re.compile(r"^/api/v1/ai/guidance/"), "ai_guidance"),
    ("GET", re.compile(r"^/api/v1/admin/export/metadata-db$"), "admin_db_export"),
    ("GET", re.compile(r"^/api/v1/usage/export$"), "analytics_export"),
    ("DELETE", re.compile(r"^/api/v1/event-logs/[^/]+$"), "process_deleted"),
    ("DELETE", re.compile(r"^/api/v1/modules/[^/]+$"), "module_uninstalled"),
    ("DELETE", re.compile(r"^/api/v1/ai/guidance/module/[^/]+$"), "ai_guidance_cleared"),
]


def _match_op(method: str, path: str) -> str | None:
    for m, rx, name in _BUSINESS_OPS:
        if m == method and rx.match(path):
            return name
    return None


class UsageTrackingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        op = _match_op(scope.get("method", ""), scope.get("path", ""))
        if op is None:
            await self.app(scope, receive, send)
            return

        status_code: dict[str, int | None] = {"code": None}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_code["code"] = message["status"]
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            # ``get_current_user`` stamps this onto the shared ASGI scope state.
            state = scope.get("state") or {}
            user_id = state.get("user_id")
            if user_id:
                await self._record(scope, op, user_id, status_code["code"], duration_ms)

    async def _record(
        self, scope: Any, op: str, user_id: str, status: int | None, duration_ms: int
    ) -> None:
        try:
            sm = get_sessionmaker()
            async with sm() as session:
                await record_server_event(
                    session,
                    user_id=user_id,
                    event_name=op,
                    event_type="operation",
                    path=scope.get("path"),
                    duration_ms=duration_ms,
                    properties={"method": scope.get("method"), "status": status},
                )
        except Exception:
            # Tracking must never surface to / break the response it describes.
            log.warning("usage_middleware.record_failed", op=op, exc_info=True)
