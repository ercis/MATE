"""/api/v1/modules - list manifests, per-log availability, get/put config.

Module-defined routes are mounted by the loader (phase 5) directly onto the
app under ``/api/v1/modules/{id}/...`` - they do **not** go through this
router; this router covers the platform's own module-meta surface.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
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
from mate.api.modules.cards import (
    MODEL_LOCK_SENTINEL,
    card_key,
    card_owned_keys,
    derive_cards,
    resolve_card_overlays,
)
from mate.api.modules.defaults import (
    DEFAULTS_SEEDED_KEY,
    get_admin_default_ids,
    get_excluded_default_ids,
)
from mate.api.modules.install_jobs import JOB_TYPE_UPLOAD
from mate.api.modules.installs import (
    seed_default_modules,
    user_module_ids,
    user_owns_module,
)
from mate.api.modules.uninstall import uninstall_for_user
from mate.api.policy import SCOPE_CARD, resolve
from mate.api.schemas.event_logs import LogModel
from mate.sdk.manifest import Artifact, Source

# UserSetting key holding the per-user record of which default module ids have
# already been offered to a user (a JSON list). Seeding grants only the defaults
# that are new since the last visit, so a freshly bundled module reaches
# existing users automatically while a default the user intentionally removed
# stays gone (its id is already in the recorded set). Legacy rows hold a bare
# `true` (the old one-shot "seeded at least once" flag). Shared with the admin
# default-declaration path via `modules.defaults`.
_DEFAULTS_SEEDED_KEY = DEFAULTS_SEEDED_KEY

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
    about: str | None = None
    # Cited works (max 20), each `{title, fullCitation, url?}`. The manifest has
    # no author fields - the citation string carries the author names.
    source: list[Source] = Field(default_factory=list)
    # Optional named links (max 20) - repo, dataset, demo, released model.
    artifacts: list[Artifact] = Field(default_factory=list)
    license: str | None = None
    provides: list[str]
    consumes: list[str]
    has_frontend: bool
    # Whether the module page renders the platform's log-scoped filter bar above
    # the panel (manifest `frontend.log_filter`). Folds in `has_frontend`: with
    # no panel there is no surface to filter.
    supports_log_filter: bool = True
    enabled: bool = True
    is_confidential_safe: bool = False
    availability: Availability | None = None


class ModuleConfigPayload(BaseModel):
    config: dict[str, Any] = {}
    enabled: bool = True
    # Set by GET/PUT: True only when *every* settings card the module exposes is
    # admin-locked (back-compat "whole module is controlled" flag). Ignored on
    # PUT input.
    controlled_by_admin: bool = False
    # Set by GET/PUT: per-card lock state, ``{card_id: locked}`` for each card
    # the module exposes (config / ai / model). The detail page disables each
    # card independently from this. Ignored on PUT input.
    controlled_cards: dict[str, bool] = {}


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
    # session, and it runs on every visit to the modules surface. Effective
    # defaults = bundled ids + admin-declared ids (the latter filtered to
    # actually-loaded modules so we never seed an id that can't be listed).
    admin_ids = await get_admin_default_ids(session)
    withheld = await get_excluded_default_ids(session)
    effective_defaults = (loader.default_module_ids | (admin_ids & set(loader.loaded))) - withheld
    await _reconcile_default_modules(session, user.id, effective_defaults)

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
            about=m.about,
            source=list(m.source),
            artifacts=list(m.artifacts),
            license=m.license,
            provides=list(m.provides),
            consumes=list(m.consumes),
            has_frontend=bool(m.frontend.panel),
            supports_log_filter=bool(m.frontend.panel) and m.frontend.log_filter,
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
    # Smallest size the card may be resized to, in units of the fixed 12-column
    # grid. Ignored when the card is not resizable.
    min_w: int = 2
    min_h: int = 3
    # Absolute pixel floors. These are what make a minimum real: a grid unit is
    # only a size once the board's width is known, so the canvas resolves these
    # against the measured width and takes whichever floor is larger. 0 = the
    # widget declares none.
    min_px_w: int = 0
    min_px_h: int = 0
    # Per-card settings schema (same dialect as module `config_schema`). The
    # palette renders a settings form from this for each placed card in edit
    # mode. ``None`` ⇒ the card has no options beyond its title.
    config_schema: dict[str, Any] | None = None
    # Structured help behind the card's ⓘ: {what, read, computed, docs_url}.
    # Passed through verbatim from the manifest.
    help: dict[str, Any] | None = None
    # The module views this card can render, and the config keys each exposes:
    # [{id, title, description, exposes: [...]}]. Empty = one implicit view.
    views: list[dict[str, Any]] = Field(default_factory=list)
    # The figures a multi-KPI card shows, so a placement can pick a subset:
    # [{id, title, info, default}]. Empty = the card is not KPI-structured.
    kpis: list[dict[str, Any]] = Field(default_factory=list)
    # Drill target for "open in module" and in-card clicks:
    # {module_id, params, label, enabled}. ``None`` ⇒ the platform still offers
    # the declaring module with no params.
    drill: dict[str, Any] | None = None
    # Whether this widget ships its own settings component. The URL is
    # conventional (`assets/widget-<id>-settings.js`), so the client only needs
    # to know whether to fetch it.
    has_settings_entry: bool = False
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
                    min_px_w=w.min_px_w,
                    min_px_h=w.min_px_h,
                    config_schema=w.config_schema,
                    help=w.help.model_dump(exclude_none=True) if w.help else None,
                    views=[v.model_dump() for v in w.views],
                    kpis=[k.model_dump(exclude_none=True) for k in w.kpis],
                    drill=w.drill.model_dump(exclude_none=True) if w.drill else None,
                    has_settings_entry=w.settings_entry is not None,
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
    loaded = get_module_loader().loaded.get(module_id)
    default_enabled = loaded.manifest.default_enabled if loaded else True
    row = await session.get(ModuleConfig, (user.id, module_id))
    # Always return the user's real enabled state (falling back to the
    # manifest's default_enabled only when there's no saved row) - a locked card
    # no longer forces default_enabled the way the old whole-module lock did.
    base_cfg = dict(row.config_json) if (row is not None and row.config_json) else {}
    enabled = row.enabled if row is not None else default_enabled
    if loaded is None:
        return ModuleConfigPayload(config=base_cfg, enabled=enabled)
    # Per-user config with each admin-locked card's shared value overlaid, plus
    # the per-card lock map the detail page uses to disable cards independently.
    cfg, controlled = await resolve_card_overlays(session, loaded.manifest, base_cfg, user.id)
    return ModuleConfigPayload(
        config=cfg,
        enabled=enabled,
        controlled_by_admin=bool(controlled) and all(controlled.values()),
        controlled_cards=controlled,
    )


@router.put("/{module_id}/config", response_model=ModuleConfigPayload)
async def put_config(
    module_id: str,
    payload: ModuleConfigPayload,
    session: SessionDep,
    user: CurrentUserDep,
) -> ModuleConfigPayload:
    await _assert_owns_module(session, user.id, module_id)
    loaded = get_module_loader().loaded.get(module_id)
    row = await session.get(ModuleConfig, (user.id, module_id))
    existing = dict(row.config_json) if (row is not None and row.config_json) else {}

    # No blanket 403: a user may still edit unlocked cards. Each admin-locked
    # card is read-only, so keep the user's stored slice for it and ignore the
    # incoming change (the shared admin value wins at runtime via _make_context)
    # - locking a card never destroys the user's saved value for it.
    result = dict(payload.config or {})
    controlled: dict[str, bool] = {}
    if loaded is not None:
        cards = derive_cards(loaded.manifest)
        for card in cards:
            _, locked = await resolve(
                session, SCOPE_CARD, card_key(module_id, card.card_id), user.id
            )
            controlled[card.card_id] = bool(locked)
            if not locked:
                continue
            for k in card_owned_keys(card, cards, {**existing, **result}):
                if k in existing:
                    result[k] = existing[k]
                else:
                    result.pop(k, None)
    # The loader-runtime sentinel is never persisted, even if a client echoes it.
    result.pop(MODEL_LOCK_SENTINEL, None)

    if row is None:
        row = ModuleConfig(
            user_id=user.id,
            module_id=module_id,
            config_json=result,
            enabled=payload.enabled,
        )
        session.add(row)
    else:
        row.config_json = result
        row.enabled = payload.enabled
    await session.commit()

    if loaded is None:
        return ModuleConfigPayload(config=result, enabled=payload.enabled)
    cfg, controlled = await resolve_card_overlays(session, loaded.manifest, result, user.id)
    return ModuleConfigPayload(
        config=cfg,
        enabled=payload.enabled,
        controlled_by_admin=bool(controlled) and all(controlled.values()),
        controlled_cards=controlled,
    )


class InstallJobResponse(BaseModel):
    job_id: str


_ALLOWED_UPLOAD_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def _has_allowed_upload_suffix(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(s) for s in _ALLOWED_UPLOAD_SUFFIXES)


@router.post("/install", response_model=InstallJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def install_from_upload(
    user: CurrentUserDep, file: Annotated[UploadFile, File()]
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


@router.get("/readme")
async def get_modules_readme(user: CurrentUserDep) -> FileResponse:
    """Serve the live ``modules/README.md`` (the module-authoring contract).

    Read straight from disk on every request - the download always mirrors the
    checked-in guide, no bundled snapshot. ``filename`` makes it an attachment.
    """
    loader = get_module_loader()
    readme = (loader.modules_dir / "README.md").resolve()
    if not readme.is_file():
        raise HTTPException(status_code=404, detail="README not found.")
    return FileResponse(
        readme,
        media_type="text/markdown",
        filename="README.md",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{module_id}/assets/{asset_path:path}")
async def get_module_asset(
    module_id: str, asset_path: str, request: Request, user: CurrentUserDep
) -> Response:
    """Serve a file from the loaded module's `.dist/` (§5.4).

    The frontend dynamic loader fetches `panel.js` / `widget-*.js` from this
    route, runs them through a CJS shim that resolves `require(...)` against
    `window.__FF_RUNTIME__`. Layout matches what
    `apps/web/scripts/bundle-modules.mjs` writes at build time / dev watch.

    Resolved from the *loaded* module's folder rather than a fixed root so it
    serves defaults (repo `modules/`) and uploads (`uploaded_modules/`) alike.

    Bundles are multi-MB, so answer conditional requests: `private, no-cache`
    lets the browser store the body but forces an ETag revalidation per use -
    a dev watch-rebuild or prod upgrade (new mtime/size) is picked up on the
    next load, while an unchanged bundle costs a ~200B 304 instead of the file.
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
    # Same mtime-size ETag formula as Starlette's FileResponse (which sets the
    # header but never answers 304 itself).
    stat = candidate.stat()
    etag_base = f"{stat.st_mtime}-{stat.st_size}".encode()
    etag = f'"{hashlib.md5(etag_base, usedforsecurity=False).hexdigest()}"'
    cache_headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        tags = {t.strip().removeprefix("W/") for t in if_none_match.split(",")}
        if "*" in tags or etag in tags:
            return Response(status_code=304, headers=cache_headers)
    # Force application/javascript so the browser executes the file as JS
    # even if the on-disk extension is unusual.
    media_type = "application/javascript" if candidate.suffix == ".js" else None
    return FileResponse(candidate, media_type=media_type, headers=cache_headers)


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
    # listing (which only shows loaded manifests). Admin-declared defaults are
    # restored too.
    admin_ids = await get_admin_default_ids(session)
    withheld = await get_excluded_default_ids(session)
    default_ids = {
        mid for mid in (loader.default_module_ids | admin_ids) if mid in loader.loaded
    } - withheld
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
    # owner removes it - other users keep using it untouched. Bundled and
    # admin-declared defaults are protected from teardown (their shared code
    # must survive for everyone else).
    await _assert_owns_module(session, user.id, module_id)
    loader = get_module_loader()
    protected = loader.default_module_ids | await get_admin_default_ids(session)
    await uninstall_for_user(session, loader, user.id, module_id, protected_ids=protected)
