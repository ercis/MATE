"""Admin governance + metrics for the MCP server.

Admin-gated: live enable/disable (without restart), the PAT mint policy, the
Prometheus metrics scrape, and an org-wide view of every user's tokens.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import AdminUserDep
from mate.api.config import get_settings
from mate.api.db.models import ApiToken, SystemSetting, User
from mate.api.db.session import SessionDep
from mate.api.mcp import metrics
from mate.api.mcp.governance import (
    MCP_ENABLED_KEY,
    MCP_MINT_POLICY_KEY,
    MCP_READ_ONLY_KEY,
    MintPolicy,
    get_mint_policy,
)
from mate.api.schemas.common import utc_isoformat

router = APIRouter(tags=["mcp-admin"])


class McpAdminConfig(BaseModel):
    boot_enabled: bool  # env MCP_ENABLED (mount happens at boot)
    enabled: bool  # live effective availability
    mint_policy: str
    boot_read_only: bool  # env MCP_READ_ONLY (write tools not even registered)
    read_only: bool  # live effective write lock
    toolsets: list[str]  # registered at boot (env MCP_TOOLSETS)


class McpAdminUpdate(BaseModel):
    enabled: bool | None = None
    mint_policy: MintPolicy | None = None
    read_only: bool | None = None


class AdminTokenInfo(BaseModel):
    id: str
    user_id: str
    user_email: str | None
    name: str
    token_prefix: str
    created_at: str
    last_used_at: str | None
    revoked: bool


async def _set_system(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(SystemSetting, key)
    if row is None:
        session.add(SystemSetting(key=key, value_json=value))
    else:
        row.value_json = value


async def _read_config(session: AsyncSession) -> McpAdminConfig:
    from mate.api.mcp.registry import registered_toolsets

    settings = get_settings()
    enabled_row = await session.get(SystemSetting, MCP_ENABLED_KEY)
    live = settings.mcp_enabled and (
        enabled_row is None or enabled_row.value_json is None or bool(enabled_row.value_json)
    )
    ro_row = await session.get(SystemSetting, MCP_READ_ONLY_KEY)
    read_only = (
        settings.mcp_read_only
        if ro_row is None or ro_row.value_json is None
        else bool(ro_row.value_json)
    )
    policy = await get_mint_policy(session)
    return McpAdminConfig(
        boot_enabled=settings.mcp_enabled,
        enabled=live,
        mint_policy=policy,
        boot_read_only=settings.mcp_read_only,
        read_only=read_only,
        toolsets=list(registered_toolsets()),
    )


@router.get("/system/mcp", response_model=McpAdminConfig)
async def get_mcp_config(session: SessionDep, user: AdminUserDep) -> McpAdminConfig:
    return await _read_config(session)


@router.put("/system/mcp", response_model=McpAdminConfig)
async def put_mcp_config(
    body: McpAdminUpdate, session: SessionDep, user: AdminUserDep
) -> McpAdminConfig:
    if body.enabled is not None:
        await _set_system(session, MCP_ENABLED_KEY, body.enabled)
    if body.mint_policy is not None:
        await _set_system(session, MCP_MINT_POLICY_KEY, body.mint_policy)
    if body.read_only is not None:
        await _set_system(session, MCP_READ_ONLY_KEY, body.read_only)
    await session.commit()
    # The middleware/tool caches pick the new values up within their 5s TTL;
    # reset here so the admin's own next read is immediately consistent.
    from mate.api.mcp.governance import reset_enabled_cache

    reset_enabled_cache()
    return await _read_config(session)


@router.get("/system/mcp-metrics")
async def mcp_metrics(user: AdminUserDep) -> Response:
    raw, content_type = metrics.render()
    return Response(content=raw, media_type=content_type)


@router.get("/admin/api-tokens", response_model=list[AdminTokenInfo])
async def admin_list_tokens(session: SessionDep, user: AdminUserDep) -> list[AdminTokenInfo]:
    rows = (
        await session.execute(
            select(ApiToken, User.email)
            .join(User, ApiToken.user_id == User.id)
            .order_by(ApiToken.created_at.desc())
        )
    ).all()
    return [
        AdminTokenInfo(
            id=t.id,
            user_id=t.user_id,
            user_email=email,
            name=t.name,
            token_prefix=t.token_prefix,
            created_at=utc_isoformat(t.created_at),
            last_used_at=utc_isoformat(t.last_used_at) if t.last_used_at else None,
            revoked=t.revoked,
        )
        for t, email in rows
    ]


@router.delete("/admin/api-tokens/{token_id}")
async def admin_revoke_token(
    token_id: str, session: SessionDep, user: AdminUserDep
) -> dict[str, bool]:
    row = await session.get(ApiToken, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    row.revoked = True
    await session.commit()
    return {"ok": True}
