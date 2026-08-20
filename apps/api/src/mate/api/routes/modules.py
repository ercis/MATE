"""/api/v1/modules - list manifests, per-log availability, get/put config.

Module-defined routes are mounted by the loader (phase 5) directly onto the
app under ``/api/v1/modules/{id}/...`` - they do **not** go through this
router; this router covers the platform's own module-meta surface.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.config import get_settings
from mate.api.db.models import ModuleConfig, ModuleLayout, UserSetting
from mate.api.db.session import SessionDep
from mate.api.jobs.runtime import get_job_runtime
from mate.api.modules import get_module_loader
from mate.api.modules.availability import Availability
from mate.api.modules.install_jobs import (
    JOB_TYPE_GIT,
    JOB_TYPE_REGISTRY,
    JOB_TYPE_UPLOAD,
)
from mate.api.modules.installer import remove_module_artifacts
from mate.api.modules.installs import (
    owner_count,
    remove_install,
    seed_default_modules,
    user_module_ids,
    user_owns_module,
)
from mate.api.policy import SCOPE_MODULE, resolve
from mate.api.schemas.event_logs import LogModel

# UserSetting key holding the per-user record of which default module ids have
# already been offered to a user (a JSON list). Seeding grants only the defaults
# that are new since the last visit, so a freshly bundled module reaches
# existing users automatically while a default the user intentionally removed
# stays gone (its id is already in the recorded set). Legacy rows hold a bare
# `true` (the old one-shot "seeded at least once" flag).
_DEFAULTS_SEEDED_KEY = "modules_defaults_seeded"

router = APIRouter(prefix="/modules", tags=["modules"])


async def _assert_owns_module(session: SessionDep, user_id: str, module_id: str) -> None:
    """404 unless *user_id* has *module_id* installed.

    Module code is shared in-process, so a non-owner could otherwise read a
    module's manifest/config they never installed. 404 (not 403) avoids
    leaking which module ids exist.
    """
    if not await user_owns_module(session, user_id, module_id):
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not installed.")


async def _reconcile_default_modules(
    session: SessionDep, user_id: str, default_ids: set[str]
) -> None:
    """Grant *user_id* any default modules not previously offered to them.

    The set of already-offered default ids is recorded per user. On each visit
    we grant only the defaults that are new since last time, then extend the
    record - so a newly bundled default shows up for existing users without a
    re-seed, while a default the user intentionally uninstalled is not brought
    back (its id is already in the recorded set).

    A legacy row stores a bare ``True`` (the old one-shot flag); we can't recover
    which ids it covered, so it is treated as "nothing recorded" and the full
    current default set is reconciled once. That can re-grant a default removed
    *before* this upgrade, but only that once - afterwards the row is an id list
    and removals stick.
    """
    row = await session.get(UserSetting, (user_id, _DEFAULTS_SEEDED_KEY))
    tracked = row is not None and isinstance(row.value_json, list)
    recorded: set[str] = set(row.value_json) if tracked else set()  # type: ignore[arg-type]
    new_ids = default_ids - recorded
    if not new_ids and tracked:
        return

    await seed_default_modules(session, user_id, new_ids)
    merged = sorted(recorded | default_ids)
    if row is None:
        session.add(UserSetting(user_id=user_id, key=_DEFAULTS_SEEDED_KEY, value_json=merged))
    else:
        row.value_json = merged


class ModuleSummary(BaseModel):
    id: str
    name: str
    version: str
    category: str
    description: str | None = None
    author: str | None = None
    license: str | None = None
    provides: list[str]
    consumes: list[str]
    has_frontend: bool
    enabled: bool = True
    is_confidential_safe: bool = False
    availability: Availability | None = None


class ModuleConfigPayload(BaseModel):
    config: dict[str, Any] = {}
    enabled: bool = True
    # Set by GET when an admin has locked this module's config for all users;
    # the detail page then renders read-only. Ignored on PUT input.
    controlled_by_admin: bool = False


@router.get("", response_model=list[ModuleSummary])
async def list_modules(
    session: SessionDep,
    user: CurrentUserDep,
    log_id: Annotated[str | None, Query()] = None,
) -> list[ModuleSummary]:
    try:
        loader = get_module_loader()
    except HTTPException:
        return []
    manifests = loader.manifests()
    if not manifests:
        return []

    # Lazily reconcile the per-user default set. We do it here (not in the auth
    # layer) because this is the path that already holds both the loader and a
    # session, and it runs on every visit to the modules surface.
    await _reconcile_default_modules(session, user.id, loader.default_module_ids)

    # Per-user visibility: only modules this user has installed. The loader
    # holds every module loaded into the process (shared), so we intersect.
    owned = await user_module_ids(session, user.id)
    manifests = [m for m in manifests if m.id in owned]
    if not manifests:
        return []

    avail_map: dict[str, Availability] = {}
    if log_id is not None:
        log_row = await get_owned_event_log(session, log_id, user.id)
        avail_map = loader.availability_for(
            detected_schema=log_row.detected_schema,
            events_count=log_row.events_count,
            cases_count=log_row.cases_count,
            installed_module_ids=owned,
            log_model=log_row.log_model,
        )

    rows = await session.execute(
        select(ModuleConfig.module_id, ModuleConfig.enabled).where(ModuleConfig.user_id == user.id)
    )
    enabled_map: dict[str, bool] = {module_id: enabled for module_id, enabled in rows.all()}

    return [
        ModuleSummary(
            id=m.id,
            name=m.name,
            version=m.version,
            category=m.category,
            description=m.description,
            author=m.author,
            license=m.license,
            provides=list(m.provides),
            consumes=list(m.consumes),
            has_frontend=bool(m.frontend.panel),
            enabled=enabled_map.get(m.id, m.default_enabled),
            is_confidential_safe=m.is_confidential_safe,
            availability=avail_map.get(m.id),
        )
        for m in manifests
    ]


class DashboardCard(BaseModel):
    """One placeable card the Dashboards palette can drop onto a board.

    Aggregated from every owned module's ``frontend.widgets`` so the palette
    can render the full catalog without loading any bundle - the bundle itself
    is fetched lazily by ``useWidget(module_id, widget_id)`` when the card is
    actually mounted.
    """

    module_id: str
    module_name: str
    widget_id: str
    title: str
    description: str | None = None
    icon: str | None = None
    default_w: int = 6
    default_h: int = 8
    # Whether the card can be resized on a dashboard. When false the card is a
    # fixed size (locked to `default_w`/`default_h`); when true it can be resized
    # no smaller than `min_w`/`min_h`.
    resizable: bool = True
    # Smallest size the card may be resized to on a dashboard (RGL cells). The
    # canvas applies these as the grid item's `minW`/`minH`. Ignored when the
    # card is not resizable.
    min_w: int = 2
    min_h: int = 3
    # Per-card settings schema (same dialect as module `config_schema`). The
    # palette renders a settings form from this for each placed card in edit
    # mode. ``None`` ⇒ the card has no options beyond its title.
    config_schema: dict[str, Any] | None = None
    # Log data model(s) this card applies to. The Dashboards palette only shows
    # a card whose models include the board's model (case-centric vs OCEL).
    log_models: list[LogModel] = Field(default_factory=lambda: ["case_centric"])


@router.get("/cards", response_model=list[DashboardCard])
async def list_cards(session: SessionDep, user: CurrentUserDep) -> list[DashboardCard]:
    """Catalog of every card exposed by the modules this user owns.

    Powers the Dashboards palette. Ordering is stable (module, then declared
    widget order) so the palette doesn't reshuffle between loads.
    """
    try:
        loader = get_module_loader()
    except HTTPException:
        return []
    manifests = loader.manifests()
    if not manifests:
        return []

    owned = await user_module_ids(session, user.id)
    cards: list[DashboardCard] = []
    for m in manifests:
        if m.id not in owned:
            continue
        for w in m.frontend.widgets:
            cards.append(
                DashboardCard(
                    module_id=m.id,
                    module_name=m.name,
                    widget_id=w.id,
                    title=w.title or w.id.replace("-", " ").replace("_", " ").title(),
                    description=w.description,
                    icon=w.icon,
                    default_w=w.default_w,
                    default_h=w.default_h,
                    resizable=w.resizable,
                    min_w=w.min_w,
                    min_h=w.min_h,
                    config_schema=w.config_schema,
                    log_models=w.log_models,
                )
            )
    return cards


@router.get("/{module_id}/manifest")
async def get_manifest(module_id: str, session: SessionDep, user: CurrentUserDep) -> dict[str, Any]:
    await _assert_owns_module(session, user.id, module_id)
    try:
        loader = get_module_loader()
    except HTTPException as exc:
        raise exc
    loaded = loader.loaded.get(module_id)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail=f"Module {module_id!r} is not loaded.",
        )
    return loaded.manifest.model_dump(by_alias=True)


@router.get("/{module_id}/config-schema")
async def get_config_schema(
    module_id: str, session: SessionDep, user: CurrentUserDep
) -> dict[str, Any]:
    await _assert_owns_module(session, user.id, module_id)
    try:
        loader = get_module_loader()
    except HTTPException as exc:
        raise exc
    loaded = loader.loaded.get(module_id)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail=f"Module {module_id!r} is not loaded.",
        )
    return loaded.manifest.config_schema or {}


@router.get("/{module_id}/config", response_model=ModuleConfigPayload)
async def get_config(
    module_id: str, session: SessionDep, user: CurrentUserDep
) -> ModuleConfigPayload:
    await _assert_owns_module(session, user.id, module_id)
    # Admin-controlled? Return the shared config (module config is not secret)
    # and flag it read-only - mirrors the AI-config control path.
    admin_cfg, controlled = await resolve(session, SCOPE_MODULE, module_id, user.id)
    if controlled:
        loaded = get_module_loader().loaded.get(module_id)
        default_enabled = loaded.manifest.default_enabled if loaded else True
        cfg = admin_cfg if isinstance(admin_cfg, dict) else {}
        return ModuleConfigPayload(config=cfg, enabled=default_enabled, controlled_by_admin=True)
    row = await session.get(ModuleConfig, (user.id, module_id))
    if row is None:
        # No saved config → fall back to the manifest's default_enabled, the
        # same fallback list_modules uses. Hardcoding True here made modules
        # shipping default_enabled=false (e.g. cv4cdd) read "Disabled" in the
        # process grid but "Enabled" on the detail toggle.
        loaded = get_module_loader().loaded.get(module_id)
        default_enabled = loaded.manifest.default_enabled if loaded else True
        return ModuleConfigPayload(config={}, enabled=default_enabled)
    return ModuleConfigPayload(config=row.config_json, enabled=row.enabled)


@router.put("/{module_id}/config", response_model=ModuleConfigPayload)
async def put_config(
    module_id: str,
    payload: ModuleConfigPayload,
    session: SessionDep,
    user: CurrentUserDep,
) -> ModuleConfigPayload:
    await _assert_owns_module(session, user.id, module_id)
    _, controlled = await resolve(session, SCOPE_MODULE, module_id, user.id)
    if controlled:
        raise HTTPException(
            status_code=403,
            detail="This module's configuration is controlled by your administrator.",
        )
    row = await session.get(ModuleConfig, (user.id, module_id))
    if row is None:
        row = ModuleConfig(
            user_id=user.id,
            module_id=module_id,
            config_json=payload.config,
            enabled=payload.enabled,
        )
        session.add(row)
    else:
        row.config_json = payload.config
        row.enabled = payload.enabled
    await session.commit()
    return payload


class GitInstallPayload(BaseModel):
    url: str = Field(..., min_length=1)
    ref: str | None = None


class RegistryInstallPayload(BaseModel):
    source: str = Field(..., pattern="^(pypi|npm)$")
    id: str = Field(..., min_length=1)
    version: str | None = None


class InstallJobResponse(BaseModel):
    job_id: str


_ALLOWED_UPLOAD_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def _has_allowed_upload_suffix(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(s) for s in _ALLOWED_UPLOAD_SUFFIXES)


@router.post("/install", response_model=InstallJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def install_from_upload(
    user: CurrentUserDep, file: UploadFile = File(...)
) -> InstallJobResponse:
    """Accept a zip / tar.gz, persist it to a staging dir, and submit a job
    that unpacks and registers the module. Returns the job id so the dock can
    stream progress (`GET /api/v1/events` SSE filtered by `job.*`).
    """
    filename = file.filename or "upload"
    if not _has_allowed_upload_suffix(filename):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported archive format. Accepted: {', '.join(_ALLOWED_UPLOAD_SUFFIXES)}.",
        )
    settings = get_settings()
    staging_dir = settings.data_dir / "module-uploads"
    staging_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix="upload-", suffix=Path(filename).suffix, dir=staging_dir)
    archive_path = Path(raw_path)
    # Stream the upload to disk so we don't hold huge payloads in memory.
    try:
        with open(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    runtime = get_job_runtime()
    job_id = await runtime.submit(
        type_=JOB_TYPE_UPLOAD,
        user_id=user.id,
        title=f"Install module - {filename}",
        subtitle="Unpacking and registering",
        payload={"archive_path": str(archive_path), "original_name": filename},
    )
    return InstallJobResponse(job_id=job_id)


@router.post(
    "/install/git", response_model=InstallJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def install_from_git(payload: GitInstallPayload, user: CurrentUserDep) -> InstallJobResponse:
    runtime = get_job_runtime()
    title = f"Install module - {payload.url.rsplit('/', 1)[-1]}"
    if payload.ref:
        title += f" ({payload.ref})"
    job_id = await runtime.submit(
        type_=JOB_TYPE_GIT,
        user_id=user.id,
        title=title,
        subtitle="Cloning and registering",
        payload={"url": payload.url, "ref": payload.ref},
    )
    return InstallJobResponse(job_id=job_id)


@router.post(
    "/install/registry",
    response_model=InstallJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_from_registry(
    payload: RegistryInstallPayload, user: CurrentUserDep
) -> InstallJobResponse:
    runtime = get_job_runtime()
    title = f"Install module - {payload.source}:{payload.id}"
    if payload.version:
        title += f"@{payload.version}"
    job_id = await runtime.submit(
        type_=JOB_TYPE_REGISTRY,
        user_id=user.id,
        title=title,
        subtitle="Fetching from registry",
        payload={"source": payload.source, "id": payload.id, "version": payload.version},
    )
    return InstallJobResponse(job_id=job_id)


class ModuleLayoutPayload(BaseModel):
    layout: dict[str, Any] = Field(default_factory=dict)


@router.get("/{module_id}/layout", response_model=ModuleLayoutPayload)
async def get_module_layout(
    module_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    log_id: Annotated[str, Query(..., min_length=1)],
) -> ModuleLayoutPayload:
    """Return the saved layout JSON for this `(user, log, module)` triple, or
    an empty object if none saved (§7.7). Frontend uses this to restore
    react-grid-layout positions across reloads.
    """
    await _assert_owns_module(session, user.id, module_id)
    await get_owned_event_log(session, log_id, user.id)
    row = await session.get(ModuleLayout, (user.id, log_id, module_id))
    return ModuleLayoutPayload(layout=row.layout_json if row else {})


@router.put("/{module_id}/layout", response_model=ModuleLayoutPayload)
async def put_module_layout(
    module_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    payload: ModuleLayoutPayload,
    log_id: Annotated[str, Query(..., min_length=1)],
) -> ModuleLayoutPayload:
    await _assert_owns_module(session, user.id, module_id)
    await get_owned_event_log(session, log_id, user.id)
    row = await session.get(ModuleLayout, (user.id, log_id, module_id))
    if row is None:
        row = ModuleLayout(
            user_id=user.id, log_id=log_id, module_id=module_id, layout_json=payload.layout
        )
        session.add(row)
    else:
        row.layout_json = payload.layout
    await session.commit()
    return payload


@router.get("/{module_id}/assets/{asset_path:path}")
async def get_module_asset(module_id: str, asset_path: str, user: CurrentUserDep) -> FileResponse:
    """Serve a file from the loaded module's `.dist/` (§5.4).

    The frontend dynamic loader fetches `panel.js` / `widget-*.js` from this
    route, runs them through a CJS shim that resolves `require(...)` against
    `window.__FF_RUNTIME__`. Layout matches what
    `apps/web/scripts/bundle-modules.mjs` writes at build time / dev watch.

    Resolved from the *loaded* module's folder rather than a fixed root so it
    serves defaults (repo `modules/`) and uploads (`uploaded_modules/`) alike.
    """
    loader = get_module_loader()
    loaded = loader.loaded.get(module_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Module not found.")
    dist_root = (loaded.discovered.folder / ".dist").resolve()
    # Reject path traversal - resolve() collapses `..` so the prefix check is
    # what actually enforces containment.
    candidate = (dist_root / asset_path).resolve()
    try:
        candidate.relative_to(dist_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid asset path.") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found.")
    # Force application/javascript so the browser executes the file as JS
    # even if the on-disk extension is unusual.
    media_type = "application/javascript" if candidate.suffix == ".js" else None
    return FileResponse(candidate, media_type=media_type)


class RestoreDefaultsResponse(BaseModel):
    restored: list[str]


@router.post("/restore-defaults", response_model=RestoreDefaultsResponse)
async def restore_defaults(session: SessionDep, user: CurrentUserDep) -> RestoreDefaultsResponse:
    """Re-add any default modules the user has removed (idempotent).

    Only ever *adds* the shared defaults - never touches custom uploads.
    Publishes ``module.installed`` per re-added id so other tabs refresh their
    listing.
    """
    loader = get_module_loader()
    # `default_module_ids` is computed from discovery (before install/import), so
    # a default that failed to install or import is in that set but absent from
    # `loaded`. Restrict to actually-loaded modules - otherwise we'd write an
    # install row and report "restored" for a module that never appears in the
    # listing (which only shows loaded manifests).
    default_ids = {mid for mid in loader.default_module_ids if mid in loader.loaded}
    owned = await user_module_ids(session, user.id)
    missing = sorted(default_ids - owned)
    if missing:
        await seed_default_modules(session, user.id, missing)
        await session.commit()
        for module_id in missing:
            await loader.bus.publish(
                "module.installed",
                {"id": module_id, "source": "default", "user_id": user.id},
            )
    return RestoreDefaultsResponse(restored=missing)


@router.delete("/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(module_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    # Per-user uninstall: drop this user's ownership record. The shared
    # on-disk artifact and in-process load are only torn down once the last
    # owner removes it - other users keep using it untouched.
    await _assert_owns_module(session, user.id, module_id)
    loader = get_module_loader()
    await remove_install(session, user.id, module_id)
    await session.commit()

    # Never tear down a default's shared repo code - only the user's install row
    # is removed above. Uploads live under uploaded_modules_dir and are removed
    # only once their last owner uninstalls. Entry-point/registry modules live
    # in neither root, so the existence check below leaves them alone.
    if module_id not in loader.default_module_ids and await owner_count(session, module_id) == 0:
        target = get_settings().uploaded_modules_dir.resolve() / module_id
        await loader.unload_one(module_id)
        if target.exists():
            remove_module_artifacts(target)
            shutil.rmtree(target, ignore_errors=True)

    # Scope the event to this user so the WS only notifies their sessions -
    # other owners' module lists are unaffected.
    await loader.bus.publish("module.uninstalled", {"id": module_id, "user_id": user.id})
