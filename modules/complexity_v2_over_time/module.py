"""Complexity v2 over time - how the full Langer Table 3.3 suite evolves.

Slices the event log along time (whole cases grouped by start time), runs the
vendored v2 complexity suite (:mod:`metrics_core`) on each slice, and returns a
per-slice metric series the panel plots as a line (x = time, y = a chosen
metric). Same time-slicing mechanism as ``modules/complexity_over_time``; the
per-slice metric bundle is the 28-metric v2 suite instead of the EPA-only core.

Routes
------
GET ``/timeseries`` - slice + compute the series for a chosen mode/params
(cached, offloaded to a worker thread).

Precompute
----------
``log.imported`` warms the default view (calendar / auto granularity) so the
first panel open is instant.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route

from .slicing import compute_timeseries

_MODES = ("absolute", "calendar", "sliding")


# ── Cache helpers (mirrors complexity_over_time / complexity_v2 pattern) ──────


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
    """Read the log's detected IEEE-XES schema (enables enriched entropy)."""
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


def _max_variants(ctx: ModuleContext) -> int:
    try:
        value = int(ctx.config.get("max_variants_distance", 300))
    except (TypeError, ValueError):
        return 300
    return max(25, min(value, 1000))


def _int_config(ctx: ModuleContext, key: str, default: int) -> int:
    try:
        return int(ctx.config.get(key, default))
    except (TypeError, ValueError):
        return default


def _cache_key(
    mode: str,
    *,
    max_variants: int,
    min_cases: int,
    slices: int | None = None,
    granularity: str | None = None,
    window: float | None = None,
    step: float | None = None,
) -> str:
    """Deterministic cache key so route + precompute + guidance agree."""
    if mode == "absolute":
        return f"ts_absolute_n{slices}_v{max_variants}_m{min_cases}"
    if mode == "sliding":
        return f"ts_sliding_w{window}_s{step}_v{max_variants}_m{min_cases}"
    return f"ts_calendar_{granularity}_v{max_variants}_m{min_cases}"


def _default_key(ctx: ModuleContext) -> str:
    return _cache_key(
        "calendar",
        max_variants=_max_variants(ctx),
        min_cases=_int_config(ctx, "min_cases_per_slice", 1),
        granularity="auto",
    )


# ── Module ───────────────────────────────────────────────────────────────────


class ComplexityV2OverTimeModule(Module):
    id = "complexity_v2_over_time"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting how the full complexity "
        "suite from Langer's thesis (Table 3.3) evolves over the lifetime of a "
        "process. Each point is a time slice - whole cases grouped by their "
        "start time - scored with the same size, variation, entropy and "
        "distance metrics (var-e, nseq-e, activity-var, avg-edit-distance, "
        "structural-var, ...) as the base complexity_v2 module. Read the series "
        "for trends, spikes, and regime shifts, reference specific slice labels "
        "and values, and distinguish genuine drift from thin slices (low "
        "n_cases). Suggest concrete next steps when relevant."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        key = _default_key(ctx)
        if not await ctx.cache.exists(key):
            return None
        series = await ctx.cache.get(key)
        if not isinstance(series, dict):
            return None
        # Slim each point to the headline metrics so the LLM gets the trajectory
        # without the full 28-metric payload per slice.
        headline = (
            "var_e",
            "nvar_e",
            "seq_e",
            "nseq_e",
            "structure",
            "affinity",
            "avg_edit_distance",
            "structural_var",
            "activity_var",
        )
        slim_slices = []
        for point in series.get("slices", []):
            metrics = point.get("metrics")
            slim_metrics = (
                {k: metrics.get(k) for k in headline} if isinstance(metrics, dict) else None
            )
            slim_slices.append(
                {
                    "label": point.get("label"),
                    "n_cases": point.get("n_cases"),
                    "n_events": point.get("n_events"),
                    "metrics": slim_metrics,
                }
            )
        return {
            "mode": series.get("mode"),
            "params": series.get("params"),
            "metric_keys": series.get("metric_keys"),
            "slices": slim_slices,
        }

    @route.get("/timeseries")
    async def timeseries(
        self,
        ctx: ModuleContext,
        mode: str = "calendar",
        slices: int = 0,
        granularity: str = "auto",
        window: float = 30.0,
        step: float = 7.0,
    ) -> dict[str, Any]:
        mode = mode if mode in _MODES else "calendar"
        max_variants = _max_variants(ctx)
        min_cases = _int_config(ctx, "min_cases_per_slice", 1)
        schema = _read_detected_schema(ctx)

        if mode == "absolute":
            n = slices if slices and slices > 0 else _int_config(ctx, "default_slices", 50)
            params: dict[str, Any] = {"slices": n}
            key = _cache_key("absolute", max_variants=max_variants, min_cases=min_cases, slices=n)
        elif mode == "sliding":
            params = {"window": float(window), "step": float(step)}
            key = _cache_key(
                "sliding",
                max_variants=max_variants,
                min_cases=min_cases,
                window=float(window),
                step=float(step),
            )
        else:
            params = {"granularity": granularity}
            key = _cache_key(
                "calendar", max_variants=max_variants, min_cases=min_cases, granularity=granularity
            )

        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                df = await log.pandas()
            return await asyncio.to_thread(
                compute_timeseries,
                df,
                mode,
                params,
                detected_schema=schema,
                max_variants=max_variants,
                min_cases=min_cases,
            )

        return await _cached_or_compute(ctx, key, _compute)

    @on_event("log.imported")
    @job(progress=True, title="Complexity v2 over time - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        await ctx.progress.update(0.0, "Loading log")
        max_variants = _max_variants(ctx)
        min_cases = _int_config(ctx, "min_cases_per_slice", 1)
        schema = _read_detected_schema(ctx)
        async with ctx.event_log as log:
            df = await log.pandas()
        await ctx.progress.update(0.2, "Slicing & computing")

        # Per-slice ticks from the compute thread (scaled into [0.2, 0.9])
        # so a many-slice series doesn't look stalled.
        loop = asyncio.get_running_loop()

        def report(fraction: float, message: str) -> None:
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(
                    ctx.progress.update(0.2 + min(max(fraction, 0.0), 1.0) * 0.7, message),
                    loop,
                )

        result = await asyncio.to_thread(
            compute_timeseries,
            df,
            "calendar",
            {"granularity": "auto"},
            detected_schema=schema,
            max_variants=max_variants,
            min_cases=min_cases,
            progress=report,
        )
        await ctx.progress.update(0.95, "Caching")
        await ctx.cache.set(_default_key(ctx), result)
        await ctx.progress.update(1.0, "Done")
