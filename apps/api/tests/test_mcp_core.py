"""Tests for the MCP v2 core: scope taxonomy + OAuth mapping, read-only mode,
write rate bucket, toolset registry, pagination cursors, error taxonomy,
origin guard, admin authz.
"""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import SystemSetting, UserSetting
from mate.api.mcp.auth import MCPPrincipal, _origin_allowed
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError, from_http_exception, tool_error
from mate.api.mcp.pagination import clamp_limit, decode_cursor, encode_cursor, page_envelope
from mate.api.mcp.scopes import (
    ALL_SCOPES,
    PAT_GRANTABLE_SCOPES,
    READ_SCOPES,
    SCOPE_ADMIN,
    SCOPE_PROCESSES_READ,
    SCOPE_PROCESSES_WRITE,
    effective_scopes,
    sanitize_scopes,
    scopes_from_oauth_claims,
)

from .conftest import TEST_USER_ID


class _FakeRequest:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.scope: dict[str, object] = {} if principal is None else {"mate_principal": principal}


class _FakeRequestContext:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request = _FakeRequest(principal)


class _FakeCtx:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request_context = _FakeRequestContext(principal)


def _principal(
    user_id: str,
    scopes: tuple[str, ...] = ALL_SCOPES,
    *,
    auth_type: str = "pat",
    roles: tuple[str, ...] = (),
) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=roles)
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type=auth_type)


async def _set_consent(user_id: str, value: bool) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
        if row is None:
            session.add(UserSetting(user_id=user_id, key=MCP_EGRESS_CONSENT_KEY, value_json=value))
        else:
            row.value_json = value
        await session.commit()


# ── scopes ───────────────────────────────────────────────────────────────────


def test_scope_taxonomy_shape() -> None:
    assert set(READ_SCOPES) < set(ALL_SCOPES)
    assert SCOPE_ADMIN in ALL_SCOPES
    assert SCOPE_ADMIN not in PAT_GRANTABLE_SCOPES


def test_sanitize_scopes_drops_admin_for_pats() -> None:
    assert SCOPE_ADMIN not in sanitize_scopes([SCOPE_ADMIN, SCOPE_PROCESSES_READ])
    assert sanitize_scopes([SCOPE_ADMIN], allow_admin=True) == [SCOPE_ADMIN]


def test_effective_scopes_empty_grant_is_read_only() -> None:
    assert effective_scopes(None) == READ_SCOPES
    assert effective_scopes([]) == READ_SCOPES
    assert effective_scopes([SCOPE_PROCESSES_WRITE]) == (SCOPE_PROCESSES_WRITE,)


def test_oauth_scope_mapping() -> None:
    # No known scopes → read fallback.
    assert scopes_from_oauth_claims({"scope": "openid profile email"}, ()) == READ_SCOPES
    assert scopes_from_oauth_claims({}, ()) == READ_SCOPES
    # Known scopes pass through.
    got = scopes_from_oauth_claims(
        {"scope": f"openid {SCOPE_PROCESSES_READ} {SCOPE_PROCESSES_WRITE}"}, ()
    )
    assert set(got) == {SCOPE_PROCESSES_READ, SCOPE_PROCESSES_WRITE}
    # `admin` needs the realm role on the same token.
    no_role = scopes_from_oauth_claims({"scope": f"{SCOPE_ADMIN} {SCOPE_PROCESSES_READ}"}, ())
    assert SCOPE_ADMIN not in no_role
    with_role = scopes_from_oauth_claims(
        {"scope": f"{SCOPE_ADMIN} {SCOPE_PROCESSES_READ}"}, ("admin",)
    )
    assert SCOPE_ADMIN in with_role


# ── errors + pagination ──────────────────────────────────────────────────────


def test_tool_error_carries_code_prefix() -> None:
    err = tool_error("not_found", "no such log")
    assert isinstance(err, ValueError)
    assert str(err).startswith("[not_found]")

    from fastapi import HTTPException

    assert from_http_exception(HTTPException(status_code=404, detail="x")).code == "not_found"
    assert from_http_exception(HTTPException(status_code=409, detail="x")).code == "conflict"
    assert from_http_exception(HTTPException(status_code=403, detail="x")).code == "forbidden"


def test_pagination_cursor_roundtrip() -> None:
    assert decode_cursor(None) == 0
    assert decode_cursor(encode_cursor(120)) == 120
    with pytest.raises(MCPToolError):
        decode_cursor("garbage!!")
    assert clamp_limit(None) == 50
    assert clamp_limit(10_000) == 200

    env = page_envelope([1, 2, 3], offset=0, limit=3, total=10)
    assert env["total"] == 10 and env["next_cursor"] is not None
    assert decode_cursor(env["next_cursor"]) == 3
    last = page_envelope([1], offset=9, limit=3, total=10)
    assert last["next_cursor"] is None


# ── registry / toolsets ──────────────────────────────────────────────────────


def test_enabled_toolsets_parsing() -> None:
    from mate.api import config as cfg
    from mate.api.mcp.registry import TOOLSET_NAMES, enabled_toolsets

    prev = os.environ.get("MCP_TOOLSETS")
    try:
        os.environ.pop("MCP_TOOLSETS", None)
        cfg.get_settings.cache_clear()
        default = enabled_toolsets()
        assert "admin" not in default and "meta" in default and "processes" in default

        os.environ["MCP_TOOLSETS"] = "all"
        cfg.get_settings.cache_clear()
        assert enabled_toolsets() == frozenset(TOOLSET_NAMES)

        os.environ["MCP_TOOLSETS"] = "processes,bogus"
        cfg.get_settings.cache_clear()
        got = enabled_toolsets()
        assert got == frozenset({"processes", "meta"})
    finally:
        if prev is None:
            os.environ.pop("MCP_TOOLSETS", None)
        else:
            os.environ["MCP_TOOLSETS"] = prev
        cfg.get_settings.cache_clear()


def test_registry_collected_admin_tools_are_admin_toolset_only() -> None:
    from mate.api.mcp.registry import all_tool_specs

    specs = {s.name: s for s in all_tool_specs()}
    assert "list_processes" in specs and specs["list_processes"].toolset == "processes"
    assert specs["list_processes"].write is False
    for s in specs.values():
        if s.name.startswith("admin_"):
            assert s.toolset == "admin"


# ── write gates: read-only mode + write bucket ───────────────────────────────


async def test_write_tool_blocked_in_read_only_mode(client: AsyncClient) -> None:
    from mate.api.mcp.core import authz
    from mate.api.mcp.governance import MCP_READ_ONLY_KEY, reset_enabled_cache

    await _set_consent(TEST_USER_ID, True)
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(SystemSetting(key=MCP_READ_ONLY_KEY, value_json=True))
        await session.commit()
    reset_enabled_cache()
    try:
        p = _principal(TEST_USER_ID, scopes=(SCOPE_PROCESSES_WRITE,))
        with pytest.raises(MCPToolError, match=r"\[read_only\]"):
            await authz(_FakeCtx(p), SCOPE_PROCESSES_WRITE, write=True)  # type: ignore[arg-type]
        # Reads keep working in read-only mode.
        read_p = _principal(TEST_USER_ID, scopes=(SCOPE_PROCESSES_READ,))
        assert await authz(_FakeCtx(read_p), SCOPE_PROCESSES_READ) is read_p  # type: ignore[arg-type]
    finally:
        async with sm() as session:
            row = await session.get(SystemSetting, MCP_READ_ONLY_KEY)
            if row is not None:
                await session.delete(row)
                await session.commit()
        reset_enabled_cache()


def test_write_rate_bucket_is_separate_and_tighter() -> None:
    from mate.api import config as cfg
    from mate.api.mcp import limits

    prev = {
        k: os.environ.get(k)
        for k in (
            "MCP_WRITE_RATE_LIMIT_PER_MINUTE",
            "MCP_WRITE_RATE_LIMIT_BURST",
            "MCP_RATE_LIMIT_PER_MINUTE",
            "MCP_RATE_LIMIT_BURST",
        )
    }
    os.environ["MCP_WRITE_RATE_LIMIT_PER_MINUTE"] = "1"
    os.environ["MCP_WRITE_RATE_LIMIT_BURST"] = "1"
    os.environ["MCP_RATE_LIMIT_PER_MINUTE"] = "1000"
    os.environ["MCP_RATE_LIMIT_BURST"] = "1000"
    cfg.get_settings.cache_clear()
    limits.reset_for_tests()
    try:
        assert limits.check_write_rate_limit("u")[0] is True
        allowed, retry_after = limits.check_write_rate_limit("u")
        assert allowed is False and retry_after >= 1
        # The per-request bucket is untouched by write charges.
        assert limits.check_rate_limit("u")[0] is True
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cfg.get_settings.cache_clear()
        limits.reset_for_tests()


# ── admin authz ──────────────────────────────────────────────────────────────


async def test_admin_authz_refuses_pat_even_with_forged_scope(client: AsyncClient) -> None:
    from mate.api.mcp.core import authz_admin

    await _set_consent(TEST_USER_ID, True)
    pat = _principal(TEST_USER_ID, scopes=(SCOPE_ADMIN,), auth_type="pat", roles=("admin",))
    with pytest.raises(MCPToolError, match=r"\[forbidden\]"):
        await authz_admin(_FakeCtx(pat))  # type: ignore[arg-type]

    oauth_no_role = _principal(TEST_USER_ID, scopes=(SCOPE_ADMIN,), auth_type="oauth")
    with pytest.raises(MCPToolError, match=r"\[forbidden\]"):
        await authz_admin(_FakeCtx(oauth_no_role))  # type: ignore[arg-type]

    oauth_no_scope = _principal(
        TEST_USER_ID, scopes=READ_SCOPES, auth_type="oauth", roles=("admin",)
    )
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await authz_admin(_FakeCtx(oauth_no_scope))  # type: ignore[arg-type]

    ok = _principal(TEST_USER_ID, scopes=(SCOPE_ADMIN,), auth_type="oauth", roles=("admin",))
    assert await authz_admin(_FakeCtx(ok)) is ok  # type: ignore[arg-type]


# ── origin guard ─────────────────────────────────────────────────────────────


def test_origin_allowlist() -> None:
    from mate.api import config as cfg

    prev = os.environ.get("API_BASE_URL")
    os.environ["API_BASE_URL"] = "https://pm-mate.uni-muenster.de"
    cfg.get_settings.cache_clear()
    try:
        assert _origin_allowed("http://localhost:6274") is True  # inspector/dev
        assert _origin_allowed("https://pm-mate.uni-muenster.de") is True
        assert _origin_allowed("https://evil.example.com") is False
    finally:
        if prev is None:
            os.environ.pop("API_BASE_URL", None)
        else:
            os.environ["API_BASE_URL"] = prev
        cfg.get_settings.cache_clear()


# ── confirm previews ─────────────────────────────────────────────────────────


def test_confirm_preview_shape() -> None:
    from mate.api.mcp.core import confirm_preview

    out = confirm_preview("delete_process", {"log_id": "x", "name": "Log"})
    assert out["confirmed"] is False
    assert out["action"] == "delete_process"
    assert "[confirm_required]" in out["message"]
