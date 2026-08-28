"""Complexity - basic and enriched EPA-based complexity measures.

Implements Rüschel & Langer's reference ``Complexity.py`` /
``EnrichedComplexity.py`` (see ``ComplexityOriginalRepo/``) against the
platform's normalised event table.

Routes
------
GET ``/metrics``         - basic + enriched bundle (cached).
GET ``/enriched-available`` - quick yes/no for the enriched view.

Precompute
----------
``log.imported`` triggers the same computation so KPIs are warm on first
panel open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route

from .complexity_core import compute_basic_metrics
from .enriched_core import compute_enriched_metrics, is_enriched_supported

# ── Cache helpers (mirrors performance / discovery pattern) ──────────────────


def _cache_is_fresh(ctx: ModuleContext, key: str) -> bool:
    cache_root = Path(ctx.cache.dir) if hasattr(ctx.cache, "dir") else None  # type: ignore[attr-defined]
    if cache_root is None:
        return False
    candidate = cache_root / f"{key}.json"
    if not candidate.exists():
        return False
    try:
        events_path = ctx.event_log.events_path  # type: ignore[attr-defined]
    except AttributeError:
        return False
    try:
        return candidate.stat().st_mtime >= events_path.stat().st_mtime
    except FileNotFoundError:
        return False


async def _cached_or_compute(ctx: ModuleContext, key: str, compute: Any) -> dict[str, Any]:
    if _cache_is_fresh(ctx, key):
        cached = await ctx.cache.get(key)
        if cached is not None:
            return cached
    result = await compute()
    await ctx.cache.set(key, result)
    return result


def _read_detected_schema(ctx: ModuleContext) -> dict[str, Any] | None:
    try:
        events_path = ctx.event_log.events_path  # type: ignore[attr-defined]
    except AttributeError:
        return None
    meta_path = events_path.parent / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    schema = meta.get("detected_schema")
    return schema if isinstance(schema, dict) else None


def _exponential_k(ctx: ModuleContext) -> float:
    try:
        return float(ctx.config.get("exponential_k", 1.0))
    except (TypeError, ValueError):
        return 1.0


# ── Module ───────────────────────────────────────────────────────────────────


class ComplexityModule(Module):
    id = "complexity"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting structural and "
        "entropy-based complexity metrics for a single event log. Reference "
        "specific metric names and values. Variant entropy and Pentland's "
        "process complexity capture variability; structure and affinity "
        "capture topology; trace length and distinct-traces percentage capture "
        "scale. Suggest concrete next steps when relevant."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        if not await ctx.cache.exists("metrics"):
            return None
        return await ctx.cache.get("metrics")

    @route.get("/metrics")
    async def metrics(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(ctx, "metrics", lambda: self._compute(ctx))

    @route.get("/enriched-available")
    async def enriched_available(self, ctx: ModuleContext) -> dict[str, bool]:
        schema = _read_detected_schema(ctx)
        return {"available": bool(is_enriched_supported(schema))}

    @on_event("log.imported")
    @job(progress=True, title="Complexity - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        await ctx.progress.update(0.0, "Loading log")
        result = await self._compute(ctx)
        await ctx.progress.update(0.95, "Caching")
        await ctx.cache.set("metrics", result)
        await ctx.progress.update(1.0, "Done")

    async def _compute(self, ctx: ModuleContext) -> dict[str, Any]:
        schema = _read_detected_schema(ctx)
        enriched_supported = bool(is_enriched_supported(schema))
        k = _exponential_k(ctx)

        async with ctx.event_log as log:
            df = await log.pandas()

        # Forward the compute thread's coarse per-stage ticks to the job's
        # progress stream (scaled into [0.05, 0.9]) so a slow log shows live
        # stages instead of an apparently stalled bar.
        loop = asyncio.get_running_loop()
        basic_span = 0.7 if enriched_supported else 1.0

        def report(fraction: float, message: str) -> None:
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(
                    ctx.progress.update(0.05 + min(max(fraction, 0.0), 1.0) * 0.85, message),
                    loop,
                )

        def _run() -> dict[str, Any]:
            basic = compute_basic_metrics(
                df,
                exponential_k=k,
                progress=lambda f, m: report(f * basic_span, m),
            )
            enriched = None
            if enriched_supported:
                report(0.75, "Computing enriched entropies")
                enriched = compute_enriched_metrics(df, exponential_k=k, basic_metrics=basic)
            return {
                "kind": "complexity_metrics",
                "basic": basic,
                "enriched": enriched,
                "enriched_supported": enriched_supported,
                "exponential_k": k,
            }

        return await asyncio.to_thread(_run)
