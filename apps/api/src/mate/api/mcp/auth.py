"""Authentication + principal resolution for the mounted MCP app.

A thin pure-ASGI middleware (not BaseHTTPMiddleware - that buffers and would
break the streamable-HTTP/SSE responses). It authenticates every request to
``/mcp`` and 401s unauthenticated ones *before* the MCP machinery runs, then
stashes the resolved :class:`MCPPrincipal` on the ASGI ``scope``. The
streamable-HTTP handler builds its ``Request`` from that same scope, so a tool
reads the principal back via ``ctx.request_context.request.scope`` - robust
across the session manager's task boundaries (a ``ContextVar`` would not be).

A token is either a Mate PAT (``mate_pat_…``, with its own granted scopes) or a
Keycloak JWT (OAuth, scopes mapped from the token's ``scope`` claim - read-only
fallback when it carries none of ours). The middleware also enforces the live
admin enable toggle, an Origin allowlist and the per-user rate limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from mate.api.auth import (
    TOKEN_PREFIX,
    CurrentUser,
    verify_token_row,
)
from mate.api.auth.dependencies import DEMO_ACCESS_TOKEN, get_current_user_and_claims
from mate.api.config import get_settings
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import User
from mate.api.mcp.scopes import effective_scopes, scopes_from_oauth_claims

log = structlog.get_logger("mcp.audit")
SCOPE_PRINCIPAL_KEY = "mate_principal"


@dataclass(frozen=True)
class MCPPrincipal:
    """The authenticated caller for one MCP request."""

    user: CurrentUser
    token_id: str | None  # the PAT row id, or None for an OAuth/JWT principal
    scopes: tuple[str, ...]
    auth_type: str  # "pat" | "oauth"


async def resolve_mcp_principal(token: str, session: AsyncSession) -> MCPPrincipal | None:
    """PAT first (cheap prefix gate, carries granted scopes), then Keycloak JWT."""
    # The demo-login bypass is dev-only; it must never authenticate an external
    # MCP client, even if DEMO_MODE is mistakenly left on alongside MCP in prod.
    if token == DEMO_ACCESS_TOKEN:
        return None
    row = await verify_token_row(session, token)
    if row is not None:
        user = await session.get(User, row.user_id)
        if user is None:
            return None
        cu = CurrentUser(
            id=user.id,
            email=user.email,
            preferred_username=user.preferred_username,
            name=user.name,
            roles=(),
        )
        return MCPPrincipal(
            user=cu, token_id=row.id, scopes=effective_scopes(row.scopes), auth_type="pat"
        )
    try:
        cu, claims = await get_current_user_and_claims(token, session)
    except Exception:
        return None
    # The token's `scope` claim maps onto the MCP taxonomy; a token carrying
    # none of our scopes falls back to the read scopes (pre-scope clients).
    # `admin` survives only when the same token also carries the admin role.
    return MCPPrincipal(
        user=cu,
        token_id=None,
        scopes=scopes_from_oauth_claims(claims, cu.roles),
        auth_type="oauth",
    )


def _bearer(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            parts = value.decode("latin-1").split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
                return parts[1].strip()
    return None


class MCPAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Local imports avoid import-time coupling to the limits/governance
        # modules (and keep this file importable before they load).
        from mate.api.mcp.governance import mcp_runtime_enabled
        from mate.api.mcp.limits import check_rate_limit

        if not await mcp_runtime_enabled():
            log.info("mcp.rejected", reason="disabled", client=_client(scope))
            await _send_json(send, 503, {"error": "unavailable", "detail": "MCP is disabled."})
            return

        # DNS-rebinding guard (MCP spec recommendation): a browser-borne request
        # carries an Origin header - only same-app origins may pass. Non-browser
        # clients (Claude Code, Codex, server-side connectors) send no Origin.
        origin = _origin(scope)
        if origin is not None and not _origin_allowed(origin):
            log.info("mcp.rejected", reason="bad_origin", origin=origin, client=_client(scope))
            await _send_json(send, 403, {"error": "forbidden", "detail": "Origin not allowed."})
            return

        token = _bearer(scope)
        principal: MCPPrincipal | None = None
        # Cheap pre-DB filter: only a plausible PAT or JWT touches the DB / JWKS,
        # so garbage bearers can't amplify into a DB SELECT + decode per request.
        if token and _plausible_token(token):
            sm = get_sessionmaker()
            async with sm() as session:
                principal = await resolve_mcp_principal(token, session)
                await session.commit()

        if principal is None:
            log.info("mcp.rejected", reason="unauthenticated", client=_client(scope))
            await _send_json(
                send,
                401,
                {
                    "error": "unauthorized",
                    "detail": "A valid bearer token (mate_pat_…) is required.",
                },
                www_authenticate=_www_authenticate(scope),
            )
            return

        allowed, retry_after = check_rate_limit(principal.user.id)
        if not allowed:
            from mate.api.mcp import metrics

            metrics.record_rate_limited()
            log.info(
                "mcp.rejected",
                reason="rate_limited",
                user_id=principal.user.id,
                client=_client(scope),
            )
            await _send_json(
                send,
                429,
                {"error": "rate_limited", "detail": "Too many requests."},
                extra_headers=[(b"retry-after", str(retry_after).encode())],
            )
            return

        scope[SCOPE_PRINCIPAL_KEY] = principal
        await self.app(scope, receive, send)


def _plausible_token(token: str) -> bool:
    """A PAT or a 3-segment JWT - anything else is rejected before any DB/JWKS hit."""
    return token.startswith(TOKEN_PREFIX) or token.count(".") == 2


def _origin(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"origin":
            return value.decode("latin-1").strip().rstrip("/").lower() or None
    return None


def _origin_allowed(origin: str) -> bool:
    """Same-app origins (CORS list + the public base URL) and loopback only."""
    if origin.startswith(("http://localhost", "http://127.0.0.1", "https://localhost")):
        return True
    settings = get_settings()
    allowed = {o.rstrip("/").lower() for o in settings.cors_origins}
    base = settings.api_base_url
    if base:
        parts = base.split("/")
        if len(parts) >= 3:
            allowed.add(f"{parts[0]}//{parts[2]}".lower())
    return origin in allowed


def _client(scope: Scope) -> str | None:
    c = scope.get("client")
    return c[0] if isinstance(c, (tuple, list)) and c else None


def _www_authenticate(scope: Scope) -> bytes:
    """RFC 9728: point the client at the protected-resource metadata so it can
    discover the authorization server (Keycloak) and start the OAuth flow."""
    from mate.api.mcp.oauth import protected_resource_metadata_url

    url = protected_resource_metadata_url(scope)
    if url:
        return f'Bearer resource_metadata="{url}"'.encode()
    return b'Bearer error="invalid_token"'


async def _send_json(
    send: Send,
    status: int,
    body: dict[str, object],
    *,
    www_authenticate: bytes | None = None,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
    if www_authenticate is not None:
        headers.append((b"www-authenticate", www_authenticate))
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": json.dumps(body).encode()})
