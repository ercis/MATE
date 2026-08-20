"""Per-user module install ownership (reference counting).

Module code lives once on shared disk and loads once into the process; these
helpers track *who* installed each module so listing, availability, and
deletion are per-user. The physical artifact is only removed when the last
owner uninstalls - callers use :func:`owner_count` to decide.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import ModuleInstall


async def record_install(
    session: AsyncSession, user_id: str, module_id: str, source: str | None
) -> None:
    """Idempotently mark *module_id* as installed for *user_id*."""
    row = await session.get(ModuleInstall, (user_id, module_id))
    if row is None:
        session.add(ModuleInstall(user_id=user_id, module_id=module_id, source=source))
    elif source is not None:
        row.source = source


async def user_module_ids(session: AsyncSession, user_id: str) -> set[str]:
    rows = await session.execute(
        select(ModuleInstall.module_id).where(ModuleInstall.user_id == user_id)
    )
    return {module_id for (module_id,) in rows.all()}


async def user_owns_module(session: AsyncSession, user_id: str, module_id: str) -> bool:
    return await session.get(ModuleInstall, (user_id, module_id)) is not None


async def module_owned_by_other(session: AsyncSession, user_id: str, module_id: str) -> bool:
    """True if *module_id* is installed by any user other than *user_id*.

    Used to reject an upload whose id collides with another user's custom
    module - module *code* is shared in-process, so two users can't own
    different code under the same id.
    """
    result = await session.execute(
        select(func.count())
        .select_from(ModuleInstall)
        .where(
            ModuleInstall.module_id == module_id,
            ModuleInstall.user_id != user_id,
        )
    )
    return int(result.scalar_one()) > 0


async def seed_default_modules(
    session: AsyncSession, user_id: str, default_ids: Iterable[str]
) -> None:
    """Grant *user_id* ownership of every id in *default_ids* it doesn't
    already own. Implemented as a single race-safe upsert so parallel
    first-login requests don't collide.
    """
    ids = list(default_ids)
    if not ids:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = sqlite_insert(ModuleInstall).values(
        [
            {
                "user_id": user_id,
                "module_id": mid,
                "source": "default",
                "installed_at": now,
            }
            for mid in ids
        ]
    )
    await session.execute(stmt.on_conflict_do_nothing())


async def remove_install(session: AsyncSession, user_id: str, module_id: str) -> None:
    await session.execute(
        delete(ModuleInstall).where(
            ModuleInstall.user_id == user_id,
            ModuleInstall.module_id == module_id,
        )
    )


async def owner_count(session: AsyncSession, module_id: str) -> int:
    """How many users still have *module_id* installed."""
    result = await session.execute(
        select(func.count()).select_from(ModuleInstall).where(ModuleInstall.module_id == module_id)
    )
    return int(result.scalar_one())
