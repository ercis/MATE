"""Audit trail for MCP access.

The authoritative record is a structured log line emitted on **every** tool
call (and auth failure) - always, independent of the user's analytics opt-out -
so it ships to log aggregation / SIEM for compliance. A best-effort mirror into
the analytics stream feeds the admin insights UI (that one respects tracking
consent and never blocks the call).
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger("mcp.audit")


_MAX_LABEL_CHARS = 500


def _bounded_labels(labels: dict[str, Any] | None) -> dict[str, Any]:
    """Size-cap each label value so one giant argument can't bloat the trail."""
    out: dict[str, Any] = {}
    for key, value in (labels or {}).items():
        if isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
            continue
        text = value if isinstance(value, str) else repr(value)
        out[key] = text if len(text) <= _MAX_LABEL_CHARS else text[:_MAX_LABEL_CHARS] + "…"
    return out


async def record_tool_call(
    *,
    user_id: str,
    tool: str,
    status: str,
    duration_ms: int,
    token_id: str | None,
    auth_type: str,
    labels: dict[str, Any] | None = None,
    mutation: bool = False,
) -> None:
    # Nested under one key so a tool argument can never collide with (or
    # overwrite) the envelope fields ("status", "tool", ...).
    labels = _bounded_labels(labels)
    log.info(
        "mcp.tool_call",
        user_id=user_id,
        tool=tool,
        status=status,
        duration_ms=duration_ms,
        token_id=token_id,
        auth_type=auth_type,
        mutation=mutation,
        args=labels,
    )
    try:
        from mate.api.db.engine import get_sessionmaker
        from mate.api.routes.analytics import record_server_event
        from mate.api.services.analytics_objects import ObjectRef

        sm = get_sessionmaker()
        async with sm() as session:
            await record_server_event(
                session,
                user_id=user_id,
                event_type="mcp",
                event_name=tool,
                duration_ms=duration_ms,
                properties={
                    "status": status,
                    "token_id": token_id,
                    "auth_type": auth_type,
                    "mutation": mutation,
                    "args": labels,
                },
                # The tool is the MCP analogue of the paper's target object.
                objects=[ObjectRef(f"tool:{tool}", "mcp_tool", "target")],
            )
    except Exception as exc:
        log.warning("mcp.audit.analytics_failed", error=str(exc))
