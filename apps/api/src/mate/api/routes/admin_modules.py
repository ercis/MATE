"""/api/v1/admin/modules - cross-user module ownership dashboard + controls.

Admin-only. Deliberately cross-user: the per-user tenant-isolation invariant
that governs the event bus and the normal ``/modules`` surface does NOT apply
here - these routes read and mutate every user's installs, gated only by the
Keycloak ``admin`` realm role (``AdminUserDep``).

Surfaces:
- who owns which module (and, best-effort, who first uploaded it),
- declaring a module a platform default (eager-seeded to all users),
- force-installing a module onto a user, and force-uninstalling it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from mate.api.auth import AdminUserDep
from mate.api.db.models import ModuleInstall, User
from mate.api.db.session import SessionDep
from mate.api.modules import get_module_loader
from mate.api.modules.defaults import (
    get_admin_default_ids,
    get_excluded_default_ids,
    mandate_default_for_all_users,
    set_admin_default_ids,
    set_excluded_default_ids,
)
from mate.api.modules.installs import record_install, user_owns_module
from mate.api.modules.uninstall import uninstall_for_user

router = APIRouter(prefix="/admin/modules", tags=["admin"])


class AdminModuleOwner(BaseModel):
    user_id: str
    email: str | None = None
    username: str | None = None
    # How this user acquired it: "default" (seeded), "upload" (they uploaded or
    # co-own identical uploaded code), or "admin" (force-installed). Best-effort
    # provenance, not an audit trail.
    source: str | None = None
    installed_at: datetime


class AdminModuleRow(BaseModel):
    id: str
    name: str
    version: str
    category: str
    has_frontend: bool
    # Ships in the repo ``modules/`` folder. Bundled modules are always default
    # for every user and can't be un-defaulted (``default_locked``).
    is_bundled: bool
    # In the effective default set (bundled OR admin-declared) - every user gets it.
    is_default: bool
    default_locked: bool
    # Withheld from *new* seeding: stays a default for existing owners and stays
    # teardown-protected, but is not auto-seeded to users who don't have it yet.
    withheld_from_new_users: bool = False
    owner_count: int
    # Earliest owner with source="upload" - best-effort "who uploaded this".
    # Null for purely bundled/default modules. Ambiguous only when two users
    # uploaded byte-identical code (content-addressed sharing).
    uploaded_by: AdminModuleOwner | None = None
    owners: list[AdminModuleOwner]


class SetDefaultBody(BaseModel):
    is_default: bool


class SetWithholdBody(BaseModel):
    withheld: bool


class ForceInstallBody(BaseModel):
    user_id: str


def _owner(inst: ModuleInstall, u: User) -> AdminModuleOwner:
    return AdminModuleOwner(
        user_id=u.id,
        email=u.email,
        username=u.preferred_username,
        source=inst.source,
        installed_at=inst.installed_at,
    )


def _make_row(
    module_id: str,
    manifest: object | None,
    owners: list[AdminModuleOwner],
    fs_defaults: set[str],
    admin_ids: set[str],
    excluded_ids: set[str],
) -> AdminModuleRow:
    uploaded = [o for o in owners if o.source == "upload"]
    uploaded_by = min(uploaded, key=lambda o: o.installed_at) if uploaded else None
    is_bundled = module_id in fs_defaults
    return AdminModuleRow(
        id=module_id,
        name=getattr(manifest, "name", None) or module_id,
        version=getattr(manifest, "version", "") or "",
        category=getattr(manifest, "category", "other") or "other",
        has_frontend=bool(getattr(getattr(manifest, "frontend", None), "panel", None)),
        is_bundled=is_bundled,
        is_default=is_bundled or module_id in admin_ids,
        default_locked=is_bundled,
        withheld_from_new_users=module_id in excluded_ids,
        owner_count=len(owners),
        uploaded_by=uploaded_by,
        owners=owners,
    )


async def _owners_for(session: SessionDep, module_id: str) -> list[AdminModuleOwner]:
    rows = (
        await session.execute(
            select(ModuleInstall, User)
            .join(User, ModuleInstall.user_id == User.id)
            .where(ModuleInstall.module_id == module_id)
            .order_by(ModuleInstall.installed_at)
        )
    ).all()
    return [_owner(inst, u) for inst, u in rows]


async def _single_row(session: SessionDep, module_id: str) -> AdminModuleRow:
    loader = get_module_loader()
    admin_ids = await get_admin_default_ids(session)
    excluded_ids = await get_excluded_default_ids(session)
    loaded = loader.loaded.get(module_id)
    manifest = loaded.manifest if loaded else None
    owners = await _owners_for(session, module_id)
    return _make_row(
        module_id, manifest, owners, loader.default_module_ids, admin_ids, excluded_ids
    )


@router.get("", response_model=list[AdminModuleRow])
async def list_admin_modules(session: SessionDep, user: AdminUserDep) -> list[AdminModuleRow]:
    """Every module known to the platform (loaded or install-only), joined to
    its owners across all users."""
    try:
        loader = get_module_loader()
    except HTTPException:
        return []

    admin_ids = await get_admin_default_ids(session)
    excluded_ids = await get_excluded_default_ids(session)
    fs_defaults = loader.default_module_ids
    loaded = loader.loaded

    rows = (
        await session.execute(
            select(ModuleInstall, User)
            .join(User, ModuleInstall.user_id == User.id)
            .order_by(ModuleInstall.module_id, ModuleInstall.installed_at)
        )
    ).all()
    owners_by_mod: dict[str, list[AdminModuleOwner]] = defaultdict(list)
    for inst, u in rows:
        owners_by_mod[inst.module_id].append(_owner(inst, u))

    all_ids = set(owners_by_mod) | set(loaded)
    out: list[AdminModuleRow] = []
    for mid in sorted(all_ids):
        loaded_mod = loaded.get(mid)
        manifest = loaded_mod.manifest if loaded_mod else None
        out.append(
            _make_row(
                mid, manifest, owners_by_mod.get(mid, []), fs_defaults, admin_ids, excluded_ids
            )
        )
    return out


@router.put("/{module_id}/default", response_model=AdminModuleRow)
async def set_module_default(
    module_id: str, body: SetDefaultBody, session: SessionDep, user: AdminUserDep
) -> AdminModuleRow:
    """Declare (or undeclare) a module a platform default.

    Turning it on adds the id to the admin default set and eager-seeds it to
    every existing user (and future users via the per-user reconcile). Turning
    it off just stops mandating it - users who already have it keep it.
    """
    loader = get_module_loader()
    if module_id not in loader.loaded:
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not loaded.")

    is_bundled = module_id in loader.default_module_ids
    admin_ids = await get_admin_default_ids(session)

    if body.is_default:
        if not is_bundled and module_id not in admin_ids:
            await set_admin_default_ids(session, admin_ids | {module_id})
            await mandate_default_for_all_users(session, module_id)
            await session.commit()
    else:
        if is_bundled:
            raise HTTPException(
                status_code=400,
                detail="Bundled modules are always default and cannot be un-defaulted.",
            )
        if module_id in admin_ids:
            await set_admin_default_ids(session, admin_ids - {module_id})
            await session.commit()

    return await _single_row(session, module_id)


@router.put("/{module_id}/withhold", response_model=AdminModuleRow)
async def set_module_withheld(
    module_id: str, body: SetWithholdBody, session: SessionDep, user: AdminUserDep
) -> AdminModuleRow:
    """Withhold (or un-withhold) a default module from *new* users.

    A withheld id stays a platform default: existing owners keep it and its
    shared code is still protected from teardown, but the per-user reconcile and
    restore-defaults paths stop auto-seeding it. This is the only way to stop a
    *bundled* module (always default, cannot be un-defaulted) from reaching users
    who haven't been seeded yet. Admin-declared defaults can be withheld too, or
    simply un-defaulted.
    """
    loader = get_module_loader()
    if module_id not in loader.loaded:
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not loaded.")

    admin_ids = await get_admin_default_ids(session)
    is_default = module_id in loader.default_module_ids or module_id in admin_ids
    excluded = await get_excluded_default_ids(session)

    if body.withheld:
        if not is_default:
            raise HTTPException(
                status_code=400,
                detail="Only default modules can be withheld from new users.",
            )
        if module_id not in excluded:
            await set_excluded_default_ids(session, excluded | {module_id})
            await session.commit()
    elif module_id in excluded:
        await set_excluded_default_ids(session, excluded - {module_id})
        await session.commit()

    return await _single_row(session, module_id)


@router.post("/{module_id}/installs", response_model=AdminModuleRow)
async def force_install(
    module_id: str, body: ForceInstallBody, session: SessionDep, user: AdminUserDep
) -> AdminModuleRow:
    """Force-install a loaded module onto a specific user (code is shared and
    already loaded, so this only adds an ownership row)."""
    loader = get_module_loader()
    if module_id not in loader.loaded:
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not loaded.")
    if await session.get(User, body.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")

    await record_install(session, body.user_id, module_id, source="admin")
    await session.commit()
    await loader.bus.publish(
        "module.installed", {"id": module_id, "source": "admin", "user_id": body.user_id}
    )
    return await _single_row(session, module_id)


@router.delete("/{module_id}/installs/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def force_uninstall(
    module_id: str, user_id: str, session: SessionDep, user: AdminUserDep
) -> None:
    """Force-uninstall a module from a specific user. Tears down the shared
    artifact only if that user was the last owner and the id isn't a default."""
    loader = get_module_loader()
    if not await user_owns_module(session, user_id, module_id):
        raise HTTPException(status_code=404, detail="User does not have this module installed.")
    admin_ids = await get_admin_default_ids(session)
    protected = loader.default_module_ids | admin_ids
    await uninstall_for_user(session, loader, user_id, module_id, protected_ids=protected)
