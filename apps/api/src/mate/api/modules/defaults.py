"""Admin-declared default modules.

The platform's *implicit* defaults are the modules bundled in the repo
``modules/`` folder (``ModuleLoader.default_module_ids``, derived from disk).
This module adds an *explicit*, admin-controlled default set on top: an admin
can flag any loaded module (typically a user-uploaded one) so every user gets
it. The chosen ids live in a single ``SystemSetting`` row, so the choice is
platform-wide and survives restarts.

"Effective defaults" = bundled ids + admin-declared ids. Callers protect the
on-disk artifact from teardown, seed new users, and reconcile existing users
against that union.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import SystemSetting, User, UserSetting
from mate.api.modules.installs import seed_default_modules

# SystemSetting key holding the admin-declared default module ids (a JSON list).
ADMIN_DEFAULTS_KEY = "modules.default_ids"

# SystemSetting key holding default ids withheld from *new* seeding (a JSON list).
# An id here stays a platform default in every other respect - existing owners
# keep it and its shared code is still protected from teardown - but the per-user
# reconcile and restore-defaults paths stop auto-seeding it. This is the only way
# to stop a *bundled* module (always-default, un-un-defaultable) from reaching
# users who haven't been seeded yet.
EXCLUDED_DEFAULTS_KEY = "modules.default_excluded_ids"

# UserSetting key: per-user record of which default ids have already been
# offered (a JSON list). Mirrors the constant the modules route uses for its
# lazy reconcile; kept here so both the route and the admin toggle share one
# source of truth. See ``routes/modules.py::_reconcile_default_modules``.
DEFAULTS_SEEDED_KEY = "modules_defaults_seeded"


async def get_admin_default_ids(session: AsyncSession) -> set[str]:
    """Return the admin-declared default module ids (empty if never set)."""
    row = await session.get(SystemSetting, ADMIN_DEFAULTS_KEY)
    if row is None or not isinstance(row.value_json, list):
        return set()
    return {str(x) for x in row.value_json}


async def set_admin_default_ids(session: AsyncSession, ids: set[str]) -> None:
    """Persist the admin-declared default set (does not commit)."""
    value = sorted(ids)
    row = await session.get(SystemSetting, ADMIN_DEFAULTS_KEY)
    if row is None:
        session.add(SystemSetting(key=ADMIN_DEFAULTS_KEY, value_json=value))
    else:
        row.value_json = value


async def get_excluded_default_ids(session: AsyncSession) -> set[str]:
    """Return the default ids withheld from new seeding (empty if never set)."""
    row = await session.get(SystemSetting, EXCLUDED_DEFAULTS_KEY)
    if row is None or not isinstance(row.value_json, list):
        return set()
    return {str(x) for x in row.value_json}


async def set_excluded_default_ids(session: AsyncSession, ids: set[str]) -> None:
    """Persist the withheld-from-new-users default set (does not commit)."""
    value = sorted(ids)
    row = await session.get(SystemSetting, EXCLUDED_DEFAULTS_KEY)
    if row is None:
        session.add(SystemSetting(key=EXCLUDED_DEFAULTS_KEY, value_json=value))
    else:
        row.value_json = value


async def mark_seeded_for_user(session: AsyncSession, user_id: str, module_id: str) -> None:
    """Record *module_id* as already-offered to *user_id* so the lazy reconcile
    won't re-add it after the user later uninstalls it.

    A legacy bare-``True`` row can't carry an id list; it's left untouched
    (reconcile treats it as "nothing recorded" and reseeds once, harmlessly).
    """
    row = await session.get(UserSetting, (user_id, DEFAULTS_SEEDED_KEY))
    if row is None:
        session.add(UserSetting(user_id=user_id, key=DEFAULTS_SEEDED_KEY, value_json=[module_id]))
    elif isinstance(row.value_json, list) and module_id not in row.value_json:
        row.value_json = sorted(set(row.value_json) | {module_id})


async def mandate_default_for_all_users(session: AsyncSession, module_id: str) -> list[str]:
    """Grant *module_id* to every existing user and mark it seeded for each.

    This is the eager side of "declare a module default": the module lands on
    every current account immediately (including one that previously uninstalled
    it - the admin mandate re-adds it), while ``mark_seeded_for_user`` makes the
    per-user reconcile respect a *later* uninstall. Does not commit.

    Returns the affected user ids.
    """
    user_ids = list((await session.execute(select(User.id))).scalars().all())
    for uid in user_ids:
        await seed_default_modules(session, uid, [module_id])
        await mark_seeded_for_user(session, uid, module_id)
    return user_ids
