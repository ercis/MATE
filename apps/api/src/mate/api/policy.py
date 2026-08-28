"""Admin-vs-user control policy resolver - the generic backbone of the control
framework.

Every controllable thing - a server-side setting (``scope="setting"``, e.g.
``ai.config``) or an installed module's config (``scope="module"``) - has at
most one :class:`~mate.api.db.models.ControlPolicy` row keyed by
``(scope, key)``. Absence of a row means ``control_mode="user"`` (each user owns
their own value, the historical default). ``control_mode="admin"`` means the
single ``admin_value_json`` is the shared value for *all* users.

Kept deliberately import-light - only ``ControlPolicy`` + sqlalchemy, no
``routes/`` import. The same cycle the ``ai_config.py`` docstring warns about
applies here: this module is consulted from ``ai_config.load_ai_config`` and
``modules/loader._make_context``, both of which sit *below* the route layer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import ControlPolicy

SCOPE_SETTING = "setting"
SCOPE_MODULE = "module"
# Per-card module control (mate.api.modules.cards): one policy per settings
# card, keyed ``"<module_id>:<card_id>"``. Supersedes the coarse whole-module
# ``SCOPE_MODULE`` lock (kept only for the 0012 migration's back-compat read).
SCOPE_CARD = "card"

MODE_USER = "user"
MODE_ADMIN = "admin"

PolicyValue = dict[str, Any] | list[Any] | str | int | float | bool | None


async def get_policy(session: AsyncSession, scope: str, key: str) -> ControlPolicy | None:
    return await session.get(ControlPolicy, (scope, key))


async def list_policies(session: AsyncSession, scope: str) -> list[ControlPolicy]:
    """Every policy row for ``scope`` (admin catalog joins these onto its items)."""
    rows = await session.scalars(select(ControlPolicy).where(ControlPolicy.scope == scope))
    return list(rows)


async def set_policy(
    session: AsyncSession,
    scope: str,
    key: str,
    *,
    control_mode: str,
    admin_value: PolicyValue,
    updated_by: str | None,
) -> ControlPolicy:
    """Upsert the policy for ``(scope, key)``.

    Switching to ``control_mode="user"`` clears any stored admin value so a
    later relock starts clean (and never leaks a stale shared secret). Does not
    commit - the caller owns the session/transaction.
    """
    row = await session.get(ControlPolicy, (scope, key))
    stored_value: PolicyValue = None if control_mode == MODE_USER else admin_value
    if row is None:
        row = ControlPolicy(
            scope=scope,
            key=key,
            control_mode=control_mode,
            admin_value_json=stored_value,
            updated_by=updated_by,
        )
        session.add(row)
    else:
        row.control_mode = control_mode
        row.admin_value_json = stored_value
        row.updated_by = updated_by
    return row


async def resolve(
    session: AsyncSession,
    scope: str,
    key: str,
    user_id: str,
) -> tuple[PolicyValue, bool]:
    """Resolve the effective value for ``(scope, key)`` for ``user_id``.

    Returns ``(admin_value_json, True)`` when the policy is admin-controlled -
    the caller uses that shared value verbatim and tells the user it is locked.
    Otherwise returns ``(None, False)`` and the caller falls back to its own
    per-user load. ``user_id`` is accepted for symmetry / future per-user
    overrides; today an admin lock applies uniformly.
    """
    row = await session.get(ControlPolicy, (scope, key))
    if row is not None and row.control_mode == MODE_ADMIN:
        return row.admin_value_json, True
    return None, False
