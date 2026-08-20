"""FastAPI dependency that resolves the current Keycloak user from a JWT.

Used like the existing ``SessionDep`` pattern in ``db/session.py``:

    @router.get("/things")
    async def list_things(user: CurrentUserDep, session: SessionDep):
        return await session.scalars(
            select(Thing).where(Thing.user_id == user.id)
        )

On the first sighting of a `sub`, the corresponding ``users`` row is created
(or its ``last_seen_at`` bumped). A process-local set caches seen IDs so the
upsert runs at most once per process per user.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth.jwks import get_signing_key
from mate.api.config import get_settings
from mate.api.db.models import User
from mate.api.db.session import SessionDep


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    preferred_username: str | None
    name: str | None
    roles: tuple[str, ...]


_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid bearer token",
    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
)

_seen_user_ids: set[str] = set()

# Demo/dev login bypass - see Settings.demo_mode. The web app mints this exact
# sentinel as the session access token for the demo provider; when demo_mode is
# on we accept it verbatim (no JWKS) and resolve the fixed demo user below. The
# string is intentionally not a JWT so it can never collide with a real token.
DEMO_ACCESS_TOKEN = "demo-access-token"
DEMO_USER = CurrentUser(
    id="demo-user",
    email="demo@mate.local",
    preferred_username="demo",
    name="Demo User",
    roles=(),
)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise _UNAUTH
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise _UNAUTH
    return parts[1].strip()


def _claim_str(claims: dict[str, object], key: str) -> str | None:
    value = claims.get(key)
    return value if isinstance(value, str) and value else None


def _extract_roles(claims: dict[str, object]) -> tuple[str, ...]:
    realm = claims.get("realm_access")
    if isinstance(realm, dict):
        roles = realm.get("roles")
        if isinstance(roles, list):
            return tuple(r for r in roles if isinstance(r, str))
    return ()


async def _decode_token(token: str) -> dict[str, object]:
    settings = get_settings()
    try:
        unverified = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _UNAUTH from exc

    kid = unverified.get("kid")
    if not isinstance(kid, str):
        raise _UNAUTH

    try:
        key = await get_signing_key(kid)
    except Exception as exc:
        raise _UNAUTH from exc

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=settings.keycloak_audience,
            issuer=settings.keycloak_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise _UNAUTH from exc

    if not isinstance(claims, dict):
        raise _UNAUTH
    return claims


async def _jit_sync_user(session: AsyncSession, user: CurrentUser) -> None:
    """Insert or refresh the ``users`` row corresponding to ``user``."""
    if user.id in _seen_user_ids:
        return

    existing = await session.get(User, user.id)
    if existing is None:
        session.add(
            User(
                id=user.id,
                email=user.email,
                preferred_username=user.preferred_username,
                name=user.name,
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            # A parallel first request from the same user inserted the row
            # between our get() and flush(). Recover by rolling back and
            # treating it as an update rather than 500-ing.
            await session.rollback()
            existing = await session.get(User, user.id)

    if existing is not None:
        existing.email = user.email
        existing.preferred_username = user.preferred_username
        existing.name = user.name
        await session.flush()

    _seen_user_ids.add(user.id)
    # Ensure the on-disk per-user dirs exist before any handler tries to write
    # to data/users/<id>/event_logs/.
    get_settings().ensure_user_dirs(user.id)


async def get_current_user_from_token(token: str, session: AsyncSession) -> CurrentUser:
    settings = get_settings()
    if settings.demo_mode and token == DEMO_ACCESS_TOKEN:
        user = replace(DEMO_USER, roles=("admin",)) if settings.demo_admin else DEMO_USER
        await _jit_sync_user(session, user)
        return user
    claims = await _decode_token(token)
    sub = _claim_str(claims, "sub")
    if not sub:
        raise _UNAUTH
    user = CurrentUser(
        id=sub,
        email=_claim_str(claims, "email"),
        preferred_username=_claim_str(claims, "preferred_username"),
        name=_claim_str(claims, "name"),
        roles=_extract_roles(claims),
    )
    await _jit_sync_user(session, user)
    return user


async def get_current_user(request: Request, session: SessionDep) -> CurrentUser:
    token = _extract_bearer(request.headers.get("authorization"))
    user = await get_current_user_from_token(token, session)
    # Expose the resolved id on the ASGI scope state so the usage-tracking
    # middleware can attribute a business operation without re-decoding the JWT.
    request.state.user_id = user.id
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


# Realm role that gates cross-user/admin operations (e.g. the full-database
# export). Assign it to an account in the Keycloak admin console under
# Realm roles → admin → Users in role.
ADMIN_ROLE = "admin"


async def require_admin(user: CurrentUserDep) -> CurrentUser:
    """Like ``CurrentUserDep`` but additionally requires the ``admin`` realm
    role. Used by endpoints that read across *all* users' data.
    """
    if ADMIN_ROLE not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


AdminUserDep = Annotated[CurrentUser, Depends(require_admin)]


def reset_user_cache_for_tests() -> None:  # pragma: no cover - test helper
    _seen_user_ids.clear()
