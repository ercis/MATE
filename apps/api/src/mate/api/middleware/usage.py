"""Server-side usage tracking for every authenticated API request.

Implemented as a *pure ASGI* middleware rather than Starlette's
``BaseHTTPMiddleware`` on purpose: BaseHTTPMiddleware buffers the response body,
which breaks the streaming AI endpoints (``/ai/chat``, ``/ai/guidance/.../stream``).
The pure-ASGI form is transparent to streaming and, because ``await self.app(...)``
only returns once the whole response (including a streamed body) has been sent,
the measured duration covers the *entire* operation - e.g. a full AI completion.

Every authenticated request becomes one ``operation`` event named after the
route template (``/api/v1/event-logs/{log_id}``), so the backend side of the
UI log is complete - the paper's "computer-initiated"/server perspective. A
small curated list keeps friendlier names for headline business ops. Noise
sources (the ingest endpoint itself, SSE streams, the 10s-polling admin
insights, docs/health) are excluded. Nothing touches the DB on the request
path: drafts go to ``services.usage_recorder``'s batched background writer,
which also checks consent per user. Long-running work still arrives via the
job runtime as ``job`` events - see ``main._job_event_recorder_loop``.
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog

from mate.api.services.usage_recorder import ServerEventDraft, enqueue_server_event

log = structlog.get_logger(__name__)

# (HTTP method, path regex, operation name). Friendly-name overrides for
# headline business operations; everything else falls back to the route
# template as the event name.
_BUSINESS_OPS: list[tuple[str, re.Pattern[str], str]] = [
    ("POST", re.compile(r"^/api/v1/ai/chat$"), "ai_chat"),
    ("POST", re.compile(r"^/api/v1/ai/guidance/"), "ai_guidance"),
    ("GET", re.compile(r"^/api/v1/admin/export/metadata-db$"), "admin_db_export"),
    ("GET", re.compile(r"^/api/v1/usage/export$"), "analytics_export"),
    ("DELETE", re.compile(r"^/api/v1/event-logs/[^/]+$"), "process_deleted"),
    ("DELETE", re.compile(r"^/api/v1/modules/[^/]+$"), "module_uninstalled"),
    ("DELETE", re.compile(r"^/api/v1/ai/guidance/module/[^/]+$"), "ai_guidance_cleared"),
]

# Paths that would only add noise (or feedback loops): the tracking pipeline
# itself, SSE streams, fast-polling reads, and infra endpoints.
_EXCLUDED_PREFIXES = (
    "/api/v1/usage",
    "/api/v1/events",
    "/api/v1/admin/insights",
    "/api/v1/system/metrics",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)

_EXCLUDED_METHODS = frozenset({"OPTIONS", "HEAD"})


def _match_op(method: str, path: str) -> str | None:
    for m, rx, name in _BUSINESS_OPS:
        if m == method and rx.match(path):
            return name
    return None


def _excluded(method: str, path: str) -> bool:
    if method in _EXCLUDED_METHODS:
        return True
    if path.startswith(_EXCLUDED_PREFIXES):
        return True
    # Per-job SSE streams: /api/v1/jobs/{id}/stream.
    return path.startswith("/api/v1/jobs/") and path.endswith("/stream")


class UsageTrackingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if _excluded(method, path):
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
                try:
                    self._enqueue(scope, method, path, user_id, status_code["code"], duration_ms)
                except Exception:
                    # Tracking must never surface to / break the response.
                    log.warning("usage_middleware.record_failed", path=path, exc_info=True)

    def _enqueue(
        self,
        scope: Any,
        method: str,
        path: str,
        user_id: str,
        status: int | None,
        duration_ms: int,
    ) -> None:
        # After routing, FastAPI leaves the matched route on the scope - its
        # template (`/api/v1/event-logs/{log_id}`) is the stable activity name
        # and its params carry the resource ids for the OCEL object layer.
        route = scope.get("route")
        template = getattr(route, "path_format", None) or path
        op = _match_op(method, path)
        path_params = {
            k: str(v) for k, v in (scope.get("path_params") or {}).items() if v is not None
        }
        properties: dict[str, Any] = {"method": method, "status": status, "route": template}
        if path_params:
            properties["path_params"] = path_params
        enqueue_server_event(
            ServerEventDraft(
                user_id=user_id,
                event_type="operation",
                event_name=op or f"{method} {template}",
                path=path,
                duration_ms=duration_ms,
                properties=properties,
            )
        )
