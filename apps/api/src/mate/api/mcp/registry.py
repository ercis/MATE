"""Toolset registry: collect tool/resource specs, register the enabled ones.

Tool functions are plain module-level async functions decorated with
:func:`mcp_tool` (so tests can call them directly); actual FastMCP registration
happens once, at server import, for the toolsets enabled via ``MCP_TOOLSETS``.
Boot-time ``MCP_READ_ONLY`` additionally skips registering write tools
entirely; the *live* read-only flag is enforced per call in ``core.authz``.

Toolset gating is boot-time by design (FastMCP's tool list is static); the
live admin kill-switches are the ``mcp.enabled`` / ``mcp.read_only`` system
settings.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mate.api.config import Settings, get_settings

log = structlog.get_logger("mcp.registry")

TOOLSET_NAMES: tuple[str, ...] = (
    "meta",
    "processes",
    "analysis",
    "dashboards",
    "jobs",
    "watched",
    "account",
    "admin",
)
_DEFAULT_EXCLUDED = frozenset({"admin"})

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[..., Any]
    name: str
    toolset: str
    write: bool
    destructive: bool
    idempotent: bool | None


@dataclass(frozen=True)
class ResourceSpec:
    fn: Callable[..., Any]
    uri: str
    toolset: str


_TOOL_SPECS: list[ToolSpec] = []
_RESOURCE_SPECS: list[ResourceSpec] = []
_registered_toolsets: tuple[str, ...] = ()


def mcp_tool(
    *,
    toolset: str,
    write: bool = False,
    destructive: bool = False,
    idempotent: bool | None = None,
    name: str | None = None,
) -> Callable[[F], F]:
    """Collect a tool spec; the function itself is returned unwrapped."""
    if toolset not in TOOLSET_NAMES:
        raise ValueError(f"Unknown toolset '{toolset}'")

    def deco(fn: F) -> F:
        _TOOL_SPECS.append(
            ToolSpec(
                fn=fn,
                name=name or fn.__name__,
                toolset=toolset,
                write=write,
                destructive=destructive,
                idempotent=idempotent,
            )
        )
        return fn

    return deco


def mcp_resource(uri: str, *, toolset: str) -> Callable[[F], F]:
    if toolset not in TOOLSET_NAMES:
        raise ValueError(f"Unknown toolset '{toolset}'")

    def deco(fn: F) -> F:
        _RESOURCE_SPECS.append(ResourceSpec(fn=fn, uri=uri, toolset=toolset))
        return fn

    return deco


def enabled_toolsets(settings: Settings | None = None) -> frozenset[str]:
    """Parse ``MCP_TOOLSETS``: empty = all except admin; "all" = everything.

    Unknown names are ignored with a warning; ``meta`` is always on.
    """
    settings = settings or get_settings()
    raw = (settings.mcp_toolsets or "").strip().lower()
    all_names = frozenset(TOOLSET_NAMES)
    if not raw:
        return (all_names - _DEFAULT_EXCLUDED) | {"meta"}
    if raw == "all":
        return all_names
    requested = {t.strip() for t in raw.split(",") if t.strip()}
    unknown = requested - all_names
    if unknown:
        log.warning("mcp.toolsets.unknown_ignored", unknown=sorted(unknown))
    return frozenset(requested & all_names) | {"meta"}


def register_enabled(mcp: FastMCP) -> tuple[str, ...]:
    """Register every collected spec whose toolset is enabled. Idempotent-once:
    call exactly once per process (server.py does)."""
    global _registered_toolsets
    # Side-effect import: pulls in every toolset module so their decorators
    # populate the spec lists before we walk them.
    importlib.import_module("mate.api.mcp.toolsets")
    settings = get_settings()
    enabled = enabled_toolsets(settings)
    skip_writes = settings.mcp_read_only
    tools = 0
    for spec in _TOOL_SPECS:
        if spec.toolset not in enabled or (skip_writes and spec.write):
            continue
        annotations = ToolAnnotations(
            readOnlyHint=not spec.write,
            destructiveHint=spec.destructive if spec.write else False,
            idempotentHint=spec.idempotent,
            openWorldHint=False,
        )
        mcp.tool(name=spec.name, annotations=annotations)(spec.fn)
        tools += 1
    resources = 0
    for rspec in _RESOURCE_SPECS:
        if rspec.toolset not in enabled:
            continue
        mcp.resource(rspec.uri)(rspec.fn)
        resources += 1
    _registered_toolsets = tuple(sorted(enabled))
    log.info(
        "mcp.registered",
        toolsets=_registered_toolsets,
        tools=tools,
        resources=resources,
        read_only_boot=skip_writes,
    )
    return _registered_toolsets


def registered_toolsets() -> tuple[str, ...]:
    """The toolsets actually registered at boot (for server_info / mcp-info)."""
    return _registered_toolsets


def all_tool_specs() -> tuple[ToolSpec, ...]:
    """Every collected spec (for tests + docs), regardless of enablement."""
    return tuple(_TOOL_SPECS)
