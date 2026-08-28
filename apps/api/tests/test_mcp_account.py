"""Tests for the MCP account toolset: usage summary, prefix-only token list,
revoke preview vs confirm (verify_token flips), foreign-token 404, scope
denial.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.auth.tokens import mint_token, verify_token
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import User, UserSetting
from mate.api.mcp import limits
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.scopes import ALL_SCOPES, SCOPE_ACCOUNT_READ, SCOPE_PROCESSES_READ
from mate.api.mcp.toolsets import account as account_tools

from .conftest import TEST_USER_ID

USER_B_ID = "00000000-0000-7000-8000-0000000000b5"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _FakeRequest:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.scope: dict[str, object] = {} if principal is None else {"mate_principal": principal}


class _FakeRequestContext:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request = _FakeRequest(principal)


class _FakeCtx:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request_context = _FakeRequestContext(principal)


def _principal(user_id: str, scopes: tuple[str, ...] = ALL_SCOPES) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=())
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type="pat")


def _ctx(user_id: str = TEST_USER_ID, scopes: tuple[str, ...] = ALL_SCOPES) -> Any:
    return _FakeCtx(_principal(user_id, scopes))


@pytest.fixture(autouse=True)
def _fresh_rate_buckets() -> None:  # pyright: ignore[reportUnusedFunction]
    limits.reset_for_tests()


async def _set_consent(user_id: str, value: bool) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
        if row is None:
            session.add(UserSetting(user_id=user_id, key=MCP_EGRESS_CONSENT_KEY, value_json=value))
        else:
            row.value_json = value
        await session.commit()


async def _ensure_user_b() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(User, USER_B_ID) is None:
            session.add(
                User(id=USER_B_ID, email="b5@mate.local", created_at=_now(), last_seen_at=_now())
            )
            await session.commit()


async def _mint(user_id: str, name: str) -> tuple[str, str]:
    """Mint a PAT; returns (token_id, cleartext secret)."""
    sm = get_sessionmaker()
    async with sm() as session:
        row, secret = await mint_token(session, user_id, name, scopes=[SCOPE_PROCESSES_READ])
        await session.commit()
        return row.id, secret


# ── usage summary ────────────────────────────────────────────────────────────


async def test_get_usage_summary_shape(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    summary = await account_tools.get_usage_summary(_ctx())
    assert {"enabled", "total_events", "total_sessions", "sessions_last_30d", "by_type"} <= set(
        summary
    )
    assert isinstance(summary["total_events"], int)
    assert isinstance(summary["by_type"], list)


# ── token list / revoke ──────────────────────────────────────────────────────


async def test_list_tokens_is_prefix_only(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    token_id, secret = await _mint(TEST_USER_ID, "mcp-list-test")

    rows = await account_tools.list_api_tokens(_ctx())
    mine = next(r for r in rows if r["id"] == token_id)
    assert mine["name"] == "mcp-list-test"
    assert mine["revoked"] is False
    assert secret.startswith(mine["token_prefix"])
    assert mine["token_prefix"] != secret
    # Never hashes or secrets - neither as a key nor anywhere in the payload.
    assert "token_hash" not in mine and "token" not in mine
    assert secret not in json.dumps(rows)


async def test_revoke_token_preview_then_confirm(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    token_id, secret = await _mint(TEST_USER_ID, "mcp-revoke-test")
    sm = get_sessionmaker()

    preview = await account_tools.revoke_api_token(_ctx(), token_id)
    assert preview["confirmed"] is False
    assert preview["preview"]["name"] == "mcp-revoke-test"
    assert preview["preview"]["already_revoked"] is False
    assert "[confirm_required]" in preview["message"]
    async with sm() as session:  # dry run: token still verifies
        assert await verify_token(session, secret) is not None

    result = await account_tools.revoke_api_token(_ctx(), token_id, confirm=True)
    assert result == {"revoked": True, "token_id": token_id}
    async with sm() as session:
        assert await verify_token(session, secret) is None

    rows = await account_tools.list_api_tokens(_ctx())
    assert next(r for r in rows if r["id"] == token_id)["revoked"] is True


async def test_revoke_foreign_token_is_not_found(client: AsyncClient) -> None:
    await _ensure_user_b()
    await _set_consent(TEST_USER_ID, True)
    await _set_consent(USER_B_ID, True)
    foreign_id, foreign_secret = await _mint(USER_B_ID, "b-token")

    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await account_tools.revoke_api_token(_ctx(), foreign_id, confirm=True)
    with pytest.raises(MCPToolError, match=r"\[not_found\]"):
        await account_tools.revoke_api_token(_ctx(), "no-such-token", confirm=True)

    # B's token is untouched and B never shows up in A's listing.
    sm = get_sessionmaker()
    async with sm() as session:
        assert await verify_token(session, foreign_secret) is not None
    assert all(r["id"] != foreign_id for r in await account_tools.list_api_tokens(_ctx()))


async def test_revoke_requires_write_scope(client: AsyncClient) -> None:
    await _set_consent(TEST_USER_ID, True)
    token_id, _secret = await _mint(TEST_USER_ID, "mcp-scope-test")
    with pytest.raises(MCPToolError, match=r"\[scope_missing\]"):
        await account_tools.revoke_api_token(
            _ctx(scopes=(SCOPE_ACCOUNT_READ,)), token_id, confirm=True
        )
