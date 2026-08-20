"""Log evolution - how the event log develops over time.

Turns a log into per-period volume series (case arrivals, completions,
work-in-progress, activity mix) plus a dotted chart, so you can see growth,
backlog build-up, seasonality and drift at a glance.

Routes
------
GET ``/timeseries`` - arrivals / completions / WIP / events / activity-mix for a
chosen calendar granularity (cached, offloaded to a worker thread).
GET ``/dotted`` - dotted-chart points (x = time, y = case rank), down-sampled
above ``max_points`` (cached).

Precompute
----------
``log.imported`` warms the default ``/timeseries`` view (auto granularity) so
the first panel open is instant. The (potentially large) dotted chart stays
on-demand.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route

from .evolution import compute_dotted, compute_evolution

_GRANULARITIES = ("auto", "daily", "weekly", "monthly", "quarterly", "yearly")
_DEFAULT_GRANULARITY = "auto"


# ── Cache helpers (mirrors complexity_over_time / performance pattern) ─────────


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


def _resolve_granularity(granularity: str) -> str:
    return granularity if granularity in _GRANULARITIES else _DEFAULT_GRANULARITY


# ── Module ─────────────────────────────────────────────────────────────────────


class LogEvolutionModule(Module):
    id = "log_evolution"

    guidance_system_prompt = (
        "You are a process-mining analyst reading how an event log develops over "
        "time. Each point is a calendar period. 'arrivals' are cases that start "
        "in the period, 'completions' are cases that finish, 'active' is the "
        "work-in-progress (open cases) at the period's end, and 'events' is total "
        "event volume. Read the series for growth, backlog build-up (arrivals "
        "outpacing completions), seasonality, ramp-down, and regime shifts. "
        "Reference specific period labels and values, and suggest concrete next "
        "steps when relevant."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        key = _cache_key(_DEFAULT_GRANULARITY)
        if not await ctx.cache.exists(key):
            return None
        series = await ctx.cache.get(key)
        if not isinstance(series, dict):
            return None
        # Drop the activity-mix matrix; the headline volume series is enough for
        # the LLM to read the trajectory.
        return {
            "granularity": series.get("granularity"),
            "freq": series.get("freq"),
            "periods": series.get("periods"),
            "arrivals": series.get("arrivals"),
            "completions": series.get("completions"),
            "active": series.get("active"),
            "events": series.get("events"),
        }

    @route.get("/timeseries")
    async def timeseries(
        self,
        ctx: ModuleContext,
        granularity: str = _DEFAULT_GRANULARITY,
    ) -> dict[str, Any]:
        granularity = _resolve_granularity(granularity)

        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                df = await log.pandas()
            return await asyncio.to_thread(compute_evolution, df, granularity)

        return await _cached_or_compute(ctx, _cache_key(granularity), _compute)

    @route.get("/dotted")
    async def dotted(
        self,
        ctx: ModuleContext,
        max_points: int = 8000,
    ) -> dict[str, Any]:
        max_points = max(1, int(max_points))

        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                df = await log.pandas()
            return await asyncio.to_thread(compute_dotted, df, max_points)

        return await _cached_or_compute(ctx, f"dotted_{max_points}", _compute)

    @on_event("log.imported")
    @job(progress=True, title="Log evolution - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        await ctx.progress.update(0.0, "Loading log")
        async with ctx.event_log as log:
            df = await log.pandas()
        await ctx.progress.update(0.4, "Aggregating over time")
        result = await asyncio.to_thread(compute_evolution, df, _DEFAULT_GRANULARITY)
        await ctx.progress.update(0.95, "Caching")
        await ctx.cache.set(_cache_key(_DEFAULT_GRANULARITY), result)
        await ctx.progress.update(1.0, "Done")


def _cache_key(granularity: str) -> str:
    """Deterministic cache key so route + precompute + guidance agree."""
    return f"evo_{granularity}"
