"""Per-user module uninstall with last-owner artifact teardown.

Shared by the user-facing ``DELETE /modules/{id}`` route and the admin
force-uninstall route so both apply identical semantics: drop this user's
ownership row, and only when the *last* owner leaves - and the id isn't a
protected default - tear the shared artifact down (unload, delete on-disk
upload, drop the S3 archive).
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.config import get_settings
from mate.api.modules.installer import remove_module_artifacts
from mate.api.modules.installs import owner_count, remove_install
from mate.api.storage.module_archive import delete_module_archive_sync

if TYPE_CHECKING:
    from mate.api.modules.loader import ModuleLoader


async def uninstall_for_user(
    session: AsyncSession,
    loader: ModuleLoader,
    user_id: str,
    module_id: str,
    *,
    protected_ids: set[str],
) -> None:
    """Remove *user_id*'s install of *module_id* and tear down the shared
    artifact iff it was the last owner and the id isn't in *protected_ids*.

    *protected_ids* is the effective default set (bundled + admin-declared) - a
    default's shared code is never deleted even by its last owner. Publishes a
    ``module.uninstalled`` event scoped to *user_id*.
    """
    await remove_install(session, user_id, module_id)
    await session.commit()

    if module_id not in protected_ids and await owner_count(session, module_id) == 0:
        target = get_settings().uploaded_modules_dir.resolve() / module_id
        await loader.unload_one(module_id)
        if target.exists():
            remove_module_artifacts(target)
            shutil.rmtree(target, ignore_errors=True)
        # Drop the S3 source archive too (no-op in local mode) so a later boot
        # doesn't re-materialise a module the last owner just removed.
        await asyncio.to_thread(delete_module_archive_sync, module_id)

    # Scope the event to this user so the stream only notifies their sessions -
    # other owners' module lists are unaffected.
    await loader.bus.publish("module.uninstalled", {"id": module_id, "user_id": user_id})
