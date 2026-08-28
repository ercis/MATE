"""/api/v1/api-tokens - per-user personal access tokens (PAT) for MCP access.

A PAT is the credential an external MCP client presents to ``/mcp`` (Keycloak
only issues short-lived browser tokens). Browser-facing, authenticated the
normal way (``CurrentUserDep``); the minted token is returned in cleartext
exactly once, on create. Also surfaces MCP connection info + the egress-consent
toggle. See [auth/tokens.py](../auth/tokens.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from mate.api.auth import ADMIN_ROLE, CurrentUserDep, mint_token
from mate.api.config import get_settings
from mate.api.db.models import ApiToken, UserSetting
from mate.api.db.session import SessionDep
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.governance import get_mint_policy, may_mint, mcp_read_only
from mate.api.mcp.scopes import PAT_GRANTABLE_SCOPES, SCOPE_DESCRIPTIONS, sanitize_scopes
from mate.api.schemas.common import utc_isoformat

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TokenInfo(BaseModel):
    id: str
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = None
    revoked: bool


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class CreateTokenResponse(TokenInfo):
    token: str
    """The cleartext secret - shown once, never retrievable again."""


class OAuthInfo(BaseModel):
    authorization_server: str
    client_id: str | None
    metadata_url: str


class ScopeInfo(BaseModel):
    id: str
    description: str


class McpInfo(BaseModel):
    enabled: bool
    url: str
    require_consent: bool
    consented: bool
    mint_allowed: bool
    read_only: bool
    toolsets: list[str]
    scopes_supported: list[ScopeInfo]
    oauth: OAuthInfo


class ConsentState(BaseModel):
    required: bool
    consented: bool


class ConsentUpdate(BaseModel):
    consented: bool


def _to_info(row: ApiToken) -> TokenInfo:
    return TokenInfo(
        id=row.id,
        name=row.name,
        token_prefix=row.token_prefix,
        scopes=list(row.scopes or []),
        created_at=utc_isoformat(row.created_at),
        last_used_at=utc_isoformat(row.last_used_at) if row.last_used_at else None,
        expires_at=utc_isoformat(row.expires_at) if row.expires_at else None,
        revoked=row.revoked,
    )


@router.get("", response_model=list[TokenInfo])
async def list_tokens(session: SessionDep, user: CurrentUserDep) -> list[TokenInfo]:
    rows = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    )
    return [_to_info(r) for r in rows.scalars().all()]


@router.post("", response_model=CreateTokenResponse)
async def create_token(
    body: CreateTokenRequest, session: SessionDep, user: CurrentUserDep
) -> CreateTokenResponse:
    policy = await get_mint_policy(session)
    if not may_mint(policy, ADMIN_ROLE in user.roles):
        raise HTTPException(
            status_code=403, detail="Creating MCP tokens is disabled by your administrator."
        )
    expires_at = None
    if body.expires_in_days is not None:
        expires_at = _utcnow() + timedelta(days=body.expires_in_days)
    row, secret = await mint_token(
        session, user.id, body.name, scopes=sanitize_scopes(body.scopes), expires_at=expires_at
    )
    await session.commit()
    return CreateTokenResponse(token=secret, **_to_info(row).model_dump())


@router.delete("/{token_id}")
async def revoke_token(token_id: str, session: SessionDep, user: CurrentUserDep) -> dict[str, bool]:
    row = await session.get(ApiToken, token_id)
    # 404 (not 403) on a foreign id - never confirm another user's token exists.
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Token not found")
    row.revoked = True
    await session.commit()
    return {"ok": True}


async def _consent_value(session: SessionDep, user_id: str) -> bool:
    row = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
    return bool(row.value_json) if row is not None and row.value_json is not None else False


@router.get("/consent", response_model=ConsentState)
async def get_consent(session: SessionDep, user: CurrentUserDep) -> ConsentState:
    settings = get_settings()
    return ConsentState(
        required=settings.mcp_require_egress_consent,
        consented=await _consent_value(session, user.id),
    )


@router.put("/consent", response_model=ConsentState)
async def set_consent(
    body: ConsentUpdate, session: SessionDep, user: CurrentUserDep
) -> ConsentState:
    row = await session.get(UserSetting, (user.id, MCP_EGRESS_CONSENT_KEY))
    if row is None:
        session.add(
            UserSetting(user_id=user.id, key=MCP_EGRESS_CONSENT_KEY, value_json=body.consented)
        )
    else:
        row.value_json = body.consented
    await session.commit()
    return ConsentState(
        required=get_settings().mcp_require_egress_consent, consented=body.consented
    )


@router.get("/mcp-info", response_model=McpInfo)
async def mcp_info(request: Request, session: SessionDep, user: CurrentUserDep) -> McpInfo:
    """MCP availability, endpoint, OAuth metadata, scopes, and this user's state."""
    from mate.api.mcp.registry import registered_toolsets

    settings = get_settings()
    base = (settings.api_base_url or str(request.base_url)).rstrip("/")
    policy = await get_mint_policy(session)
    return McpInfo(
        enabled=settings.mcp_enabled,
        url=f"{base}/mcp",
        require_consent=settings.mcp_require_egress_consent,
        consented=await _consent_value(session, user.id),
        mint_allowed=may_mint(policy, ADMIN_ROLE in user.roles),
        read_only=await mcp_read_only(),
        toolsets=list(registered_toolsets()),
        scopes_supported=[
            ScopeInfo(id=s, description=SCOPE_DESCRIPTIONS.get(s, "")) for s in PAT_GRANTABLE_SCOPES
        ],
        oauth=OAuthInfo(
            authorization_server=settings.keycloak_issuer,
            client_id=settings.mcp_oauth_client_id,
            metadata_url=f"{base}/.well-known/oauth-protected-resource",
        ),
    )
