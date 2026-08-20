"""Process Comparison - diff two or more event logs.

Routes take the baseline ``log_id`` (the log the panel is opened on, injected by
the loader) plus a comma-separated ``others`` list of comparison log ids. Each
comparison log is opened through ``ctx.open_event_log`` - the sanctioned,
ownership-checked cross-log accessor - so a user can only ever diff their own
logs. Results are cached under a key that hashes the log set *and* each log's
parquet mtime, so a re-import of any log invalidates the relevant entries.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from mate.sdk import Module, ModuleContext, route

from . import compute as comp
from .serializers import (
    serialize_activity_deltas,
    serialize_dfg_diff,
    serialize_variant_diff,
)


def _parse_others(baseline: str, others: str) -> list[str]:
    """Ordered, de-duplicated comparison ids, with the baseline excluded."""
    seen: set[str] = {baseline}
    out: list[str] = []
    for raw in others.split(","):
        oid = raw.strip()
        if oid and oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out


def _events_mtime(access: Any) -> float:
    path = getattr(access, "events_path", None)
    if not isinstance(path, Path):
        return 0.0
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cache_key(prefix: str, ordered_ids: list[str], mtimes: list[float]) -> str:
    sig = "|".join(f"{i}:{m:.0f}" for i, m in zip(ordered_ids, mtimes, strict=False))
    digest = hashlib.sha1(sig.encode()).hexdigest()[:16]
    return f"{prefix}__{digest}"


class ProcessComparisonModule(Module):
    id = "process_comparison"

    # -- shared loading ------------------------------------------------------

    async def _resolve(self, ctx: ModuleContext, other_ids: list[str]) -> list[Any]:
        """Resolve [baseline, *others] to EventLogAccess instances.

        Opening each comparison log here enforces ownership on *every* request
        (even cache hits), since the cache key depends on the resolved mtimes.
        """
        accessors: list[Any] = [ctx.event_log]
        for oid in other_ids:
            try:
                accessors.append(await ctx.open_event_log(oid))
            except PermissionError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return accessors

    async def _frames(self, accessors: list[Any]) -> list[Any]:
        frames: list[Any] = []
        for access in accessors:
            async with access as log:
                frames.append(await log.pandas())
        return frames

    async def _compute_over_set(
        self, ctx: ModuleContext, prefix: str, others: str, run: Any
    ) -> dict[str, Any]:
        """Resolve [baseline, *others], cache on (ids + mtimes), and run ``run``.

        ``run(ordered_ids, frames)`` is a pure, thread-offloaded function - all
        the per-route logic lives there; everything around it (ownership checks,
        caching, frame loading) is shared.
        """
        other_ids = _parse_others(ctx.log_id, others)
        if not other_ids:
            raise HTTPException(
                status_code=422, detail="Select at least one log to compare against."
            )
        accessors = await self._resolve(ctx, other_ids)
        ordered_ids = [ctx.log_id, *other_ids]
        key = _cache_key(prefix, ordered_ids, [_events_mtime(a) for a in accessors])
        cached = await ctx.cache.get(key)
        if cached is not None:
            return cached
        frames = await self._frames(accessors)
        result = await asyncio.to_thread(run, ordered_ids, frames)
        await ctx.cache.set(key, result)
        return result

    # -- routes --------------------------------------------------------------

    @route.get("/similarity")
    async def similarity(self, ctx: ModuleContext, others: str = "") -> dict[str, Any]:
        def _run(ordered_ids: list[str], frames: list[Any]) -> dict[str, Any]:
            variant_counts = [comp.variant_counts(df) for df in frames]
            metrics = comp.pairwise_similarity(
                activities=[set(comp.activity_frequencies(df)) for df in frames],
                edges=[set(comp.discover_dfg(df)[0]) for df in frames],
                variants=[set(vc) for vc in variant_counts],
                footprints=[comp.footprint_relations(df) for df in frames],
                variant_counts_list=variant_counts,
            )
            return {"kind": "similarity", "log_ids": ordered_ids, "metrics": metrics}

        return await self._compute_over_set(ctx, "similarity", others, _run)

    @route.get("/dfg-overlay")
    async def dfg_overlay(self, ctx: ModuleContext, other: str = "") -> dict[str, Any]:
        other_id = other.strip()
        if not other_id or other_id == ctx.log_id:
            raise HTTPException(status_code=422, detail="Pick one other log to overlay.")
        accessors = await self._resolve(ctx, [other_id])
        ordered_ids = [ctx.log_id, other_id]
        key = _cache_key("dfg_overlay", ordered_ids, [_events_mtime(a) for a in accessors])
        cached = await ctx.cache.get(key)
        if cached is not None:
            return cached

        frames = await self._frames(accessors)

        def _run() -> dict[str, Any]:
            dfg_a, sa, ea = comp.discover_dfg(frames[0])
            dfg_b, sb, eb = comp.discover_dfg(frames[1])
            payload = serialize_dfg_diff(dfg_a, sa, ea, dfg_b, sb, eb)
            payload["baseline_log_id"] = ctx.log_id
            payload["other_log_id"] = other_id
            return payload

        result = await asyncio.to_thread(_run)
        await ctx.cache.set(key, result)
        return result

    @route.get("/variants")
    async def variants(self, ctx: ModuleContext, others: str = "") -> dict[str, Any]:
        def _run(ordered_ids: list[str], frames: list[Any]) -> dict[str, Any]:
            return serialize_variant_diff(ordered_ids, [comp.variant_counts(df) for df in frames])

        return await self._compute_over_set(ctx, "variants", others, _run)

    @route.get("/activity-deltas")
    async def activity_deltas(self, ctx: ModuleContext, others: str = "") -> dict[str, Any]:
        def _run(ordered_ids: list[str], frames: list[Any]) -> dict[str, Any]:
            freqs = [comp.activity_frequencies(df) for df in frames]
            sojourns = [comp.activity_mean_sojourn(df) for df in frames]
            return serialize_activity_deltas(ordered_ids, freqs, sojourns)

        return await self._compute_over_set(ctx, "activity-deltas", others, _run)
