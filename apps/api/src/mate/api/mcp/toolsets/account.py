"""Account toolset: usage summary + own API-token list/revoke.

No PAT minting over MCP (an agent must never be able to persist its own
access) and no AI-provider config (keys). Token reads are prefix-only - the
hash and the secret never leave the database.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import ApiToken
from mate.api.mcp.core import MCPContext, authz, confirm_preview, guarded
from mate.api.mcp.errors import CODE_NOT_FOUND, tool_error
from mate.api.mcp.registry import mcp_tool
from mate.api.mcp.scopes import SCOPE_ACCOUNT_READ, SCOPE_ACCOUNT_WRITE
from mate.api.schemas.common import utc_isoformat


def _token_info(row: ApiToken) -> dict[str, Any]:
    """Prefix-only token row (mirrors ``routes/api_tokens.TokenInfo``) - never
    the hash, never a secret."""
    return {
        "id": row.id,
        "name": row.name,
        "token_prefix": row.token_prefix,
        "scopes": list(row.scopes or []),
        "created_at": utc_isoformat(row.created_at),
        "last_used_at": utc_isoformat(row.last_used_at) if row.last_used_at else None,
        "expires_at": utc_isoformat(row.expires_at) if row.expires_at else None,
        "revoked": row.revoked,
    }


@mcp_tool(toolset="account", idempotent=True)
async def get_usage_summary(ctx: MCPContext) -> dict[str, Any]:
    """Your usage-analytics summary: event/session totals and by-type counts."""
    p = await authz(ctx, SCOPE_ACCOUNT_READ)

    async def _impl() -> dict[str, Any]:
        # Reuse the /usage/summary route internals verbatim (same aggregates).
        from mate.api.routes.analytics import get_summary

        sm = get_sessionmaker()
        async with sm() as session:
            summary = await get_summary(session, p.user)
        return summary.model_dump(mode="json")

    return await guarded(p, "get_usage_summary", {}, _impl())


@mcp_tool(toolset="account", idempotent=True)
async def list_api_tokens(ctx: MCPContext) -> list[dict[str, Any]]:
    """List your API tokens (id, name, prefix, scopes, timestamps, revoked).

    Prefix-only: a token's secret is shown once at creation in the web UI and
    is never retrievable here.
    """
    p = await authz(ctx, SCOPE_ACCOUNT_READ)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(ApiToken)
                        .where(ApiToken.user_id == p.user.id)
                        .order_by(ApiToken.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [_token_info(r) for r in rows]

    return await guarded(p, "list_api_tokens", {}, _impl())


@mcp_tool(toolset="account", write=True, destructive=True)
async def revoke_api_token(ctx: MCPContext, token_id: str, confirm: bool = False) -> dict[str, Any]:
    """Revoke one of your API tokens (irreversible). Dry-runs without ``confirm``."""
    p = await authz(ctx, SCOPE_ACCOUNT_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await session.get(ApiToken, token_id)
            # 404 (not 403) on a foreign id - never confirm another user's
            # token exists.
            if row is None or row.user_id != p.user.id:
                raise tool_error(CODE_NOT_FOUND, "Token not found")
            if not confirm:
                return confirm_preview(
                    "revoke_api_token",
                    {
                        "token_id": row.id,
                        "name": row.name,
                        "token_prefix": row.token_prefix,
                        "already_revoked": row.revoked,
                    },
                )
            row.revoked = True
            await session.commit()
        return {"revoked": True, "token_id": token_id}

    return await guarded(
        p, "revoke_api_token", {"token_id": token_id, "confirm": confirm}, _impl(), mutation=True
    )
