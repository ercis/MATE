"""Keycloak admin-REST client for server-to-server user management.

Used ONLY by the admin "delete user" flow to remove an account from Keycloak
after the local data purge. Separate from ``auth/jwks.py`` (which only validates
tokens): this needs a confidential client whose service account holds the
``realm-management`` client role ``manage-users``.

When the admin client is not configured (``keycloak_admin_base_url`` /
``keycloak_admin_client_secret`` unset - dev, demo, tests, or a prod misconfig),
:meth:`KeycloakAdmin.delete_user` is a logged no-op returning a "not configured"
skip reason. That keeps local/demo working with no Keycloak and makes a prod
misconfiguration fail SAFE: the Mate-side purge still completes and the caller
is told the KC account was not touched, instead of a silent partial delete.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx
import structlog

from mate.api.config import Settings, get_settings

log = structlog.get_logger(__name__)

# Refresh the service token this many seconds before its stated expiry.
_TOKEN_SKEW_SECONDS = 30.0


class KeycloakAdminError(RuntimeError):
    """A configured Keycloak admin call failed (auth / permission / 5xx)."""


@dataclass
class KeycloakDeleteResult:
    """Outcome of :meth:`KeycloakAdmin.delete_user`.

    ``deleted`` is True when the account is gone from Keycloak (204, or 404 =
    already absent). ``skipped_reason`` is set only when the client is not
    configured (nothing was attempted).
    """

    deleted: bool
    skipped_reason: str | None = None


class KeycloakAdmin:
    """Minimal admin client: cache a client-credentials token, delete a user."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expiry: float = 0.0  # time.monotonic() deadline

    def _settings(self) -> Settings:
        return get_settings()

    def is_configured(self) -> bool:
        s = self._settings()
        return bool(
            s.keycloak_admin_base_url
            and s.keycloak_admin_client_id
            and s.keycloak_admin_client_secret
        )

    async def _get_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expiry:
            return self._token
        async with self._lock:
            # Re-check after grabbing the lock (another caller may have refreshed).
            now = time.monotonic()
            if self._token and now < self._token_expiry:
                return self._token
            s = self._settings()
            base = (s.keycloak_admin_base_url or "").rstrip("/")
            url = f"{base}/realms/{s.keycloak_realm}/protocol/openid-connect/token"
            data = {
                "grant_type": "client_credentials",
                "client_id": s.keycloak_admin_client_id or "",
                "client_secret": s.keycloak_admin_client_secret or "",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, data=data)
            if resp.status_code != 200:
                raise KeycloakAdminError(
                    f"Keycloak token request failed: {resp.status_code} {resp.text[:200]}"
                )
            body = resp.json()
            token = body.get("access_token")
            if not token:
                raise KeycloakAdminError("Keycloak token response carried no access_token")
            expires_in = float(body.get("expires_in", 60))
            self._token = token
            self._token_expiry = time.monotonic() + max(1.0, expires_in - _TOKEN_SKEW_SECONDS)
            return token

    async def delete_user(self, user_id: str) -> KeycloakDeleteResult:
        """Delete the Keycloak user whose id equals *user_id* (the JWT ``sub``).

        204 or 404 (already gone) => deleted. 401/403 => the service account
        lacks ``manage-users`` (or the token is stale) => :class:`KeycloakAdminError`.
        Not configured => no-op with a skip reason.
        """
        if not self.is_configured():
            log.info("keycloak_admin.delete_skipped_unconfigured", user_id=user_id)
            return KeycloakDeleteResult(deleted=False, skipped_reason="not configured")

        token = await self._get_token()
        s = self._settings()
        base = (s.keycloak_admin_base_url or "").rstrip("/")
        url = f"{base}/admin/realms/{s.keycloak_realm}/users/{user_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers={"Authorization": f"Bearer {token}"})

        if resp.status_code in (204, 404):
            log.info("keycloak_admin.user_deleted", user_id=user_id, status=resp.status_code)
            return KeycloakDeleteResult(deleted=True)
        if resp.status_code in (401, 403):
            # Drop the token so the next attempt re-fetches (covers expiry).
            self._token = None
            raise KeycloakAdminError(
                f"Keycloak refused user delete: {resp.status_code} "
                "(check the service account's realm-management:manage-users role)"
            )
        raise KeycloakAdminError(
            f"Keycloak user delete failed: {resp.status_code} {resp.text[:200]}"
        )


@lru_cache(maxsize=1)
def get_keycloak_admin() -> KeycloakAdmin:
    return KeycloakAdmin()


def reset_for_tests() -> None:  # pragma: no cover - test helper
    get_keycloak_admin.cache_clear()
