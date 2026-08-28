"""Tests for the MCP server + its enterprise hardening.

Covers the security-critical surface: PAT mint/verify (hash-only, scopes,
revoke/expiry), token routes + mint policy, the ``/mcp`` auth gate + OAuth
discovery, scope + egress-consent enforcement, rate limiting, and per-tool
tenant isolation.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.auth.tokens import TOKEN_PREFIX, mint_token, verify_token, verify_token_row
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import ApiToken, EventLog, User, UserSetting
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.scopes import (
    ALL_SCOPES,
    PAT_GRANTABLE_SCOPES,
    SCOPE_MODULES_READ,
    SCOPE_PROCESSES_READ,
)

from .conftest import TEST_USER_ID

USER_B_ID = "00000000-0000-7000-8000-0000000000b2"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _FakeRequest:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.scope: dict[str, object] = {} if principal is None else {"mate_principal": principal}


class _FakeRequestContext:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request = _FakeRequest(principal)


class _FakeCtx:
    """Stand-in for FastMCP's Context - tools only read request_context.request.scope."""

    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request_context = _FakeRequestContext(principal)


def _principal(user_id: str, scopes: tuple[str, ...] = ALL_SCOPES) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=())
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type="pat")


async def _set_consent(session, user_id: str, value: bool) -> None:
    row = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
    if row is None:
        session.add(UserSetting(user_id=user_id, key=MCP_EGRESS_CONSENT_KEY, value_json=value))
    else:
        row.value_json = value


# ── PAT mint / verify / scopes ──────────────────────────────────────────────


async def test_pat_mint_stores_only_hash_and_verifies() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        row, secret = await mint_token(session, TEST_USER_ID, "ci", scopes=[SCOPE_PROCESSES_READ])
        await session.commit()
        token_id = row.id

    assert secret.startswith(TOKEN_PREFIX)
    async with sm() as session:
        stored = await session.get(ApiToken, token_id)
        assert stored is not None
        assert stored.token_hash != secret
        assert stored.scopes == [SCOPE_PROCESSES_READ]

        user = await verify_token(session, secret)
        assert user is not None and user.id == TEST_USER_ID and user.roles == ()
        row2 = await verify_token_row(session, secret)
        assert row2 is not None and row2.last_used_at is not None


async def test_pat_mint_sanitizes_unknown_scopes() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        row, _secret = await mint_token(
            session, TEST_USER_ID, "s", scopes=["bogus", SCOPE_MODULES_READ]
        )
        await session.commit()
        assert row.scopes == [SCOPE_MODULES_READ]


async def test_pat_verify_rejects_bad_revoked_and_expired() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        assert await verify_token(session, "not-a-pat") is None
        assert await verify_token(session, TOKEN_PREFIX + "deadbeef") is None
        revoked, revoked_secret = await mint_token(session, TEST_USER_ID, "revoked")
        revoked.revoked = True
        _exp, expired_secret = await mint_token(
            session, TEST_USER_ID, "expired", expires_at=_now() - timedelta(days=1)
        )
        await session.commit()
        assert await verify_token(session, revoked_secret) is None
        assert await verify_token(session, expired_secret) is None


# ── Token routes + mint policy + consent ────────────────────────────────────


async def test_api_token_routes_crud(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/api-tokens", json={"name": "laptop", "scopes": [SCOPE_PROCESSES_READ]}
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["token"].startswith(TOKEN_PREFIX)
    assert body["scopes"] == [SCOPE_PROCESSES_READ]
    token_id, secret = body["id"], body["token"]

    listed = (await client.get("/api/v1/api-tokens")).json()
    mine = next(i for i in listed if i["id"] == token_id)
    assert "token" not in mine and mine["revoked"] is False

    assert (await client.delete(f"/api/v1/api-tokens/{token_id}")).status_code == 200
    sm = get_sessionmaker()
    async with sm() as session:
        assert await verify_token(session, secret) is None


async def test_mint_policy_admin_only_blocks_non_admin(client: AsyncClient) -> None:
    from mate.api.db.models import SystemSetting
    from mate.api.mcp.governance import MCP_MINT_POLICY_KEY

    sm = get_sessionmaker()
    async with sm() as session:
        session.add(SystemSetting(key=MCP_MINT_POLICY_KEY, value_json="admin_only"))
        await session.commit()
    try:
        # Conftest's fake user is a non-admin ("user" role).
        resp = await client.post("/api/v1/api-tokens", json={"name": "x"})
        assert resp.status_code == 403
    finally:
        async with sm() as session:
            row = await session.get(SystemSetting, MCP_MINT_POLICY_KEY)
            if row is not None:
                await session.delete(row)
                await session.commit()


async def test_consent_roundtrip_and_mcp_info(client: AsyncClient) -> None:
    state = (await client.get("/api/v1/api-tokens/consent")).json()
    assert state["required"] is True

    put = await client.put("/api/v1/api-tokens/consent", json={"consented": True})
    assert put.status_code == 200 and put.json()["consented"] is True

    info = (await client.get("/api/v1/api-tokens/mcp-info")).json()
    assert info["url"].endswith("/mcp")
    assert info["consented"] is True
    # The mint UI offers every PAT-grantable scope - never `admin`.
    assert {s["id"] for s in info["scopes_supported"]} == set(PAT_GRANTABLE_SCOPES)
    assert "admin" not in {s["id"] for s in info["scopes_supported"]}
    assert info["read_only"] is False
    assert isinstance(info["toolsets"], list)
    assert "metadata_url" in info["oauth"]


# ── Tool authz: scope + consent + tenant isolation ──────────────────────────


async def test_tool_requires_consent(client: AsyncClient) -> None:
    from mate.api.mcp import server

    sm = get_sessionmaker()
    async with sm() as session:
        await _set_consent(session, TEST_USER_ID, False)
        await session.commit()
    with pytest.raises(ValueError, match="not enabled"):
        await server.list_processes(_FakeCtx(_principal(TEST_USER_ID)))


async def test_tool_requires_scope(client: AsyncClient) -> None:
    from mate.api.mcp import server

    sm = get_sessionmaker()
    async with sm() as session:
        await _set_consent(session, TEST_USER_ID, True)
        await session.commit()
    # Principal holds only processes:read → a modules:read tool is denied.
    only_processes = _principal(TEST_USER_ID, scopes=(SCOPE_PROCESSES_READ,))
    with pytest.raises(ValueError, match="scope"):
        await server.get_module_output(_FakeCtx(only_processes), log_id="x", module_id="discovery")


async def test_tools_are_tenant_isolated(client: AsyncClient) -> None:
    from mate.api.mcp import server

    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(User, USER_B_ID) is None:
            session.add(
                User(id=USER_B_ID, email="b@mate.local", created_at=_now(), last_seen_at=_now())
            )
            await session.flush()
        for lid, uid in (("log-a", TEST_USER_ID), ("log-b", USER_B_ID)):
            if await session.get(EventLog, lid) is None:
                session.add(
                    EventLog(id=lid, user_id=uid, name=lid, status="ready", created_at=_now())
                )
        await _set_consent(session, TEST_USER_ID, True)
        await _set_consent(session, USER_B_ID, True)
        await session.commit()

    page_a = await server.list_processes(_FakeCtx(_principal(TEST_USER_ID)))
    a_ids = {p["id"] for p in page_a["items"]}
    assert "log-a" in a_ids and "log-b" not in a_ids
    page_b = await server.list_processes(_FakeCtx(_principal(USER_B_ID)))
    b_ids = {p["id"] for p in page_b["items"]}
    assert "log-b" in b_ids and "log-a" not in b_ids

    with pytest.raises(ValueError):
        await server.get_module_output(
            _FakeCtx(_principal(TEST_USER_ID)), log_id="log-b", module_id="discovery"
        )


async def test_tool_unauthenticated_rejected() -> None:
    from mate.api.mcp import server

    with pytest.raises(ValueError):
        await server.list_processes(_FakeCtx(None))


# ── Rate limiting ────────────────────────────────────────────────────────────


def test_rate_limit_token_bucket() -> None:
    from mate.api import config as cfg
    from mate.api.mcp import limits

    prev = os.environ.get("MCP_RATE_LIMIT_PER_MINUTE"), os.environ.get("MCP_RATE_LIMIT_BURST")
    os.environ["MCP_RATE_LIMIT_PER_MINUTE"] = "2"
    os.environ["MCP_RATE_LIMIT_BURST"] = "2"
    cfg.get_settings.cache_clear()
    limits.reset_for_tests()
    try:
        assert limits.check_rate_limit("u")[0] is True
        assert limits.check_rate_limit("u")[0] is True
        allowed, retry_after = limits.check_rate_limit("u")
        assert allowed is False and retry_after >= 1
    finally:
        for key, value in zip(
            ("MCP_RATE_LIMIT_PER_MINUTE", "MCP_RATE_LIMIT_BURST"), prev, strict=True
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cfg.get_settings.cache_clear()
        limits.reset_for_tests()


# ── /mcp transport: enabled app (auth gate, OAuth discovery, path) ──────────


@contextlib.asynccontextmanager
async def _mcp_enabled_app() -> AsyncIterator[AsyncClient]:
    from mate.api import config as cfg

    prev = {k: os.environ.get(k) for k in ("MCP_ENABLED", "API_BASE_URL")}
    os.environ["MCP_ENABLED"] = "1"
    os.environ["API_BASE_URL"] = "http://testserver"  # OAuth discovery needs a fixed base
    cfg.get_settings.cache_clear()
    from mate.api.mcp.governance import reset_enabled_cache

    reset_enabled_cache()
    try:
        from mate.api.main import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            yield c
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cfg.get_settings.cache_clear()
        reset_enabled_cache()


async def test_mcp_endpoint_requires_bearer() -> None:
    async with _mcp_enabled_app() as c:
        no_auth = await c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert no_auth.status_code == 401
        assert no_auth.json()["error"] == "unauthorized"
        assert "resource_metadata" in no_auth.headers.get("www-authenticate", "")

        bad = await c.post(
            "/mcp/",
            headers={"Authorization": f"Bearer {TOKEN_PREFIX}nope"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert bad.status_code == 401


async def test_oauth_protected_resource_metadata() -> None:
    async with _mcp_enabled_app() as c:
        resp = await c.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["resource"].endswith("/mcp")
        assert meta["authorization_servers"]
        assert set(meta["scopes_supported"]) == set(ALL_SCOPES)


async def test_mcp_not_mounted_when_disabled(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 404


def test_mcp_inner_path_is_root() -> None:
    """Regression guard: FastMCP must serve at "/" so mounting at "/mcp" lands at
    /mcp (the default "/mcp" would double-prefix to /mcp/mcp)."""
    from mate.api.mcp.server import mcp

    assert mcp.settings.streamable_http_path == "/"


# ── Admin governance gating ─────────────────────────────────────────────────


async def test_admin_routes_require_admin(client: AsyncClient) -> None:
    # Conftest's fake user is a non-admin → admin governance + metrics are gated.
    assert (await client.get("/api/v1/system/mcp")).status_code == 403
    assert (await client.get("/api/v1/system/mcp-metrics")).status_code == 403
    assert (await client.get("/api/v1/admin/api-tokens")).status_code == 403
