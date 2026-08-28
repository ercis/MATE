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
from typing import Any, Literal

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mate.sdk import Module, ModuleContext, job, on_event, route

from .layout.model import LAYOUT_VERSION
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


# -- server-side DFG layout ----------------------------------------------------
#
# POST /dfg/layout computes coordinates for the CLIENT-FILTERED visible
# subgraph (the activity/connection sliders live in the panel, so the server
# never sees the full picture) — see `modules/discovery/layout/` for the
# algorithms. Hard cap: past this the response would be unusable in the canvas
# anyway; the IP has its own softer size cap that degrades to heuristic ranks.
_LAYOUT_MAX_NODES = 400
# A complete DFG on 400 nodes is 160k edges — `insert_virtual_nodes` would build
# millions of virtuals long before any of it could be drawn. The node cap alone
# does not bound that.
_LAYOUT_MAX_EDGES = 4000
# Variant sequences handed to the layout (backbone + Mennens ranking). The tail
# beyond the most frequent few hundred variants cannot influence either.
_LAYOUT_MAX_VARIANTS = 500

# Per-request overrides a client may send in `params` — everything else in the
# body is ignored (values are clamped again inside LayoutOptions.from_dict).
# `h_gap`/`v_gap` stay out on purpose: the pixel grid is what lets the canvas
# morph between layout modes instead of rescaling, and that is not the client's
# to change. backbone-v2's routing knobs are safe — they only shape edges.
_LAYOUT_PARAM_KEYS = frozenset(
    {
        "time_limit_s",
        "allow_horizontal_edges",
        "lambda_sq",
        "lambda_end",
        "paper_compat_metrics",
        "seed",
        "max_ip_nodes",
        "route_clearance",
        "port_gap",
        "min_track_gap",
        "max_fillet_radius",
        "merge_len",
        "arrow_gap",
        "pair_bow",
        "route_budget_ms",
    }
)


class LayoutNodeIn(BaseModel):
    id: str
    width: float = 220.0
    height: float = 59.0


class DfgLayoutRequest(BaseModel):
    algorithm: Literal["backbone", "backbone-v2", "sugiyama"] = "backbone"
    nodes: list[LayoutNodeIn] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    start_id: str | None = None
    end_id: str | None = None
    params: dict[str, float | bool] | None = None


def _layout_options(config: Any, params: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve layout options: module config < whitelisted request params."""

    def _cfg(key: str, default: float) -> float:
        try:
            value = config.get(key, None) if config is not None else None
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    options: dict[str, Any] = {
        "time_limit_s": _cfg("layout_solver_time_limit_s", 10.0),
        "lambda_sq": _cfg("layout_lambda_sq", 1.0),
        "lambda_end": _cfg("layout_lambda_end", 1.0),
        "allow_horizontal_edges": bool(
            config.get("layout_allow_horizontal_edges", True) if config is not None else True
        ),
    }
    for key, value in (params or {}).items():
        if key in _LAYOUT_PARAM_KEYS:
            options[key] = value
    return options


def _layout_cache_key(body: DfgLayoutRequest, options: dict[str, Any]) -> str:
    """Digest of everything that determines the layout output.

    LAYOUT_VERSION rotates keys whenever the algorithm code changes behavior;
    re-import and config changes already wipe the module cache platform-side,
    and ephemeral dashboard filters land in their own `_v_*` namespace — so the
    freshness semantics are exactly those of GET /dfg.
    """
    payload = {
        "v": LAYOUT_VERSION,
        "alg": body.algorithm,
        "nodes": sorted((n.id, round(n.width, 1), round(n.height, 1)) for n in body.nodes),
        "edges": sorted([s, t] for s, t in body.edges),
        "start": body.start_id,
        "end": body.end_id,
        "opts": options,
    }
    digest = hashlib.blake2b(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8"),
        digest_size=8,
    ).hexdigest()
    return f"dfg_layout__{digest}"


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


def _dfg_layout_worker(
    path: str, request: dict[str, Any], options: dict[str, Any]
) -> dict[str, Any]:
    """Compute a server-side layout for the client's visible subgraph.

    The heavy imports (pandas here; networkx / ortools inside the pipeline)
    stay worker-side: the API event loop never pays for them.
    """
    import pandas as pd

    from .layout.pipeline import compute_layout

    renamed = _rename_pm4py(pd.read_parquet(path))
    ordered = renamed.sort_values(["case:concept:name", "time:timestamp"], kind="mergesort")
    # Same variant recipe as _filter_variants_coverage: one activity tuple per
    # case, counted — rank 1 is the most frequent variant (the backbone).
    variant_per_case = ordered.groupby("case:concept:name", sort=False)["concept:name"].agg(tuple)
    counts = variant_per_case.value_counts()
    variants = [
        ([str(activity) for activity in sequence], int(count))
        for sequence, count in counts.head(_LAYOUT_MAX_VARIANTS).items()
    ]
    return compute_layout(
        request["nodes"],
        request["edges"],
        variants,
        request.get("start_id"),
        request.get("end_id"),
        options,
    )


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


def _top_counts(counts: Any, n: int) -> dict[str, int]:
    """Top-``n`` entries of an ``{activity: frequency}`` mapping, by frequency."""
    if not isinstance(counts, dict):
        return {}
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return {str(k): int(v) for k, v in ordered}


class DiscoveryModule(Module):
    id = "discovery"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting a discovered process "
        "map (directly-follows graph) and model-size statistics for an event "
        "log. Cite specific activity names, edge frequencies, and start/end "
        "activities. Point out dominant paths, rework loops (edges leading "
        "back to earlier activities) and unusually rare transitions. Suggest "
        "concrete next steps when relevant."
    )
    guidance_user_prefix = "Interpret this discovered process map:"

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        """Compact summary of the cached discovery artifacts for AI guidance.

        Derived exclusively from ``ctx.cache`` - never touches
        ``ctx.event_log``, so it works under the restricted AI/MCP context
        (where every raw event-log accessor raises). Returns ``None`` until
        the import precompute (or a panel visit) has cached a DFG or a Petri
        net.
        """
        dfg = await ctx.cache.get("dfg") if await ctx.cache.exists("dfg") else None
        petri = (
            await ctx.cache.get("petri_net_inductive")
            if await ctx.cache.exists("petri_net_inductive")
            else None
        )
        if not isinstance(dfg, dict) and not isinstance(petri, dict):
            return None

        payload: dict[str, Any] = {}
        if isinstance(dfg, dict):
            activities = [a for a in dfg.get("activities", []) if isinstance(a, dict)]
            edges = [e for e in dfg.get("edges", []) if isinstance(e, dict)]
            top_activities = sorted(activities, key=lambda a: a.get("frequency", 0), reverse=True)[
                :10
            ]
            top_edges = sorted(edges, key=lambda e: e.get("frequency", 0), reverse=True)[:15]
            payload["process_map"] = {
                "activity_count": len(activities),
                "edge_count": len(edges),
                "top_activities_by_frequency": [
                    {"activity": a.get("label") or a.get("id"), "frequency": a.get("frequency")}
                    for a in top_activities
                ],
                "top_edges_by_frequency": [
                    {
                        "source": e.get("source"),
                        "target": e.get("target"),
                        "frequency": e.get("frequency"),
                    }
                    for e in top_edges
                ],
                "start_activities": _top_counts(dfg.get("start_activities"), 10),
                "end_activities": _top_counts(dfg.get("end_activities"), 10),
            }
        if isinstance(petri, dict):
            payload["petri_net_inductive"] = {
                "places": len(petri.get("places", [])),
                "transitions": len(petri.get("transitions", [])),
                "arcs": len(petri.get("arcs", [])),
            }
        return payload

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

    @route.post("/dfg/layout")
    async def dfg_layout(
        self, ctx: ModuleContext, *, body: DfgLayoutRequest | None = None
    ) -> dict[str, Any]:
        """Server-side layout for the client-filtered DFG subgraph.

        Solver trouble is never a 5xx: the response's `solver.status` reports
        optimal / feasible_timeout / fallback_* and coordinates are always
        present. Only protocol errors are 4xx.
        """
        if body is None:
            raise HTTPException(status_code=422, detail="Missing layout request body.")
        if len(body.nodes) > _LAYOUT_MAX_NODES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Layout refused: {len(body.nodes)} nodes "
                    f"(limit {_LAYOUT_MAX_NODES}). Filter the map down first."
                ),
            )
        if len(body.edges) > _LAYOUT_MAX_EDGES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Layout refused: {len(body.edges)} edges "
                    f"(limit {_LAYOUT_MAX_EDGES}). Filter the map down first."
                ),
            )
        known = {node.id for node in body.nodes}
        for source, target in body.edges:
            if source not in known or target not in known:
                raise HTTPException(
                    status_code=422,
                    detail=f"Edge ({source!r}, {target!r}) references an unknown node id.",
                )
        for terminal in (body.start_id, body.end_id):
            if terminal is not None and terminal not in known:
                raise HTTPException(
                    status_code=422,
                    detail=f"Terminal {terminal!r} is not in the node list.",
                )

        options = _layout_options(ctx.config, body.params)
        options["algorithm"] = body.algorithm
        if not body.nodes:
            # Mirrors pipeline._empty_response without importing the compute
            # chain (networkx et al.) into the API process.
            return {
                "kind": "dfg_layout",
                "version": LAYOUT_VERSION,
                "algorithm": body.algorithm,
                "x": {},
                "y": {},
                "rank": {},
                "order": {},
                "edges": [],
                "metrics": {},
                "solver": {"status": "empty", "wall_ms": 0.0, "objective": None},
                "wall_ms": 0.0,
            }

        key = _layout_cache_key(body, options)
        request_payload = body.model_dump(mode="json")
        return await _cached_or_compute(
            ctx, key, lambda: _offload(ctx, _dfg_layout_worker, request_payload, options)
        )

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
