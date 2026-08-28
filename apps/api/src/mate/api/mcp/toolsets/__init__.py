"""MCP toolsets. Importing this package collects every tool/resource spec.

One module per toolset; each defines plain module-level async functions
decorated with ``@mcp_tool(toolset=...)`` / ``@mcp_resource(...)`` (registry
collection - directly importable in tests). ``server.py`` triggers actual
FastMCP registration for the enabled toolsets.
"""

from mate.api.mcp.toolsets import (
    account,
    admin,
    analysis,
    dashboards,
    jobs,
    meta,
    processes,
    watched,
)

__all__ = [
    "account",
    "admin",
    "analysis",
    "dashboards",
    "jobs",
    "meta",
    "processes",
    "watched",
]
