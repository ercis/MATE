"""Complexity v2 - the thesis's full event-log complexity suite.

Implements every metric in scope of Langer's *Understanding Business Process
Complexity* (Table 3.3) against the platform's normalised event table. See
:mod:`metrics_core` for the definitions.

Routes
------
GET ``/metrics``            - the 28-metric suite, grouped by category (cached).
GET ``/transition-matrix``  - directly-follows transition-probability matrix
                              (``prob-act-pairs``) for the heatmap (cached).
GET ``/enriched-available`` - quick yes/no for the enriched-entropy view.

Precompute
----------
``log.imported`` warms the metrics + matrix caches so the panel opens instantly.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route

from .metrics_core import (
    CATEGORY_ORDER,
    METRIC_DEFS,
    compute_all,
    is_enriched_supported,
    transition_probability_matrix,
)

_TRANSITION_TOP_K = 25


# ── Cache helpers (mirror the discovery / complexity pattern) ─────────────────


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


async def _cached_or_compute(
    ctx: ModuleContext, key: str, compute: Callable[[], Any]
) -> dict[str, Any]:
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


def _max_variants(ctx: ModuleContext) -> int:
    try:
        value = int(ctx.config.get("max_variants_distance", 300))
    except (TypeError, ValueError):
        return 300
    return max(25, min(value, 1000))


def _assemble(values: dict[str, Any]) -> dict[str, Any]:
    """Join the flat metric values with :data:`METRIC_DEFS` into the
    category-grouped payload the panel renders (one row per Table 3.3 metric)."""
    groups: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        items = [
            {**d, "value": values.get(d["key"])} for d in METRIC_DEFS if d["category"] == category
        ]
        if items:
            groups.append({"category": category, "items": items})

    return {
        "kind": "complexity_v2",
        "values": values,
        "groups": groups,
        "enriched_supported": bool(values.get("enriched_supported")),
        "downsampled": bool(values.get("downsampled")),
        "n_events": values.get("n_events"),
        "n_cases": values.get("n_sequences"),
        "n_variants": values.get("n_unique_seq"),
        "distance_variants_used": values.get("distance_variants_used"),
        "max_variants": values.get("max_variants"),
    }


# ── Process-pool offload (§8.3) ──────────────────────────────────────────────
#
# The metric suite + transition matrix are CPU-bound; each runs on its own core
# via `ctx.run_in_process`. Workers are top-level pure fns that read a Parquet
# *path* (handed over by `materialize_parquet`) - no DataFrame is pickled, no
# platform import in the worker.


async def _offload(ctx: ModuleContext, worker: Callable[..., Any], *args: Any) -> dict[str, Any]:
    async with ctx.event_log as log:
        path, is_temp = await log.materialize_parquet()
    try:
        return await ctx.run_in_process(worker, path, *args)
    finally:
        if is_temp:
            await asyncio.to_thread(os.remove, path)


def _metrics_worker(
    path: str, detected_schema: dict[str, Any] | None, max_variants: int
) -> dict[str, Any]:
    import pandas as pd

    df = pd.read_parquet(path)
    return _assemble(compute_all(df, detected_schema=detected_schema, max_variants=max_variants))


def _matrix_worker(path: str) -> dict[str, Any]:
    import pandas as pd

    df = pd.read_parquet(path)
    return transition_probability_matrix(df, top_k=_TRANSITION_TOP_K)


# ── Module ─────────────────────────────────────────────────────────────────────


class ComplexityV2Module(Module):
    id = "complexity_v2"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting the full complexity-metric "
        "suite from Langer's thesis for a single event log. Reference specific "
        "metric labels (var-e, nseq-e, activity-var, avg-edit-distance, "
        "structural-var, #-acyclic-paths, perc-unique-seq, ...) and their values. "
        "Entropy and variation metrics capture variability; distance metrics "
        "capture trace heterogeneity; size metrics capture scale. Note that "
        "metrics cluster (size/entropy together; variation/distance together) and "
        "some trade off against each other. Suggest concrete next steps."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        if not await ctx.cache.exists("metrics"):
            return None
        return await ctx.cache.get("metrics")

    @route.get("/metrics")
    async def metrics(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(ctx, "metrics", lambda: self._compute(ctx))

    @route.get("/transition-matrix")
    async def transition_matrix(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(ctx, "transition_matrix", lambda: self._compute_matrix(ctx))

    @route.get("/enriched-available")
    async def enriched_available(self, ctx: ModuleContext) -> dict[str, bool]:
        return {"available": bool(is_enriched_supported(_read_detected_schema(ctx)))}

    @on_event("log.imported")
    @job(progress=True, title="Complexity v2 - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        await ctx.progress.update(0.0, "Loading log")
        schema = _read_detected_schema(ctx)
        max_variants = _max_variants(ctx)

        await ctx.progress.update(0.2, "Computing metrics (entropy, variation, distance)")
        metrics = await _offload(ctx, _metrics_worker, schema, max_variants)
        await ctx.progress.update(0.85, "Computing transition matrix")
        matrix = await _offload(ctx, _matrix_worker)

        await ctx.cache.set("metrics", metrics)
        await ctx.cache.set("transition_matrix", matrix)
        await ctx.progress.update(1.0, "Done")

    async def _compute(self, ctx: ModuleContext) -> dict[str, Any]:
        schema = _read_detected_schema(ctx)
        max_variants = _max_variants(ctx)
        return await _offload(ctx, _metrics_worker, schema, max_variants)

    async def _compute_matrix(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _matrix_worker)
