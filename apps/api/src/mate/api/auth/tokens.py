"""Per-user personal access tokens (PAT).

The only machine-to-machine credential the platform issues. Keycloak hands out
short-lived, browser-bound access tokens, so a non-browser client (an external
MCP client over ``/mcp``) authenticates with a PAT instead. The plaintext
secret is shown to the user once at creation; only its ``blake2b`` hash is
stored, so the DB never holds a usable token. A PAT resolves to its owning user
with **no roles** - it can never reach admin-gated routes.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.models import ApiToken, User

TOKEN_PREFIX = "mate_pat_"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(secret: str) -> str:
    return hashlib.blake2b(secret.encode(), digest_size=32).hexdigest()


async def mint_token(
    session: AsyncSession,
    user_id: str,
    name: str,
    scopes: Iterable[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiToken, str]:
    """Create a PAT for ``user_id``. Returns ``(row, plaintext)``.

    The plaintext is the only time the secret exists in cleartext - persist
    nothing but the hash. ``scopes`` are sanitised to the known taxonomy; an
    empty list means "all read scopes". The caller commits the session.
    """
    # Lazy import: `tokens` is imported during `auth/__init__`, and `mcp.scopes`
    # pulls in the `mcp` package - importing it at module top would re-enter a
    # half-initialised `mate.api.auth`. At call time the cycle is long resolved.
    from mate.api.mcp.scopes import sanitize_scopes

    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=name or "token",
        token_hash=_hash(secret),
        token_prefix=secret[: len(TOKEN_PREFIX) + 4],
        scopes=sanitize_scopes(scopes),
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row, secret


async def verify_token_row(session: AsyncSession, plaintext: str) -> ApiToken | None:
    """Resolve a PAT to its DB row if valid, bumping ``last_used_at``.

    Rejects unknown / revoked / expired tokens and anything without the
    ``mate_pat_`` prefix (cheap gate so a Keycloak JWT never hits this path).
    """
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    row = (
        await session.execute(select(ApiToken).where(ApiToken.token_hash == _hash(plaintext)))
    ).scalar_one_or_none()
    if row is None or row.revoked:
        return None
    if row.expires_at is not None and row.expires_at < _utcnow():
        return None
    row.last_used_at = _utcnow()
    return row


async def verify_token(session: AsyncSession, plaintext: str) -> CurrentUser | None:
    """Resolve a PAT to its (role-less) owner, or ``None`` if it's not valid."""
    row = await verify_token_row(session, plaintext)
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None:
        return None
    return CurrentUser(
        id=user.id,
        email=user.email,
        preferred_username=user.preferred_username,
        name=user.name,
        roles=(),
    )
