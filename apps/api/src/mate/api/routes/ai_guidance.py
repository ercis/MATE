"""/api/v1/ai/guidance - AI-assisted interpretation for module + process data.

The four surfaces:

* ``POST /module/{module_id}`` - interprets a single module's cached output.
* ``POST /module/{module_id}/stream`` - same, but SSE-streams the long-form
  ``interpretation`` text while the structured tail arrives in a final event.
* ``POST /process/{log_id}`` - synthesises across all enabled modules.
* ``POST /import/column-mapping`` - pre-import CSV column-mapping suggestions.
* ``POST /import/quality/{log_id}`` - post-import data-quality summary.

Caching mirrors the per-module ``ResultCache`` pattern: every entry stores
its source ``output_hash``, and we only return the cached entry when that
hash matches the *current* payload's hash. No extra DB table needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from collections.abc import AsyncGenerator
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from mate.api.ai_config import load_ai_config
from mate.api.ai_guidance import (
    GuidanceError,
    generate_guidance,
    stream_interpretation,
    structured_completion,
)
from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.models import ModuleConfig
from mate.api.db.session import SessionDep
from mate.api.modules import get_module_loader
from mate.api.modules.cache import ResultCache

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/ai/guidance", tags=["ai"])

_GUIDANCE_KEY = "__ai_guidance"


# ── Pydantic models ────────────────────────────────────────────────────────


class GuidanceFlag(BaseModel):
    severity: Literal["info", "warning", "critical"]
    message: str


class GuidanceBody(BaseModel):
    interpretation: str
    recommended_actions: list[str]
    anomaly_flags: list[GuidanceFlag]


class GuidanceResponse(BaseModel):
    cached: bool
    output_hash: str
    generated_at: float
    model: str | None = None
    provider: str | None = None
    guidance: GuidanceBody


class ModuleGuidanceRequest(BaseModel):
    force: bool = False


class ImportColumnMappingRequest(BaseModel):
    headers: list[str]
    sample_rows: list[list[str]] = []


class ImportColumnMappingResponse(BaseModel):
    suggestions: dict[str, str]
    """Canonical field name → CSV header name. Only fields the model is
    confident about are included."""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


async def _build_payload(module_id: str, log_id: str, user_id: str) -> tuple[Any, str, str]:
    """Resolve a module's ``guidance_payload`` for a log.

    Returns ``(payload, system_prompt, user_prefix)``.
    """
    loader = get_module_loader()
    loaded = loader.loaded.get(module_id)
    if loaded is None:
        raise HTTPException(404, detail=f"Module {module_id!r} not loaded.")

    fn = getattr(loaded.instance, "guidance_payload", None)
    if not callable(fn):
        raise HTTPException(
            400,
            detail=f"Module {module_id!r} doesn't expose guidance_payload().",
        )

    system_prompt = str(
        getattr(loaded.instance, "guidance_system_prompt", "")
        or f"You are a process-mining analyst interpreting the {module_id} module's output."
    )
    user_prefix = str(
        getattr(loaded.instance, "guidance_user_prefix", "")
        or f"Analyse this {module_id} module output for the process at hand:"
    )

    ctx = await loader._make_context(module_id, log_id, user_id)
    try:
        payload = (
            await fn(ctx) if asyncio.iscoroutinefunction(fn) else await asyncio.to_thread(fn, ctx)
        )
    finally:
        shutil.rmtree(ctx.workdir, ignore_errors=True)

    if payload is None:
        raise HTTPException(
            409,
            detail=(f"Module {module_id!r} has no data to interpret yet - run the module first."),
        )
    return payload, system_prompt, user_prefix


async def _enabled_modules_with_guidance(session: SessionDep, user_id: str) -> list[str]:
    """Return ids of loaded modules that are enabled AND expose guidance_payload."""
    rows = await session.execute(
        select(ModuleConfig.module_id, ModuleConfig.enabled).where(ModuleConfig.user_id == user_id)
    )
    enabled_map: dict[str, bool] = {mid: en for mid, en in rows.all()}
    loader = get_module_loader()
    out: list[str] = []
    for mid, loaded in loader.loaded.items():
        if not enabled_map.get(mid, True):
            continue
        if callable(getattr(loaded.instance, "guidance_payload", None)):
            out.append(mid)
    return sorted(out)


# ── Module-level guidance ──────────────────────────────────────────────────


@router.post("/module/{module_id}", response_model=GuidanceResponse)
async def module_guidance(
    module_id: str,
    body: ModuleGuidanceRequest,
    session: SessionDep,
    user: CurrentUserDep,
    log_id: str = Query(..., min_length=1),
) -> GuidanceResponse:
    await get_owned_event_log(session, log_id, user.id)
    payload, system_prompt, user_prefix = await _build_payload(module_id, log_id, user.id)
    output_hash = _hash_payload(payload)
    cache = ResultCache(log_id, module_id, user.id)

    if not body.force:
        cached = await cache.get(_GUIDANCE_KEY)
        if isinstance(cached, dict) and cached.get("output_hash") == output_hash:
            return GuidanceResponse(
                cached=True,
                output_hash=output_hash,
                generated_at=float(cached.get("generated_at", 0.0)),
                model=cached.get("model"),
                provider=cached.get("provider"),
                guidance=GuidanceBody.model_validate(cached["guidance"]),
            )

    cfg = await load_ai_config(session, user.id)
    try:
        guidance = await generate_guidance(
            cfg,
            system_prompt=system_prompt,
            payload=payload,
            user_prefix=user_prefix,
        )
    except GuidanceError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    record = {
        "guidance": guidance,
        "output_hash": output_hash,
        "generated_at": time.time(),
        "model": cfg.selected_model,
        "provider": cfg.selected_provider,
    }
    await cache.set(_GUIDANCE_KEY, record)
    return GuidanceResponse(
        cached=False,
        output_hash=output_hash,
        generated_at=record["generated_at"],
        model=record["model"],
        provider=record["provider"],
        guidance=GuidanceBody.model_validate(guidance),
    )


@router.delete("/module/{module_id}")
async def delete_module_guidance(
    module_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    log_id: str = Query(..., min_length=1),
) -> dict[str, bool]:
    await get_owned_event_log(session, log_id, user.id)
    cache = ResultCache(log_id, module_id, user.id)
    await cache.delete(_GUIDANCE_KEY)
    return {"ok": True}


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/module/{module_id}/stream")
async def module_guidance_stream(
    module_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    log_id: str = Query(..., min_length=1),
) -> StreamingResponse:
    await get_owned_event_log(session, log_id, user.id)
    payload, system_prompt, user_prefix = await _build_payload(module_id, log_id, user.id)
    output_hash = _hash_payload(payload)
    cfg = await load_ai_config(session, user.id)
    cache = ResultCache(log_id, module_id, user.id)

    async def _gen() -> AsyncGenerator[str, None]:
        try:
            async for chunk in stream_interpretation(
                cfg,
                system_prompt=system_prompt,
                payload=payload,
                user_prefix=user_prefix,
            ):
                yield _sse({"delta": chunk})
            # Now fetch the structured tail.
            try:
                full = await generate_guidance(
                    cfg,
                    system_prompt=system_prompt,
                    payload=payload,
                    user_prefix=user_prefix,
                )
            except GuidanceError as exc:
                yield _sse({"error": str(exc)})
                return
            record = {
                "guidance": full,
                "output_hash": output_hash,
                "generated_at": time.time(),
                "model": cfg.selected_model,
                "provider": cfg.selected_provider,
            }
            await cache.set(_GUIDANCE_KEY, record)
            yield _sse(
                {
                    "final": {
                        "cached": False,
                        "output_hash": output_hash,
                        "generated_at": record["generated_at"],
                        "model": record["model"],
                        "provider": record["provider"],
                        "guidance": full,
                    }
                }
            )
        except GuidanceError as exc:
            yield _sse({"error": str(exc)})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Process-level guidance ────────────────────────────────────────────────


_PROCESS_SYSTEM = (
    "You are a process-mining analyst. Given outputs from several analysis "
    "modules for the same event log, synthesise a brief overview: what stands "
    "out, what to investigate first, and any data-quality concerns. Reference "
    "specific module outputs by name when relevant."
)


@router.post("/process/{log_id}", response_model=GuidanceResponse)
async def process_guidance(
    log_id: str,
    body: ModuleGuidanceRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> GuidanceResponse:
    await get_owned_event_log(session, log_id, user.id)
    module_ids = await _enabled_modules_with_guidance(session, user.id)
    if not module_ids:
        raise HTTPException(
            409,
            detail="No enabled modules expose guidance for this process yet.",
        )

    composite: dict[str, Any] = {}
    for mid in module_ids:
        try:
            payload, _sys, _prefix = await _build_payload(mid, log_id, user.id)
            composite[mid] = payload
        except HTTPException:
            # Skip modules without data yet - overview still works on the rest.
            continue

    if not composite:
        raise HTTPException(
            409,
            detail="None of the enabled modules have data to interpret yet.",
        )

    output_hash = _hash_payload(composite)
    cache = ResultCache(log_id, "__platform__", user.id)

    if not body.force:
        cached = await cache.get(_GUIDANCE_KEY)
        if isinstance(cached, dict) and cached.get("output_hash") == output_hash:
            return GuidanceResponse(
                cached=True,
                output_hash=output_hash,
                generated_at=float(cached.get("generated_at", 0.0)),
                model=cached.get("model"),
                provider=cached.get("provider"),
                guidance=GuidanceBody.model_validate(cached["guidance"]),
            )

    cfg = await load_ai_config(session, user.id)
    try:
        guidance = await generate_guidance(
            cfg,
            system_prompt=_PROCESS_SYSTEM,
            payload=composite,
            user_prefix="Synthesise across these per-module outputs:",
        )
    except GuidanceError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    record = {
        "guidance": guidance,
        "output_hash": output_hash,
        "generated_at": time.time(),
        "model": cfg.selected_model,
        "provider": cfg.selected_provider,
        "modules": list(composite.keys()),
    }
    await cache.set(_GUIDANCE_KEY, record)
    return GuidanceResponse(
        cached=False,
        output_hash=output_hash,
        generated_at=record["generated_at"],
        model=record["model"],
        provider=record["provider"],
        guidance=GuidanceBody.model_validate(guidance),
    )


# ── Import-time guidance ───────────────────────────────────────────────────


_COLUMN_MAPPING_SYSTEM = (
    "You map raw CSV column headers to a canonical process-mining schema. "
    "Canonical fields: case_id, activity, timestamp, end_timestamp, resource, "
    "cost. Return ONLY a JSON object with the canonical field names as keys "
    "and the matching header strings as values. Omit fields you can't match "
    "confidently. Do not invent values not present in the headers list."
)


_COLUMN_MAPPING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "case_id": {"type": "string"},
        "activity": {"type": "string"},
        "timestamp": {"type": "string"},
        "end_timestamp": {"type": "string"},
        "resource": {"type": "string"},
        "cost": {"type": "string"},
    },
}


@router.post("/import/column-mapping", response_model=ImportColumnMappingResponse)
async def import_column_mapping(
    body: ImportColumnMappingRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> ImportColumnMappingResponse:
    if not body.headers:
        return ImportColumnMappingResponse(suggestions={})
    cfg = await load_ai_config(session, user.id)
    if not cfg.selected_provider or not cfg.selected_model:
        raise HTTPException(400, detail="No AI model selected. Configure one in Settings → AI.")
    p = getattr(cfg, cfg.selected_provider)
    if not p.api_key:
        raise HTTPException(400, detail=f"No API key configured for {cfg.selected_provider!r}.")

    payload = {
        "headers": body.headers,
        "sample_rows": body.sample_rows[:8],
    }
    try:
        result = await structured_completion(
            cfg,
            system_prompt=_COLUMN_MAPPING_SYSTEM,
            payload=payload,
            schema=_COLUMN_MAPPING_SCHEMA,
            tool_name="emit_column_mapping",
            user_prefix="Map these CSV headers to canonical fields.",
        )
    except GuidanceError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    headers_set = set(body.headers)
    suggestions: dict[str, str] = {}
    for canonical, header in result.items():
        if (
            canonical in _COLUMN_MAPPING_SCHEMA["properties"]
            and isinstance(header, str)
            and header in headers_set
        ):
            suggestions[canonical] = header
    return ImportColumnMappingResponse(suggestions=suggestions)


_QUALITY_SYSTEM = (
    "You review an imported event log's metadata and flag data-quality "
    "issues that would degrade downstream process-mining results."
)


@router.post("/import/quality/{log_id}", response_model=GuidanceResponse)
async def import_quality(
    log_id: str,
    body: ModuleGuidanceRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> GuidanceResponse:
    log_row = await get_owned_event_log(session, log_id, user.id)
    payload = {
        "name": log_row.name,
        "source_format": log_row.source_format,
        "status": log_row.status,
        "events_count": log_row.events_count,
        "cases_count": log_row.cases_count,
        "variants_count": log_row.variants_count,
        "date_min": str(log_row.date_min) if log_row.date_min else None,
        "date_max": str(log_row.date_max) if log_row.date_max else None,
        "detected_schema": log_row.detected_schema,
    }
    output_hash = _hash_payload(payload)
    cache = ResultCache(log_id, "__platform__", user.id)
    cache_key = f"{_GUIDANCE_KEY}__import_quality"

    if not body.force:
        cached = await cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("output_hash") == output_hash:
            return GuidanceResponse(
                cached=True,
                output_hash=output_hash,
                generated_at=float(cached.get("generated_at", 0.0)),
                model=cached.get("model"),
                provider=cached.get("provider"),
                guidance=GuidanceBody.model_validate(cached["guidance"]),
            )

    cfg = await load_ai_config(session, user.id)
    try:
        guidance = await generate_guidance(
            cfg,
            system_prompt=_QUALITY_SYSTEM,
            payload=payload,
            user_prefix="Assess this imported event log for data quality:",
        )
    except GuidanceError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    record = {
        "guidance": guidance,
        "output_hash": output_hash,
        "generated_at": time.time(),
        "model": cfg.selected_model,
        "provider": cfg.selected_provider,
    }
    await cache.set(cache_key, record)
    return GuidanceResponse(
        cached=False,
        output_hash=output_hash,
        generated_at=record["generated_at"],
        model=record["model"],
        provider=record["provider"],
        guidance=GuidanceBody.model_validate(guidance),
    )
