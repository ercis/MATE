"""The Mate MCP server: full-platform tools over a user's process-mining data.

Every call resolves the caller from the authenticated :class:`MCPPrincipal`
that [auth.MCPAuthMiddleware](auth.py) stashed on the ASGI scope - ``user_id``
is never a tool argument, so a token only ever acts as its owner (the platform
tenant invariant). Tool bodies live in :mod:`mate.api.mcp.toolsets` (one module
per toolset) and share :mod:`mate.api.mcp.core` for authz/guard/limits; this
module owns the FastMCP instance and performs the one-time registration of the
enabled toolsets. Raw event rows are never exposed - reads are aggregates and
curated module outputs only (the MCP data wall).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mate.api.mcp.core import MCP_SERVER_VERSION
from mate.api.mcp.registry import register_enabled

_INSTRUCTIONS = (
    "Access to the authenticated user's Mate process-mining platform. "
    "Call list_processes to discover event logs (use their id as log_id), "
    "list_modules to see which analyses have results for a log, then "
    "get_module_output / get_process_overview (or the curated get_bottlenecks / "
    "get_conformance / get_process_model / get_drifts) to read the findings. "
    "Aggregate log views: get_activities, get_variants, get_data_quality. "
    "Mutating tools require a write scope; destructive ones take confirm=true "
    "and return a dry-run preview without it. Long operations return a job_id - "
    "poll get_job or block on wait_for_job. Read the mate://docs/usage resource "
    "for the full contract. All outputs are curated summaries - never raw event "
    "rows."
)

# ``streamable_http_path="/"`` so the app serves at its mount root; FastMCP's
# default ("/mcp") would double-prefix to /mcp/mcp when mounted at /mcp.
#
# The SDK's DNS-rebinding protection defaults to a localhost-only Host
# allowlist - behind the prod proxy every request carries the public hostname
# and would 421. Host/Origin trust is owned by our own layer instead:
# MCPAuthMiddleware enforces the Origin allowlist + bearer auth on every
# request, and OAuth discovery URLs never derive from the Host header.
mcp = FastMCP(
    "Mate",
    instructions=_INSTRUCTIONS,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# register_enabled imports the toolset modules (collecting every spec) and
# registers the enabled ones - exactly once for this process.
register_enabled(mcp)

# Back-compat aliases: tests + external imports use `server.<tool>`.
from mate.api.mcp.toolsets.analysis import (  # noqa: E402
    get_bottlenecks,
    get_conformance,
    get_drifts,
    get_module_output,
    get_process_model,
    get_process_overview,
    list_modules,
    module_output_resource,
)
from mate.api.mcp.toolsets.meta import get_server_info, whoami  # noqa: E402
from mate.api.mcp.toolsets.processes import (  # noqa: E402
    list_processes,
    processes_resource,
)


def build_mcp_app():
    """Build the streamable-HTTP ASGI app (also creates ``mcp.session_manager``)."""
    return mcp.streamable_http_app()


__all__ = [
    "MCP_SERVER_VERSION",
    "build_mcp_app",
    "get_bottlenecks",
    "get_conformance",
    "get_drifts",
    "get_module_output",
    "get_process_model",
    "get_process_overview",
    "get_server_info",
    "list_modules",
    "list_processes",
    "mcp",
    "module_output_resource",
    "processes_resource",
    "whoami",
]
