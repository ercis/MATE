"""Mate MCP server - full-platform tools over a user's process-mining data.

Toolsets (registry-gated), scoped PAT/OAuth auth, per-user egress consent,
read-only mode and confirm-gated destructive tools; raw event rows are never
exposed. Mounted at ``/mcp`` (streamable HTTP) when ``settings.mcp_enabled``.
Two pieces the rest of the app wires up:

* :func:`build_mcp_asgi_app` - the auth-wrapped ASGI app to ``app.mount(...)``.
* :func:`mcp_session_manager` - an async context manager the API lifespan must
  enter so the streamable-HTTP session manager runs (a mounted sub-app's own
  lifespan is never triggered by Starlette).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from mate.api.mcp.auth import MCPAuthMiddleware
from mate.api.mcp.server import build_mcp_app, mcp


def build_mcp_asgi_app():
    """Auth-wrapped streamable-HTTP app for mounting. Creates the session manager."""
    return MCPAuthMiddleware(build_mcp_app())


@asynccontextmanager
async def mcp_session_manager():
    """Run the streamable-HTTP session manager for the app's lifetime.

    Must be entered by the API lifespan *after* :func:`build_mcp_asgi_app` has
    been called (mounting creates ``mcp.session_manager``).
    """
    async with mcp.session_manager.run():
        yield


__all__ = ["build_mcp_asgi_app", "mcp_session_manager"]
