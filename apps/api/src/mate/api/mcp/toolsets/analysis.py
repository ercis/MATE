"""Analysis toolset: module outputs, results, datasets + module lifecycle.

Reads ride the guidance-payload path (curated, aggregate-only module outputs)
plus the module result cache and the dataset layer. Every module read runs
behind the structural data wall (``restrict_event_log=True``): the module sees
its own cached outputs but every raw event-log accessor raises, so nothing can
leak XES/parquet rows into an MCP response. Per-user module ownership
(``module_installs``) gates every module read; writes reuse the module route
internals (admin-lock, ref-counted uninstall, default restore).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import ModuleConfig, ModuleInstall
from mate.api.mcp.core import (
    MCPContext,
    authz,
    cap,
    confirm_preview,
    ensure_owned_log,
    guarded,
)
from mate.api.mcp.errors import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    from_http_exception,
    tool_error,
)
from mate.api.mcp.registry import mcp_resource, mcp_tool
from mate.api.mcp.scopes import SCOPE_MODULES_MANAGE, SCOPE_MODULES_READ, SCOPE_MODULES_WRITE

# Per-module budget inside get_process_overview - one slow module must not
# starve the composite (the whole tool still runs under the global timeout).
_OVERVIEW_MODULE_TIMEOUT_S = 8.0

# ResultCache namespace for the process-level (cross-module) AI guidance.
_PLATFORM_GUIDANCE_ID = "__platform__"


async def _installed_module_ids(user_id: str) -> set[str]:
    sm = get_sessionmaker()
    async with sm() as session:
        rows = await session.execute(
            select(ModuleInstall.module_id).where(ModuleInstall.user_id == user_id)
        )
        return set(rows.scalars().all())


async def _ensure_installed(session: AsyncSession, user_id: str, module_id: str) -> None:
    """Per-user module gating: a loaded module the caller hasn't installed is
    not theirs to read (module_installs ref-counts ownership)."""
    if await session.get(ModuleInstall, (user_id, module_id)) is None:
        raise tool_error(CODE_NOT_FOUND, f"Module '{module_id}' is not installed for your account.")


def _no_results_error(module_id: str) -> Exception:
    return tool_error(
        CODE_CONFLICT,
        f"Module '{module_id}' has no precomputed results yet - run its analysis first.",
    )


async def _module_output(user_id: str, log_id: str, module_id: str) -> dict[str, Any]:
    from mate.api.routes.ai_guidance import build_payload

    sm = get_sessionmaker()
    async with sm() as session:
        await ensure_owned_log(session, log_id, user_id)
        await _ensure_installed(session, user_id, module_id)
    try:
        # restrict_event_log=True is the structural data wall: a module whose
        # guidance_payload reaches for raw event rows hits PermissionError
        # instead of leaking them into the MCP response.
        payload, _system, _prefix = await build_payload(
            module_id, log_id, user_id, restrict_event_log=True
        )
    except HTTPException as exc:
        raise from_http_exception(exc) from exc
    except PermissionError as exc:
        raise _no_results_error(module_id) from exc
    return cap({"log_id": log_id, "module_id": module_id, "output": payload})


@mcp_tool(toolset="analysis", idempotent=True)
async def list_modules(ctx: MCPContext, log_id: str) -> list[dict[str, Any]]:
    """List the analysis modules installed for your account, with their per-log
    availability for a process.

    Each entry carries ``module_id``, ``name``, ``description``, ``enabled``
    (your per-module toggle), ``has_guidance`` (whether get_module_output can
    read it) and ``availability`` (status ``available|degraded|unavailable``
    plus reasons) for the given ``log_id``.
    """
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> list[dict[str, Any]]:
        from mate.api.modules import get_module_loader

        loader = get_module_loader()
        sm = get_sessionmaker()
        async with sm() as session:
            log_row = await ensure_owned_log(session, log_id, p.user.id)
            detected_schema = log_row.detected_schema
            events_count = log_row.events_count
            cases_count = log_row.cases_count
            log_model = log_row.log_model
            installed = set(
                (
                    await session.execute(
                        select(ModuleInstall.module_id).where(ModuleInstall.user_id == p.user.id)
                    )
                )
                .scalars()
                .all()
            )
            rows = await session.execute(
                select(ModuleConfig.module_id, ModuleConfig.enabled).where(
                    ModuleConfig.user_id == p.user.id
                )
            )
            enabled_map: dict[str, bool] = {mid: bool(en) for mid, en in rows.all()}
        # Same availability computation the GET /modules?log_id= route uses.
        avail_map = loader.availability_for(
            detected_schema=detected_schema,
            events_count=events_count,
            cases_count=cases_count,
            installed_module_ids=installed,
            log_model=log_model,
        )
        out: list[dict[str, Any]] = []
        for mid in sorted(installed):
            loaded = loader.loaded.get(mid)
            if loaded is None:
                continue
            man = loaded.manifest
            avail = avail_map.get(mid)
            out.append(
                {
                    "module_id": mid,
                    "name": man.name or mid,
                    "description": man.description or "",
                    "enabled": enabled_map.get(mid, man.default_enabled),
                    "has_guidance": callable(getattr(loaded.instance, "guidance_payload", None)),
                    "availability": avail.to_dict() if avail is not None else None,
                }
            )
        return out

    return await guarded(p, "list_modules", {"log_id": log_id}, _impl())


@mcp_tool(toolset="analysis", idempotent=True)
async def get_module_output(ctx: MCPContext, log_id: str, module_id: str) -> dict[str, Any]:
    """Get one module's curated analytical output for a process (no raw event rows)."""
    p = await authz(ctx, SCOPE_MODULES_READ)
    return await guarded(
        p,
        "get_module_output",
        {"log_id": log_id, "module_id": module_id},
        _module_output(p.user.id, log_id, module_id),
    )


def _skip_reason(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return f"timed out after {_OVERVIEW_MODULE_TIMEOUT_S:g}s"
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, PermissionError):
        return "no precomputed results yet - run the module's analysis first"
    return f"failed: {type(exc).__name__}"


@mcp_tool(toolset="analysis", idempotent=True)
async def get_process_overview(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Curated outputs from every guidance-capable module for a process.

    Modules are queried concurrently; ones without data (or too slow) are
    listed under ``skipped`` with a reason instead of failing the call.
    """
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> dict[str, Any]:
        from mate.api.routes.ai_guidance import build_payload, enabled_modules_with_guidance

        sm = get_sessionmaker()
        async with sm() as session:
            await ensure_owned_log(session, log_id, p.user.id)
            module_ids = await enabled_modules_with_guidance(session, p.user.id)
        installed = await _installed_module_ids(p.user.id)
        targets = [mid for mid in module_ids if mid in installed]

        async def _one(mid: str) -> Any:
            payload, _system, _prefix = await asyncio.wait_for(
                build_payload(mid, log_id, p.user.id, restrict_event_log=True),
                timeout=_OVERVIEW_MODULE_TIMEOUT_S,
            )
            return payload

        results = await asyncio.gather(*(_one(mid) for mid in targets), return_exceptions=True)
        composite: dict[str, Any] = {}
        skipped: dict[str, str] = {}
        for mid, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                skipped[mid] = _skip_reason(result)
            else:
                composite[mid] = result
        return cap({"log_id": log_id, "modules": composite, "skipped": skipped})

    return await guarded(p, "get_process_overview", {"log_id": log_id}, _impl())


# ── result cache / datasets / cached guidance (direct curated reads) ─────────


async def _module_results(user_id: str, log_id: str, module_id: str) -> dict[str, Any]:
    from mate.api.modules.cache import ResultCache

    sm = get_sessionmaker()
    async with sm() as session:
        await ensure_owned_log(session, log_id, user_id)
        await _ensure_installed(session, user_id, module_id)
    cache = ResultCache(log_id, module_id, user_id)

    def _read() -> tuple[dict[str, Any], list[str]]:
        results: dict[str, Any] = {}
        artifacts: list[str] = []
        try:
            entries = sorted(cache.dir.iterdir())
        except OSError:
            entries = []
        for path in entries:
            # Canonical variant only: `_v_<digest>` filter forks are dirs and
            # `is_file()` skips them. `__`-prefixed keys are internal caches
            # (e.g. the stored AI guidance) - never surfaced here.
            if not path.is_file() or path.stem.startswith("__"):
                continue
            if path.suffix == ".json":
                try:
                    results[path.stem] = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
            elif path.suffix in (".parquet", ".bin"):
                artifacts.append(path.stem)
        return results, artifacts

    results, artifacts = await asyncio.to_thread(_read)
    return cap(
        {
            "log_id": log_id,
            "module_id": module_id,
            "keys": sorted(results),
            "results": results,
            "artifact_keys": artifacts,
        }
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def get_module_results(ctx: MCPContext, log_id: str, module_id: str) -> dict[str, Any]:
    """Read a module's cached JSON results for a process (canonical view only).

    Returns ``keys`` + parsed ``results`` for every public JSON entry in the
    module's result cache; binary/parquet artifacts are listed by name under
    ``artifact_keys`` without contents. Dashboard filter variants and internal
    ``__``-prefixed entries are never included.
    """
    p = await authz(ctx, SCOPE_MODULES_READ)
    return await guarded(
        p,
        "get_module_results",
        {"log_id": log_id, "module_id": module_id},
        _module_results(p.user.id, log_id, module_id),
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def list_datasets(ctx: MCPContext) -> list[dict[str, Any]]:
    """List the named, typed data outputs (datasets) your installed modules expose.

    Each entry has ``module_id``, ``dataset_id``, ``shape``
    (table|graph|kpi|tree|blob), ``log_models`` and an optional
    ``params_schema``. Fetch one with get_dataset.
    """
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> list[dict[str, Any]]:
        from mate.api.routes.datasets import list_datasets as dataset_catalog

        sm = get_sessionmaker()
        async with sm() as session:
            try:
                entries = await dataset_catalog(session, p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return [e.model_dump() for e in entries]

    return await guarded(p, "list_datasets", {}, _impl())


@mcp_tool(toolset="analysis", idempotent=True)
async def get_dataset(
    ctx: MCPContext,
    module_id: str,
    dataset_id: str,
    log_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one module dataset to its canonical envelope for a process.

    Serves the committed (canonical) view of the log - no ephemeral filters -
    and runs behind the data wall, so it returns the module's computed data,
    never raw event rows. ``params`` are passed to the dataset's route (see the
    dataset's ``params_schema`` from list_datasets).
    """
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> dict[str, Any]:
        from mate.api.datasets.adapters import resolve_dataset
        from mate.api.modules import get_module_loader

        sm = get_sessionmaker()
        async with sm() as session:
            await ensure_owned_log(session, log_id, p.user.id)
            await _ensure_installed(session, p.user.id, module_id)
        try:
            loader = get_module_loader()
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        try:
            envelope = await resolve_dataset(
                loader,
                module_id,
                dataset_id,
                log_id,
                p.user.id,
                params=params,
                restrict_event_log=True,
            )
        except PermissionError as exc:
            raise tool_error(
                CODE_CONFLICT,
                f"Dataset '{module_id}/{dataset_id}' is not computed yet - "
                "run the module's analysis first.",
            ) from exc
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        except ValueError as exc:
            raise tool_error(CODE_NOT_FOUND, str(exc)) from exc
        return cap(
            {
                "log_id": log_id,
                "module_id": module_id,
                "dataset_id": dataset_id,
                "dataset": envelope.model_dump(by_alias=True),
            }
        )

    return await guarded(
        p,
        "get_dataset",
        {"log_id": log_id, "module_id": module_id, "dataset_id": dataset_id},
        _impl(),
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def get_cached_guidance(ctx: MCPContext, log_id: str, module_id: str) -> dict[str, Any]:
    """Read the stored AI guidance for a module (or "__platform__" for the
    process-level synthesis) on a process.

    Cache-only: returns ``{"cached": false}`` when nothing is stored and NEVER
    triggers a new LLM call. ``output_hash`` identifies the module output the
    guidance was generated from.
    """
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> dict[str, Any]:
        from mate.api.modules.cache import ResultCache
        from mate.api.routes.ai_guidance import GUIDANCE_CACHE_KEY

        sm = get_sessionmaker()
        async with sm() as session:
            await ensure_owned_log(session, log_id, p.user.id)
            if module_id != _PLATFORM_GUIDANCE_ID:
                await _ensure_installed(session, p.user.id, module_id)
        cached = await ResultCache(log_id, module_id, p.user.id).get(GUIDANCE_CACHE_KEY)
        if not isinstance(cached, dict):
            return {"log_id": log_id, "module_id": module_id, "cached": False, "guidance": None}
        return cap(
            {
                "log_id": log_id,
                "module_id": module_id,
                "cached": True,
                "guidance": cached.get("guidance"),
                "output_hash": cached.get("output_hash"),
                "generated_at": cached.get("generated_at"),
                "model": cached.get("model"),
                "provider": cached.get("provider"),
            }
        )

    return await guarded(
        p, "get_cached_guidance", {"log_id": log_id, "module_id": module_id}, _impl()
    )


# ── module lifecycle (writes - reuse the module route internals) ─────────────


@mcp_tool(toolset="analysis", write=True, idempotent=True)
async def set_module_config(
    ctx: MCPContext,
    module_id: str,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Update a module's per-user configuration and/or enabled flag.

    Omitted fields keep their current value. Fails with [forbidden] when an
    administrator controls this module's configuration platform-wide.
    """
    p = await authz(ctx, SCOPE_MODULES_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        from mate.api.routes.modules import ModuleConfigPayload, get_config, put_config

        sm = get_sessionmaker()
        async with sm() as session:
            try:
                # Same internals as GET/PUT /modules/{id}/config: per-user
                # ownership gate (404) + the admin config lock (403).
                current = await get_config(module_id, session, p.user)
                merged = ModuleConfigPayload(
                    config=current.config if config is None else config,
                    enabled=current.enabled if enabled is None else enabled,
                )
                saved = await put_config(module_id, merged, session, p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"module_id": module_id, "config": saved.config, "enabled": saved.enabled}

    return await guarded(p, "set_module_config", {"module_id": module_id}, _impl(), mutation=True)


@mcp_tool(toolset="analysis", write=True, destructive=True)
async def uninstall_module(
    ctx: MCPContext, module_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Uninstall a module from your account. DESTRUCTIVE - pass confirm=true.

    Without ``confirm`` this returns a dry-run preview (module name, whether
    you are the last owner - in which case shared uploaded artifacts are
    deleted) and changes nothing.
    """
    p = await authz(ctx, SCOPE_MODULES_MANAGE, write=True)

    async def _impl() -> dict[str, Any]:
        from mate.api.modules import get_module_loader
        from mate.api.modules.installs import owner_count
        from mate.api.routes.modules import uninstall

        sm = get_sessionmaker()
        async with sm() as session:
            await _ensure_installed(session, p.user.id, module_id)
            owners = await owner_count(session, module_id)
        try:
            loader = get_module_loader()
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        loaded = loader.loaded.get(module_id)
        name = loaded.manifest.name if loaded is not None else module_id
        last_owner = owners <= 1
        # Mirrors DELETE /modules/{id}: shared artifacts are only torn down when
        # the last owner uninstalls a non-default (uploaded) module.
        removes_artifacts = last_owner and module_id not in loader.default_module_ids
        if not confirm:
            return confirm_preview(
                "uninstall_module",
                {
                    "module_id": module_id,
                    "name": name,
                    "last_owner": last_owner,
                    "removes_shared_artifacts": removes_artifacts,
                },
            )
        async with sm() as session:
            try:
                await uninstall(module_id, session, p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"uninstalled": module_id, "last_owner": last_owner}

    return await guarded(p, "uninstall_module", {"module_id": module_id}, _impl(), mutation=True)


@mcp_tool(toolset="analysis", write=True, idempotent=True)
async def restore_default_modules(ctx: MCPContext) -> dict[str, Any]:
    """Re-add any default modules you previously removed (idempotent).

    Only ever adds the platform defaults - never touches custom uploads.
    Returns the ids that were restored (empty when nothing was missing).
    """
    p = await authz(ctx, SCOPE_MODULES_MANAGE, write=True)

    async def _impl() -> dict[str, Any]:
        from mate.api.routes.modules import restore_defaults

        sm = get_sessionmaker()
        async with sm() as session:
            try:
                resp = await restore_defaults(session, p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"restored": resp.restored}

    return await guarded(p, "restore_default_modules", {}, _impl(), mutation=True)


# ── curated per-domain tools (thin, friendlier intent over get_module_output) ─


@mcp_tool(toolset="analysis", idempotent=True)
async def get_bottlenecks(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Performance KPIs + the top bottleneck activities for a process."""
    p = await authz(ctx, SCOPE_MODULES_READ)
    return await guarded(
        p, "get_bottlenecks", {"log_id": log_id}, _module_output(p.user.id, log_id, "performance")
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def get_conformance(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Conformance fitness/precision + per-activity deviations vs the reference model."""
    p = await authz(ctx, SCOPE_MODULES_READ)
    return await guarded(
        p, "get_conformance", {"log_id": log_id}, _module_output(p.user.id, log_id, "conformance")
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def get_process_model(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """The discovered process-model overview (activities, paths) for a process."""
    p = await authz(ctx, SCOPE_MODULES_READ)
    return await guarded(
        p, "get_process_model", {"log_id": log_id}, _module_output(p.user.id, log_id, "discovery")
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def get_drifts(ctx: MCPContext, log_id: str) -> dict[str, Any]:
    """Detected concept drifts (type, time window, confidence) for a process."""
    p = await authz(ctx, SCOPE_MODULES_READ)
    return await guarded(
        p, "get_drifts", {"log_id": log_id}, _module_output(p.user.id, log_id, "cv4cdd")
    )


@mcp_resource("mate://process/{log_id}/module/{module_id}", toolset="analysis")
async def module_output_resource(log_id: str, module_id: str) -> str:
    """One module's curated output as a JSON resource."""
    from mate.api.mcp.server import mcp

    ctx = mcp.get_context()
    p = await authz(ctx, SCOPE_MODULES_READ)  # type: ignore[arg-type]
    result = await guarded(
        p,
        "resource:module_output",
        {"log_id": log_id, "module_id": module_id},
        _module_output(p.user.id, log_id, module_id),
    )
    return json.dumps(result, default=str)
