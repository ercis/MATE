"""Meta toolset: server identity + caller identity. Always enabled.

Neither tool returns process data, so neither requires a scope or egress
consent - they only describe the token and the server contract.
"""

from __future__ import annotations

from typing import Any

from mate.api.mcp.core import MCP_SERVER_VERSION, MCPContext, guarded, principal
from mate.api.mcp.registry import mcp_resource, mcp_tool


@mcp_tool(toolset="meta", idempotent=True)
async def get_server_info(ctx: MCPContext) -> dict[str, Any]:
    """Server version, enabled toolsets, read-only state and your token's scopes."""
    p = principal(ctx)

    async def _impl() -> dict[str, Any]:
        from mate.api.mcp.governance import mcp_read_only
        from mate.api.mcp.registry import registered_toolsets

        return {
            "name": "Mate",
            "version": MCP_SERVER_VERSION,
            "auth_type": p.auth_type,
            "scopes": list(p.scopes),
            "toolsets": list(registered_toolsets()),
            "read_only": await mcp_read_only(),
        }

    return await guarded(p, "get_server_info", {}, _impl())


@mcp_tool(toolset="meta", idempotent=True)
async def whoami(ctx: MCPContext) -> dict[str, Any]:
    """The authenticated account this token acts as."""
    p = principal(ctx)

    async def _impl() -> dict[str, Any]:
        return {
            "user_id": p.user.id,
            "email": p.user.email,
            "name": p.user.name,
            "preferred_username": p.user.preferred_username,
            "auth_type": p.auth_type,
            "token_id": p.token_id,
            "scopes": list(p.scopes),
        }

    return await guarded(p, "whoami", {}, _impl())


@mcp_resource("mate://docs/usage", toolset="meta")
async def usage_docs() -> str:
    """How to drive this server, for agent consumption."""
    return (
        "Mate MCP server usage\n"
        "=====================\n"
        "Discovery: call list_processes to find event logs; use a returned id as\n"
        "log_id everywhere. list_modules(log_id) shows which analyses have output;\n"
        "get_module_output / get_process_overview read the findings. Aggregate log\n"
        "views: get_activities, get_variants, get_data_quality, get_time_bounds.\n"
        "\n"
        "Mutations: tools marked as write require a write scope on your token and\n"
        "are refused in read-only mode. Destructive tools take confirm=true; called\n"
        "without it they return a dry-run preview and change nothing.\n"
        "\n"
        "Long operations return a job_id; poll get_job or block on wait_for_job.\n"
        "Errors carry a stable [code] prefix: not_found, forbidden, conflict,\n"
        "invalid, rate_limited, timeout, read_only, consent_required,\n"
        "scope_missing, confirm_required, internal.\n"
        "\n"
        "Raw event rows are never exposed over MCP - only aggregates and curated\n"
        "module outputs.\n"
    )
