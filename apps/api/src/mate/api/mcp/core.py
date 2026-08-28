"""Shared runtime for MCP tools: principal, authz, guard, output cap, previews.

Every tool follows the same shape::

    @mcp_tool(toolset="processes", write=True, destructive=True)
    async def delete_process(ctx: MCPContext, log_id: str, confirm: bool = False) -> dict:
        p = await authz(ctx, SCOPE_PROCESSES_WRITE, write=True)

        async def _impl() -> dict[str, Any]:
            ...
        return await guarded(p, "delete_process", {"log_id": log_id}, _impl(), mutation=True)

``authz`` enforces scope + egress consent (+ read-only mode and the write rate
bucket for mutations); ``guarded`` runs the body under the concurrency cap and
timeout while recording metrics + audit. ``user_id`` is never a tool argument -
it always comes from the authenticated principal (tenant invariant).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import Context
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import ADMIN_ROLE, get_owned_event_log
from mate.api.config import get_settings
from mate.api.db.models import EventLog
from mate.api.mcp import audit, metrics
from mate.api.mcp.auth import SCOPE_PRINCIPAL_KEY, MCPPrincipal
from mate.api.mcp.consent import egress_consented
from mate.api.mcp.errors import (
    CODE_CONFIRM_REQUIRED,
    CODE_CONSENT_REQUIRED,
    CODE_FORBIDDEN,
    CODE_RATE_LIMITED,
    CODE_READ_ONLY,
    CODE_SCOPE_MISSING,
    CODE_TIMEOUT,
    MCPToolError,
    from_http_exception,
    tool_error,
)
from mate.api.mcp.limits import check_write_rate_limit, concurrency_gate
from mate.api.mcp.scopes import SCOPE_ADMIN

MCP_SERVER_VERSION = "2.0"
_MAX_OUTPUT_BYTES = 200_000

MCPContext = Context[Any, Any, Any]


def principal(ctx: MCPContext) -> MCPPrincipal:
    request = ctx.request_context.request
    p = request.scope.get(SCOPE_PRINCIPAL_KEY) if request is not None else None
    if not isinstance(p, MCPPrincipal):
        raise tool_error(CODE_FORBIDDEN, "Unauthenticated MCP call.")
    return p


async def authz(ctx: MCPContext, required_scope: str, *, write: bool = False) -> MCPPrincipal:
    """Resolve the caller and enforce scope + egress consent (+ write gates)."""
    p = principal(ctx)
    if required_scope not in p.scopes:
        raise tool_error(
            CODE_SCOPE_MISSING, f"This token lacks the required scope: {required_scope}"
        )
    if not await egress_consented(p.user.id):
        raise tool_error(
            CODE_CONSENT_REQUIRED,
            "External data access is not enabled for your account. "
            "Enable it in Settings → API & MCP.",
        )
    if write:
        await ensure_writable(p)
    return p


async def authz_admin(ctx: MCPContext, *, write: bool = False) -> MCPPrincipal:
    """Admin toolset gate: OAuth principal + ``admin`` scope + ``admin`` realm role.

    PATs are role-less by construction, so they can never pass this - platform
    administration over MCP always rides a short-lived Keycloak token.
    """
    p = principal(ctx)
    if p.auth_type != "oauth":
        raise tool_error(
            CODE_FORBIDDEN, "Admin tools require OAuth (Keycloak) authentication, not a PAT."
        )
    if SCOPE_ADMIN not in p.scopes:
        raise tool_error(CODE_SCOPE_MISSING, f"This token lacks the required scope: {SCOPE_ADMIN}")
    if ADMIN_ROLE not in p.user.roles:
        raise tool_error(CODE_FORBIDDEN, "The admin realm role is required.")
    if not await egress_consented(p.user.id):
        raise tool_error(
            CODE_CONSENT_REQUIRED,
            "External data access is not enabled for your account. "
            "Enable it in Settings → API & MCP.",
        )
    if write:
        await ensure_writable(p)
    return p


async def ensure_writable(p: MCPPrincipal) -> None:
    """Mutation gate: live read-only flag + the (tighter) write rate bucket."""
    from mate.api.mcp.governance import mcp_read_only

    if await mcp_read_only():
        raise tool_error(
            CODE_READ_ONLY, "The MCP server is in read-only mode; mutating tools are disabled."
        )
    allowed, retry_after = check_write_rate_limit(p.user.id)
    if not allowed:
        metrics.record_rate_limited()
        raise tool_error(CODE_RATE_LIMITED, f"Write rate limit exceeded; retry in {retry_after}s.")


async def guarded(
    principal_: MCPPrincipal,
    tool: str,
    labels: dict[str, Any],
    coro: Awaitable[Any],
    *,
    mutation: bool = False,
    timeout: float | None = None,
    gate: bool = True,
) -> Any:
    """Run a tool body under the concurrency cap + timeout, recording metrics + audit.

    ``gate=False`` skips the concurrency semaphores - only for cheap pure-await
    bodies (e.g. ``wait_for_job``) that would otherwise pin a slot while idle.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()
    status = "ok"
    metrics.inc_active()

    async def _acquire_and_run() -> Any:
        # Acquire the concurrency slots *inside* the timeout so a saturated pool
        # fails fast instead of queueing unbounded (cross-user head-of-line).
        if not gate:
            return await coro
        async with concurrency_gate(principal_.user.id):
            return await coro

    try:
        return await asyncio.wait_for(
            _acquire_and_run(),
            timeout=timeout if timeout is not None else get_settings().mcp_tool_timeout_seconds,
        )
    except TimeoutError as exc:
        status = "timeout"
        raise tool_error(CODE_TIMEOUT, f"Tool '{tool}' timed out or the server is busy.") from exc
    except MCPToolError as exc:
        status = exc.code
        raise
    except Exception:
        status = "error"
        raise
    finally:
        metrics.dec_active()
        duration = loop.time() - start
        metrics.record_tool_call(tool, status, duration)
        await audit.record_tool_call(
            user_id=principal_.user.id,
            tool=tool,
            status=status,
            duration_ms=int(duration * 1000),
            token_id=principal_.token_id,
            auth_type=principal_.auth_type,
            labels=labels,
            mutation=mutation,
        )


async def ensure_owned_log(session: AsyncSession, log_id: str, user_id: str) -> EventLog:
    """Ownership gate, translated to a tool error (404 for missing AND foreign)."""
    try:
        return await get_owned_event_log(session, log_id, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


def cap(obj: Any) -> Any:
    """Bound a tool's serialized output so one call can't return an unbounded blob."""
    raw = json.dumps(obj, default=str)
    if len(raw) <= _MAX_OUTPUT_BYTES:
        return obj
    return {
        "truncated": True,
        "reason": f"output exceeded {_MAX_OUTPUT_BYTES} bytes",
        "preview": raw[:_MAX_OUTPUT_BYTES],
    }


def confirm_preview(action: str, details: dict[str, Any]) -> dict[str, Any]:
    """The dry-run response a destructive tool returns when ``confirm`` is false.

    Deliberately NOT an error: the agent gets a preview of what would happen
    and re-calls with ``confirm=true`` to execute.
    """
    return {
        "confirmed": False,
        "action": action,
        "preview": details,
        "message": (
            f"[{CODE_CONFIRM_REQUIRED}] Pass confirm=true to execute '{action}'. "
            "Nothing was changed."
        ),
    }
