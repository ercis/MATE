"""End-to-end MCP transport test: a real SDK client speaking streamable HTTP
against the mounted app (auth middleware + session manager + tool dispatch).

One test function on purpose: ``StreamableHTTPSessionManager.run()`` is
once-per-instance and the FastMCP server is a process-level singleton, so the
session manager may be entered exactly once in this pytest process (the other
MCP suites never enter it - they call tools directly or only exercise the
pre-session auth layer).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mate.api.auth.tokens import mint_token
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import EventLog, UserSetting
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY

from .conftest import TEST_USER_ID

E2E_LOG_ID = "e2e-mcp-log"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _seed(session_maker: Any) -> str:
    async with session_maker() as session:
        consent = await session.get(UserSetting, (TEST_USER_ID, MCP_EGRESS_CONSENT_KEY))
        if consent is None:
            session.add(
                UserSetting(user_id=TEST_USER_ID, key=MCP_EGRESS_CONSENT_KEY, value_json=True)
            )
        else:
            consent.value_json = True
        if await session.get(EventLog, E2E_LOG_ID) is None:
            session.add(
                EventLog(
                    id=E2E_LOG_ID,
                    user_id=TEST_USER_ID,
                    name="E2E log",
                    status="ready",
                    created_at=_now(),
                )
            )
        # Empty grant == all read scopes: the negative write assertion below
        # relies on this default.
        _row, secret = await mint_token(session, TEST_USER_ID, "e2e", scopes=[])
        await session.commit()
    return secret


def _text_payload(result: Any) -> Any:
    """First text content block of a tool result, JSON-decoded when possible."""
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return text
    return None


async def test_streamable_http_end_to_end(client: AsyncClient) -> None:
    from mate.api import config as cfg

    prev = {k: os.environ.get(k) for k in ("MCP_ENABLED", "API_BASE_URL", "MCP_TOOLSETS")}
    os.environ["MCP_ENABLED"] = "1"
    os.environ["API_BASE_URL"] = "http://testserver"
    os.environ.pop("MCP_TOOLSETS", None)  # default: every toolset except admin
    cfg.get_settings.cache_clear()
    from mate.api.mcp.governance import reset_enabled_cache

    reset_enabled_cache()
    try:
        secret = await _seed(get_sessionmaker())

        from mate.api.main import create_app
        from mate.api.mcp import mcp_session_manager

        app = create_app()

        async with (
            mcp_session_manager(),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {secret}"},
                # Mirror the SDK's defaults: short connect/write, long SSE read.
                timeout=httpx.Timeout(30.0, read=300.0),
            ) as http_client,
            streamable_http_client("http://testserver/mcp/", http_client=http_client) as (
                read,
                write,
                _get_session_id,
            ),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            assert init.serverInfo.name == "Mate"

            # tools/list: the full v2 surface minus the admin toolset.
            listed = await session.list_tools()
            tools = {t.name: t for t in listed.tools}
            expected = {
                "get_server_info",
                "whoami",
                "list_processes",
                "get_process",
                "get_variants",
                "get_module_output",
                "get_process_overview",
                "list_dashboards",
                "create_dashboard",
                "list_jobs",
                "wait_for_job",
                "list_watched_folders",
                "list_api_tokens",
                "delete_process",
            }
            assert expected <= set(tools), sorted(set(tools))
            assert not any(name.startswith("admin_") for name in tools)

            # Annotations ride the registry flags.
            delete_ann = tools["delete_process"].annotations
            assert delete_ann is not None
            assert delete_ann.readOnlyHint is False
            assert delete_ann.destructiveHint is True
            info_ann = tools["get_server_info"].annotations
            assert info_ann is not None and info_ann.readOnlyHint is True

            # tools/call happy paths.
            info = _text_payload(await session.call_tool("get_server_info", {}))
            assert info["version"] == "2.0"
            assert info["auth_type"] == "pat"
            assert "admin" not in info["toolsets"]

            page = _text_payload(await session.call_tool("list_processes", {}))
            assert any(item["id"] == E2E_LOG_ID for item in page["items"])

            # Negative: a read-only default grant may not mutate.
            denied = await session.call_tool(
                "delete_process", {"log_id": E2E_LOG_ID, "confirm": False}
            )
            assert denied.isError is True
            assert "[scope_missing]" in (_text_payload(denied) or "")
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cfg.get_settings.cache_clear()
        reset_enabled_cache()
