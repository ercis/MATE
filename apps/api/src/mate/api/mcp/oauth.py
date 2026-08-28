"""OAuth 2.1 resource-server discovery for the MCP server.

The MCP server is an OAuth **resource server**; Keycloak is the authorization
server. We don't implement the AS - we advertise it (RFC 9728 protected-
resource metadata) so a compliant MCP client can discover Keycloak and run the
auth-code + PKCE flow against it, then call ``/mcp`` with the resulting Bearer
JWT (validated by the existing JWKS path). The OAuth client is **pre-registered**
in Keycloak (DCR is not assumed); its ``client_id`` is surfaced via
``/api/v1/api-tokens/mcp-info``.

Security: the advertised base URL comes **only** from the configured
``api_base_url`` - never from the request ``Host`` header - so a forged Host
can't point a client's OAuth discovery at an attacker. OAuth discovery is
therefore only advertised when ``API_BASE_URL`` is set.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.types import Scope

from mate.api.config import get_settings
from mate.api.mcp.scopes import ALL_SCOPES

router = APIRouter(tags=["mcp-oauth"])

_METADATA_PATH = "/.well-known/oauth-protected-resource"


def protected_resource_metadata_url(scope: Scope) -> str | None:
    """Absolute URL of the protected-resource metadata, for ``WWW-Authenticate``.

    Returns ``None`` (so the 401 falls back to a plain ``Bearer`` challenge)
    unless MCP is enabled and ``api_base_url`` is configured.
    """
    settings = get_settings()
    if not settings.mcp_enabled or not settings.api_base_url:
        return None
    return f"{settings.api_base_url.rstrip('/')}{_METADATA_PATH}"


def _metadata(base: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "resource": f"{base.rstrip('/')}/mcp",
        "authorization_servers": [settings.keycloak_issuer],
        "scopes_supported": list(ALL_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{base.rstrip('/')}/api/v1/api-tokens/mcp-info",
    }


@router.get(_METADATA_PATH)
async def protected_resource_metadata() -> dict[str, Any]:
    settings = get_settings()
    if not settings.api_base_url:
        raise HTTPException(
            status_code=503,
            detail="OAuth discovery requires API_BASE_URL to be configured.",
        )
    return _metadata(settings.api_base_url)
