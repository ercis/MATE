"""Admin "delete user" orchestration - purge every trace of a user.

FK cascade off ``users.id`` clears the DB tables; this module handles
everything cascade does NOT reach, in an order chosen so a failure never
orphans data irrecoverably:

1. guards (self-delete, missing user)
2. cancel the user's queued/running jobs (before any disk teardown)
3. tear down each installed module (BEFORE the cascade - see below)
4. hard-delete the ``users`` row (FK cascade drops the child tables)
5. rmtree the on-disk ``data/users/{id}/`` tree
6. delete the S3 subtree (no-op in local mode)
7. delete the Keycloak account (last, best-effort)
8. evict the in-process JIT-sync cache (mandatory)

Only step 4 is transactional. Steps 2-4 failing => raise (the row survives,
every step is idempotent, safe to retry). Steps 5-8 failing => collect a warning
and continue; NEVER raise after the step-4 commit (that would tell the caller
nothing happened when the account is already gone). Modeled on
``services/log_aggregates.delete_log_and_data``.

Module teardown MUST precede the cascade: ``module_installs`` is
``ondelete=CASCADE``, so once the row is gone the install list is unrecoverable
and a last-owner's shared uploaded-module code / venv / S3 archive can never be
garbage-collected. ``uninstall_for_user`` commits internally, so it is the first
committing step and a mid-loop failure stays cleanly retriable from DB state.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field

import structlog
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth.dependencies import evict_user_from_jit_cache
from mate.api.auth.keycloak_admin import KeycloakAdmin, KeycloakAdminError
from mate.api.config import get_settings
from mate.api.db.models import User
from mate.api.jobs.runtime import JobRuntime
from mate.api.modules.defaults import get_admin_default_ids
from mate.api.modules.installs import user_module_ids
from mate.api.modules.loader import ModuleLoader
from mate.api.modules.uninstall import uninstall_for_user
from mate.api.storage import sync as storage_sync

log = structlog.get_logger(__name__)


@dataclass
class UserDeletionReport:
    """What the purge did. ``deleted`` flips True once the DB commit lands;
    everything after that is best-effort and surfaced via ``warnings``."""

    user_id: str
    deleted: bool = False
    jobs_cancelled: int = 0
    modules_torn_down: int = 0
    keycloak_deleted: bool = False
    keycloak_skipped_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


async def delete_user_and_all_data(
    session: AsyncSession,
    runtime: JobRuntime,
    loader: ModuleLoader,
    keycloak: KeycloakAdmin,
    *,
    target_user_id: str,
    caller_id: str,
) -> UserDeletionReport:
    """Delete *target_user_id* and every artifact they own. See module docstring."""
    # 1. Guards - raise before any side effect. (Self-delete guard also keeps
    #    the acting admin alive, so this route can never strip the last admin.)
    if target_user_id == caller_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    user = await session.get(User, target_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    report = UserDeletionReport(user_id=target_user_id)
    settings = get_settings()

    # 2. Cancel the user's jobs so no worker keeps writing into a directory we
    #    are about to delete.
    report.jobs_cancelled = await runtime.cancel_for_user(target_user_id)

    # 3. Module teardown BEFORE the cascade (module_installs is CASCADE - once
    #    the row is gone we can no longer enumerate last-owned shared artifacts).
    protected = loader.default_module_ids | await get_admin_default_ids(session)
    for module_id in await user_module_ids(session, target_user_id):
        try:
            await uninstall_for_user(
                session, loader, target_user_id, module_id, protected_ids=protected
            )
            report.modules_torn_down += 1
        except Exception as exc:  # a stuck module must not strand the whole delete
            log.warning(
                "user_deletion.module_teardown_failed",
                user_id=target_user_id,
                module_id=module_id,
                error=str(exc),
            )
            report.warnings.append(f"module {module_id} teardown failed: {exc}")

    # 4. DB hard-delete - the atomic tombstone. FK cascade clears the child
    #    tables (incl. dashboard_shares where the user is created_by OR target).
    #    Re-fetch: uninstall_for_user commits internally, expiring the instance.
    user = await session.get(User, target_user_id)
    if user is not None:
        await session.delete(user)
        await session.commit()
    report.deleted = True

    # --- past the point of no return: never raise below, collect warnings. ---

    # 5. On-disk purge (event_logs + module_results + managed watched, all under
    #    the one dir). Off-thread so a large tree doesn't stall the event loop.
    user_dir = settings.users_dir / target_user_id
    try:
        await asyncio.to_thread(shutil.rmtree, user_dir, ignore_errors=True)
    except Exception as exc:  # pragma: no cover - ignore_errors makes this rare
        log.warning("user_deletion.rmtree_failed", user_id=target_user_id, error=str(exc))
        report.warnings.append(f"local data cleanup failed: {exc}")

    # 6. S3 subtree (deletes the whole {prefix}/users/{id}/ prefix; no-op local).
    try:
        await storage_sync.delete_dir_remote(user_dir)
    except Exception as exc:
        log.warning("user_deletion.s3_delete_failed", user_id=target_user_id, error=str(exc))
        report.warnings.append(f"remote storage cleanup failed: {exc}")

    # 7. Keycloak (last external hop, best-effort). A failure here leaves only a
    #    KC account whose next brokered login mints a fresh empty identity.
    try:
        kc = await keycloak.delete_user(target_user_id)
        report.keycloak_deleted = kc.deleted
        report.keycloak_skipped_reason = kc.skipped_reason
        if kc.skipped_reason:
            report.warnings.append(f"Keycloak account not removed: {kc.skipped_reason}")
    except KeycloakAdminError as exc:
        log.warning("user_deletion.keycloak_delete_failed", user_id=target_user_id, error=str(exc))
        report.keycloak_deleted = False
        report.warnings.append(f"Keycloak account not removed: {exc}")

    # 8. Evict the JIT-sync cache (mandatory - else a same-sub re-login skips
    #    re-creating the users row + dirs and 500s on every insert).
    evict_user_from_jit_cache(target_user_id)

    log.info(
        "user_deletion.completed",
        user_id=target_user_id,
        jobs_cancelled=report.jobs_cancelled,
        modules_torn_down=report.modules_torn_down,
        keycloak_deleted=report.keycloak_deleted,
        warnings=len(report.warnings),
    )
    return report
