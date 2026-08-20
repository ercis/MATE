"""Discovery - DFG, Petri nets (Alpha / Inductive), Process Tree, Heuristics Net.

Each route runs the relevant pm4py discovery algorithm against the log's
events.parquet, serialises the output to a JSON shape consumed by the
xyflow canvases on the frontend, and caches under
``data/module_results/{log_id}/discovery/{key}.json``.

A `precompute` handler subscribed to ``log.imported`` runs all five
algorithms once per import so the frontend hits cache on first paint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

from mate.sdk import Module, ModuleContext, job, on_event, route

from .serializers import (
    serialize_bpmn,
    serialize_dfg,
    serialize_heuristics_net,
    serialize_petri_net,
    serialize_prefix_tree,
    serialize_process_tree,
)

# ILP miner builds a linear program whose memory scales super-linearly in
# the number of distinct activities and traces. Past these caps pm4py
# routinely OOM-kills the container (exit 137), which kills every other
# in-flight request too. We'd rather fail fast with a 413.
_ILP_MAX_ACTIVITIES = 30
_ILP_MAX_CASES = 5_000

_HEURISTICS_DEFAULTS: dict[str, float] = {
    "dependency_threshold": 0.5,
    "and_threshold": 0.65,
    "loop_two_threshold": 0.5,
}


def _heuristics_thresholds(
    config: Any,
    *,
    dependency_threshold: float | None = None,
    and_threshold: float | None = None,
    loop_two_threshold: float | None = None,
) -> dict[str, float]:
    """Resolve heuristics-net thresholds.

    Precedence: explicit query-param > module config > package default.
    """
    overrides = {
        "dependency_threshold": dependency_threshold,
        "and_threshold": and_threshold,
        "loop_two_threshold": loop_two_threshold,
    }
    out: dict[str, float] = {}
    for k, default in _HEURISTICS_DEFAULTS.items():
        explicit = overrides[k]
        if explicit is not None:
            out[k] = float(explicit)
            continue
        from_cfg = config.get(f"heuristics_{k}", None) if config is not None else None
        out[k] = float(from_cfg if from_cfg is not None else default)
    return out


def _heuristics_cache_key(thresholds: dict[str, float]) -> str:
    h = hashlib.blake2b(
        json.dumps(thresholds, sort_keys=True).encode("utf-8"),
        digest_size=4,
    ).hexdigest()
    return f"heuristics_net__{h}"


async def _cached_or_compute(
    ctx: ModuleContext,
    key: str,
    compute: Any,
    *,
    min_version: int = 0,
) -> dict[str, Any]:
    """Return the cached result if present AND `result["version"] >= min_version`.

    Uses only the sanctioned cache Protocol (`exists`/`get`/`set`). The result
    cache is keyed per `(log_id, module_id)` and the platform invalidates it
    automatically on re-import and on config change (see `modules/README.md`,
    §"ctx.cache": "Caches are invalidated automatically when the log changes
    (re-import) or when the module config changes."), so no manual freshness
    check is needed. The `min_version` gate lets a callsite invalidate snapshots
    from before a serializer-shape bump without renaming the cache key.
    """
    if await ctx.cache.exists(key):
        cached = await ctx.cache.get(key)
        if cached is not None:
            cached_version = cached.get("version", 0) if isinstance(cached, dict) else 0
            if cached_version >= min_version:
                return cached
    result = await compute()
    await ctx.cache.set(key, result)
    return result


def _rename_pm4py(df: Any) -> Any:
    return df.rename(
        columns={
            "case_id": "case:concept:name",
            "activity": "concept:name",
            "timestamp": "time:timestamp",
        }
    )


def _filter_variants_coverage(renamed: Any, coverage: float) -> Any:
    """Keep the most frequent variants that cumulatively cover ``coverage``.

    ``coverage`` is a 0..1 fraction of cases. Variants are ranked by case
    frequency (descending) and kept until their cumulative share reaches
    ``coverage``, dropping the long tail. This mirrors the semantics of the
    ``pm4py.filter_variants_percentage`` helper that was removed in pm4py 2.7.
    """
    ordered = renamed.sort_values(["case:concept:name", "time:timestamp"], kind="mergesort")
    # One variant (ordered activity tuple) per case.
    variant_per_case = ordered.groupby("case:concept:name", sort=False)["concept:name"].agg(tuple)
    counts = variant_per_case.value_counts()  # variant -> n cases, descending
    total = counts.sum()
    if total == 0:
        return renamed
    # Keep variants up to and including the one that crosses the threshold.
    prev_cumulative = counts.cumsum().shift(fill_value=0) / total
    kept_variants = set(counts.index[prev_cumulative < coverage])
    kept_cases = variant_per_case[variant_per_case.map(lambda v: v in kept_variants)].index
    return renamed[renamed["case:concept:name"].isin(kept_cases)]


def _activity_mean_trace_position(renamed: Any) -> dict[str, float]:
    """Mean normalised position (0..1) of each activity within its trace.

    For each event we compute its 0..1 position inside its case (event index
    over trace length - 1), then average per activity. Single-event traces
    contribute 0.0. The result is the frontend's "when does this activity
    tend to happen" sort key - lets the DFG canvas order within-layer nodes
    by real temporal execution rather than by frequency.
    """
    sorted_df = renamed.sort_values(["case:concept:name", "time:timestamp"], kind="mergesort")
    grouped = sorted_df.groupby("case:concept:name", sort=False)
    # Per-event 0-based index inside its case, and case length.
    cum_index = grouped.cumcount()
    case_size = grouped["concept:name"].transform("size")
    # Length-1 cases produce a 0/0; map them to 0.0 explicitly.
    denom = (case_size - 1).where(case_size > 1, 1)
    positions = (cum_index / denom).where(case_size > 1, 0.0)

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for activity, pos in zip(sorted_df["concept:name"].tolist(), positions.tolist(), strict=False):
        if pos is None or (isinstance(pos, float) and pos != pos):
            continue
        key = str(activity)
        sums[key] = sums.get(key, 0.0) + float(pos)
        counts[key] = counts.get(key, 0) + 1
    return {k: sums[k] / counts[k] for k in sums}


def _edge_mean_durations(renamed: Any) -> dict[tuple[str, str], float]:
    """Mean transition time (in seconds) per (a, b) directly-follows pair.

    Computed by sorting events by case + timestamp, taking the in-case
    LEAD across (activity, timestamp), and grouping the resulting deltas.
    Used by the DFG view's "Edge label: Duration" mode.
    """
    sorted_df = renamed.sort_values(["case:concept:name", "time:timestamp"], kind="mergesort")
    grouped = sorted_df.groupby("case:concept:name", sort=False)
    next_act = grouped["concept:name"].shift(-1)
    next_ts = grouped["time:timestamp"].shift(-1)
    delta_seconds = (next_ts - sorted_df["time:timestamp"]).dt.total_seconds()

    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    for src, tgt, dur in zip(
        sorted_df["concept:name"].tolist(),
        next_act.tolist(),
        delta_seconds.tolist(),
        strict=False,
    ):
        if tgt is None or (isinstance(tgt, float) and tgt != tgt):
            continue
        if dur is None or (isinstance(dur, float) and dur != dur):
            continue
        key = (str(src), str(tgt))
        sums[key] = sums.get(key, 0.0) + float(dur)
        counts[key] = counts.get(key, 0) + 1
    return {k: sums[k] / counts[k] for k in sums}


# -- process-pool offload -----------------------------------------------------
#
# pm4py mining is CPU-bound and GIL-heavy, so each algorithm runs on its own
# core via `ctx.run_in_process` (§8.3). The worker is a top-level, pure function
# that receives a Parquet *path* (handed over by `materialize_parquet`, so no
# multi-million-row DataFrame is pickled) and reads it with plain pandas - it
# never imports the platform. The reflows below are byte-identical to the former
# `asyncio.to_thread` closures; only where the work runs changed.


async def _offload(ctx: ModuleContext, worker: Any, *args: Any) -> dict[str, Any]:
    """Run `worker(parquet_path, *args)` on a pool core, handing it the current
    (filtered) view as a Parquet path and removing any temp file afterwards."""
    async with ctx.event_log as log:
        path, is_temp = await log.materialize_parquet()
    try:
        return await ctx.run_in_process(worker, path, *args)
    finally:
        if is_temp:
            await asyncio.to_thread(os.remove, path)


async def _guard_ilp_size(ctx: ModuleContext) -> None:
    """Refuse ILP inputs that would OOM-kill the container. Counted cheaply in
    DuckDB (GIL-free) in-process, so the worker only mines - and so the 413 is
    raised here rather than across the pool boundary."""
    async with ctx.event_log as log:
        rows = await log.duckdb_fetch(
            "SELECT COUNT(DISTINCT activity), COUNT(DISTINCT case_id) FROM events"
        )
    n_activities = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    n_cases = int(rows[0][1]) if rows and rows[0][1] is not None else 0
    if n_activities > _ILP_MAX_ACTIVITIES or n_cases > _ILP_MAX_CASES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"ILP miner refused: log has {n_activities} distinct activities and "
                f"{n_cases} cases (limits: {_ILP_MAX_ACTIVITIES} activities, "
                f"{_ILP_MAX_CASES} cases). Try the Inductive or Alpha+ miner instead."
            ),
        )


def _dfg_worker(path: str, variant_pct: float | None) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    renamed = _rename_pm4py(pd.read_parquet(path))
    filtered = renamed
    if variant_pct is not None and variant_pct < 1.0:
        filtered = _filter_variants_coverage(renamed, variant_pct)
    dfg, start, end = pm4py.discover_dfg(filtered)
    durations = _edge_mean_durations(filtered)
    mean_positions = _activity_mean_trace_position(filtered)
    return serialize_dfg(dfg, start, end, durations=durations, mean_positions=mean_positions)


def _petri_alpha_worker(path: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    net, im, fm = pm4py.discover_petri_net_alpha(_rename_pm4py(pd.read_parquet(path)))
    return serialize_petri_net(net, im, fm)


def _petri_alpha_plus_worker(path: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    net, im, fm = pm4py.discover_petri_net_alpha_plus(_rename_pm4py(pd.read_parquet(path)))
    return serialize_petri_net(net, im, fm)


def _petri_inductive_worker(path: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    net, im, fm = pm4py.discover_petri_net_inductive(_rename_pm4py(pd.read_parquet(path)))
    return serialize_petri_net(net, im, fm)


def _petri_imf_worker(path: str, noise_threshold: float) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    net, im, fm = pm4py.discover_petri_net_inductive(
        _rename_pm4py(pd.read_parquet(path)), noise_threshold=noise_threshold
    )
    return serialize_petri_net(net, im, fm)


def _petri_ilp_worker(path: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    net, im, fm = pm4py.discover_petri_net_ilp(_rename_pm4py(pd.read_parquet(path)))
    return serialize_petri_net(net, im, fm)


def _process_tree_worker(path: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    tree = pm4py.discover_process_tree_inductive(_rename_pm4py(pd.read_parquet(path)))
    return serialize_process_tree(tree)


def _process_tree_imf_worker(path: str, noise_threshold: float) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    tree = pm4py.discover_process_tree_inductive(
        _rename_pm4py(pd.read_parquet(path)), noise_threshold=noise_threshold
    )
    return serialize_process_tree(tree)


def _process_tree_via_petri_worker(path: str, algo: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    renamed = _rename_pm4py(pd.read_parquet(path))
    if algo == "alpha":
        net, im, fm = pm4py.discover_petri_net_alpha(renamed)
    elif algo == "alpha-plus":
        net, im, fm = pm4py.discover_petri_net_alpha_plus(renamed)
    elif algo == "ilp":
        net, im, fm = pm4py.discover_petri_net_ilp(renamed)
    else:
        raise ValueError(f"Unknown algo: {algo!r}")
    tree = pm4py.convert_to_process_tree(net, im, fm)
    return serialize_process_tree(tree)


def _heuristics_net_worker(
    path: str,
    dependency_threshold: float,
    and_threshold: float,
    loop_two_threshold: float,
) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    hnet = pm4py.discover_heuristics_net(
        _rename_pm4py(pd.read_parquet(path)),
        dependency_threshold=dependency_threshold,
        and_threshold=and_threshold,
        loop_two_threshold=loop_two_threshold,
    )
    return serialize_heuristics_net(hnet)


def _bpmn_inductive_worker(path: str) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    return serialize_bpmn(pm4py.discover_bpmn_inductive(_rename_pm4py(pd.read_parquet(path))))


def _bpmn_imf_worker(path: str, noise_threshold: float) -> dict[str, Any]:
    import pandas as pd
    import pm4py

    bpmn_graph = pm4py.discover_bpmn_inductive(
        _rename_pm4py(pd.read_parquet(path)), noise_threshold=noise_threshold
    )
    return serialize_bpmn(bpmn_graph)


def _prefix_tree_worker(path: str) -> dict[str, Any]:
    import pandas as pd

    renamed = _rename_pm4py(pd.read_parquet(path))
    sorted_df = renamed.sort_values(["case:concept:name", "time:timestamp"], kind="mergesort")
    cases: list[list[str]] = (
        sorted_df.groupby("case:concept:name", sort=False)["concept:name"].apply(list).tolist()
    )
    return serialize_prefix_tree(cases)


class DiscoveryModule(Module):
    id = "discovery"

    # -- compute helpers (reusable from routes + precompute). Each offloads to a
    # process-pool core via `_offload` + the top-level `_*_worker` fns above; the
    # route/precompute callers and the `ctx.cache` keys are unchanged. ---------

    async def _compute_dfg(
        self, ctx: ModuleContext, *, variant_pct: float | None = None
    ) -> dict[str, Any]:
        return await _offload(ctx, _dfg_worker, variant_pct)

    async def _compute_petri_alpha(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _petri_alpha_worker)

    async def _compute_petri_inductive(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _petri_inductive_worker)

    async def _compute_process_tree(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _process_tree_worker)

    async def _compute_heuristics_net(
        self,
        ctx: ModuleContext,
        *,
        dependency_threshold: float,
        and_threshold: float,
        loop_two_threshold: float,
    ) -> dict[str, Any]:
        return await _offload(
            ctx, _heuristics_net_worker, dependency_threshold, and_threshold, loop_two_threshold
        )

    async def _compute_petri_alpha_plus(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _petri_alpha_plus_worker)

    async def _compute_petri_ilp(self, ctx: ModuleContext) -> dict[str, Any]:
        await _guard_ilp_size(ctx)
        return await _offload(ctx, _petri_ilp_worker)

    async def _compute_petri_imf(
        self, ctx: ModuleContext, *, noise_threshold: float
    ) -> dict[str, Any]:
        return await _offload(ctx, _petri_imf_worker, noise_threshold)

    async def _compute_process_tree_imf(
        self, ctx: ModuleContext, *, noise_threshold: float
    ) -> dict[str, Any]:
        return await _offload(ctx, _process_tree_imf_worker, noise_threshold)

    async def _compute_process_tree_via_petri(
        self, ctx: ModuleContext, algo: str
    ) -> dict[str, Any]:
        if algo == "ilp":
            await _guard_ilp_size(ctx)
        return await _offload(ctx, _process_tree_via_petri_worker, algo)

    async def _compute_bpmn_inductive(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _bpmn_inductive_worker)

    async def _compute_bpmn_imf(
        self, ctx: ModuleContext, *, noise_threshold: float
    ) -> dict[str, Any]:
        return await _offload(ctx, _bpmn_imf_worker, noise_threshold)

    async def _compute_prefix_tree(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _offload(ctx, _prefix_tree_worker)

    # -- routes ---------------------------------------------------------------

    @route.get("/dfg")
    async def dfg(self, ctx: ModuleContext, *, variant_pct: float | None = None) -> dict[str, Any]:
        if variant_pct is not None:
            vp = max(0.0, min(1.0, float(variant_pct)))
            key = f"dfg_variants_{vp:.2f}"
            return await _cached_or_compute(
                ctx, key, lambda: self._compute_dfg(ctx, variant_pct=vp)
            )
        # min_version=3: mean_trace_position was added in v3; force-recompute
        # older caches that don't have it (v2 added durations).
        return await _cached_or_compute(ctx, "dfg", lambda: self._compute_dfg(ctx), min_version=3)

    @route.get("/petri-net/alpha")
    async def petri_alpha(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx, "petri_net_alpha", lambda: self._compute_petri_alpha(ctx)
        )

    @route.get("/petri-net/inductive")
    async def petri_inductive(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx, "petri_net_inductive", lambda: self._compute_petri_inductive(ctx)
        )

    @route.get("/process-tree")
    async def process_tree(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx, "process_tree", lambda: self._compute_process_tree(ctx)
        )

    @route.get("/heuristics-net")
    async def heuristics_net(
        self,
        ctx: ModuleContext,
        *,
        dependency_threshold: float | None = None,
        and_threshold: float | None = None,
        loop_two_threshold: float | None = None,
    ) -> dict[str, Any]:
        thresholds = _heuristics_thresholds(
            ctx.config,
            dependency_threshold=dependency_threshold,
            and_threshold=and_threshold,
            loop_two_threshold=loop_two_threshold,
        )
        key = _heuristics_cache_key(thresholds)
        return await _cached_or_compute(
            ctx, key, lambda: self._compute_heuristics_net(ctx, **thresholds)
        )

    @route.get("/petri-net/alpha-plus")
    async def petri_alpha_plus(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx, "petri_net_alpha_plus", lambda: self._compute_petri_alpha_plus(ctx)
        )

    @route.get("/petri-net/ilp")
    async def petri_ilp(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(ctx, "petri_net_ilp", lambda: self._compute_petri_ilp(ctx))

    @route.get("/petri-net/imf")
    async def petri_imf(
        self, ctx: ModuleContext, *, noise_threshold: float | None = None
    ) -> dict[str, Any]:
        nt = float(noise_threshold) if noise_threshold is not None else 0.2
        key = f"petri_net_imf__{nt:.3f}"
        return await _cached_or_compute(
            ctx, key, lambda: self._compute_petri_imf(ctx, noise_threshold=nt)
        )

    @route.get("/process-tree/imf")
    async def process_tree_imf(
        self, ctx: ModuleContext, *, noise_threshold: float | None = None
    ) -> dict[str, Any]:
        nt = float(noise_threshold) if noise_threshold is not None else 0.2
        key = f"process_tree_imf__{nt:.3f}"
        return await _cached_or_compute(
            ctx, key, lambda: self._compute_process_tree_imf(ctx, noise_threshold=nt)
        )

    @route.get("/process-tree/alpha")
    async def process_tree_alpha(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx,
            "process_tree_alpha",
            lambda: self._compute_process_tree_via_petri(ctx, "alpha"),
        )

    @route.get("/process-tree/alpha-plus")
    async def process_tree_alpha_plus(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx,
            "process_tree_alpha_plus",
            lambda: self._compute_process_tree_via_petri(ctx, "alpha-plus"),
        )

    @route.get("/process-tree/ilp")
    async def process_tree_ilp(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(
            ctx,
            "process_tree_ilp",
            lambda: self._compute_process_tree_via_petri(ctx, "ilp"),
        )

    @route.get("/prefix-tree")
    async def prefix_tree(self, ctx: ModuleContext) -> dict[str, Any]:
        return await _cached_or_compute(ctx, "prefix_tree", lambda: self._compute_prefix_tree(ctx))

    # -- BPMN -----------------------------------------------------------------

    async def _resolve_active_bpmn(
        self,
        ctx: ModuleContext,
        *,
        algo: str = "inductive",
        noise_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Resolve the algorithm-derived (cached) BPMN payload.

        The BPMN view is read-only - there is no user-edited or uploaded model
        to take precedence. The plain Inductive Miner is used by default;
        ``algo == "imf"`` re-mines with the Infrequent variant at
        ``noise_threshold`` to structurally prune the least-used behaviour.
        """
        if algo == "imf":
            nt = float(noise_threshold) if noise_threshold is not None else 0.2
            key = f"bpmn_imf__{nt:.3f}"
            return await _cached_or_compute(
                ctx, key, lambda: self._compute_bpmn_imf(ctx, noise_threshold=nt)
            )
        return await _cached_or_compute(
            ctx, "bpmn_inductive", lambda: self._compute_bpmn_inductive(ctx)
        )

    @route.get("/bpmn")
    async def bpmn(
        self,
        ctx: ModuleContext,
        *,
        algo: str | None = None,
        noise_threshold: float | None = None,
    ) -> dict[str, Any]:
        return await self._resolve_active_bpmn(
            ctx, algo=algo or "inductive", noise_threshold=noise_threshold
        )

    @route.get("/bpmn/download")
    async def bpmn_download(self, ctx: ModuleContext) -> Response:
        payload = await self._resolve_active_bpmn(ctx)
        xml = str(payload.get("xml", ""))
        return Response(
            content=xml,
            media_type="application/bpmn+xml",
            headers={"Content-Disposition": 'attachment; filename="process.bpmn"'},
        )

    # -- precompute on import -------------------------------------------------

    @on_event("log.imported")
    @job(progress=True, title="Discovery - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        thresholds = _heuristics_thresholds(ctx.config)
        default_nt = 0.2
        stages: list[tuple[str, Any]] = [
            ("dfg", lambda: self._compute_dfg(ctx)),
            ("petri_net_alpha", lambda: self._compute_petri_alpha(ctx)),
            ("petri_net_alpha_plus", lambda: self._compute_petri_alpha_plus(ctx)),
            ("petri_net_inductive", lambda: self._compute_petri_inductive(ctx)),
            (
                f"petri_net_imf__{default_nt:.3f}",
                lambda: self._compute_petri_imf(ctx, noise_threshold=default_nt),
            ),
            ("process_tree", lambda: self._compute_process_tree(ctx)),
            (
                f"process_tree_imf__{default_nt:.3f}",
                lambda: self._compute_process_tree_imf(ctx, noise_threshold=default_nt),
            ),
            ("prefix_tree", lambda: self._compute_prefix_tree(ctx)),
            ("bpmn_inductive", lambda: self._compute_bpmn_inductive(ctx)),
            (
                _heuristics_cache_key(thresholds),
                lambda: self._compute_heuristics_net(ctx, **thresholds),
            ),
        ]

        total = len(stages)
        for i, (key, fn) in enumerate(stages):
            await ctx.progress.update(i, total=total, stage=key, message=key)
            result = await fn()
            await ctx.cache.set(key, result)
        await ctx.progress.update(total, total=total, stage="done", message="done")
