"""Prometheus metrics for the MCP server.

Exposed at ``/api/v1/system/mcp-metrics`` (admin-gated). Single-instance, so the
default in-process registry is the whole picture.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

_TOOL_CALLS = Counter("mate_mcp_tool_calls_total", "MCP tool calls", ["tool", "status"])
_TOOL_LATENCY = Histogram(
    "mate_mcp_tool_latency_seconds", "MCP tool call latency (seconds)", ["tool"]
)
_RATE_LIMITED = Counter("mate_mcp_rate_limited_total", "MCP requests rejected by the rate limiter")
_ACTIVE = Gauge("mate_mcp_active_calls", "In-flight MCP tool calls")


def record_tool_call(tool: str, status: str, duration_seconds: float) -> None:
    _TOOL_CALLS.labels(tool=tool, status=status).inc()
    _TOOL_LATENCY.labels(tool=tool).observe(duration_seconds)


def record_rate_limited() -> None:
    _RATE_LIMITED.inc()


def inc_active() -> None:
    _ACTIVE.inc()


def dec_active() -> None:
    _ACTIVE.dec()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
