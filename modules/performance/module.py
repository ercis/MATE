"""Performance - KPIs, bottlenecks, performance DFG, cycle time distribution.

Heavy DuckDB SQL for the bulk of the per-case stats; pm4py for per-activity
sojourn / service time. Each result is cached under
``data/module_results/{log_id}/performance/{key}.json`` and invalidated
when the events.parquet mtime advances.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, route

from .compute import (
    CYCLE_HISTOGRAM_SQL,
    KPI_SQL,
    PER_ACTIVITY_FREQ_SQL,
    VARIANTS_SQL,
    compute_throughput_per_day,
    detect_bottlenecks,
    log_spaced_histogram,
    quantile_from_sorted,
)


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


def _renamed(df: Any) -> Any:
    return df.rename(
        columns={
            "case_id": "case:concept:name",
            "activity": "concept:name",
            "timestamp": "time:timestamp",
        }
    )


async def _per_activity_sojourn(df: Any, freq_rows: list[tuple]) -> list[dict[str, Any]]:
    """Compute per-activity avg sojourn + samples per activity for histograms.

    pm4py's `get_sojourn_time` works on event logs; for raw frequencies we
    rely on the DuckDB query. Sojourn samples are derived from inter-event
    deltas grouped by activity.
    """

    def _run() -> dict[str, list[float]]:
        import pandas as pd

        sorted_df = df.sort_values(["case_id", "timestamp"])
        sorted_df["next_ts"] = sorted_df.groupby("case_id")["timestamp"].shift(-1)
        sorted_df["sojourn_s"] = (
            (sorted_df["next_ts"] - sorted_df["timestamp"]).dt.total_seconds()
        )
        valid = sorted_df.dropna(subset=["sojourn_s"])
        out: dict[str, list[float]] = {}
        for activity, group in valid.groupby("activity"):
            out[str(activity)] = [float(v) for v in group["sojourn_s"].tolist() if v >= 0]
        return out

    samples_by_activity = await asyncio.to_thread(_run)

    stats: list[dict[str, Any]] = []
    freq_lookup = {row[0]: int(row[1]) for row in freq_rows}
    activities = sorted(set(list(freq_lookup.keys()) + list(samples_by_activity.keys())))
    for activity in activities:
        samples = sorted(samples_by_activity.get(activity, []))
        if samples:
            avg = float(sum(samples) / len(samples))
            p90 = quantile_from_sorted(samples, 0.9)
        else:
            avg = 0.0
            p90 = 0.0
        stats.append(
            {
                "activity": activity,
                "frequency": freq_lookup.get(activity, len(samples)),
                "avg_sojourn_s": avg,
                "p90_sojourn_s": p90,
                "samples": samples,
            }
        )
    return stats


class PerformanceModule(Module):
    id = "performance"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting performance KPIs and "
        "bottleneck rankings for an event log. Cite specific activity names, "
        "cycle-time numbers, and share-of-time percentages. Distinguish "
        "between high-frequency slow steps (volume issue) and rare slow steps "
        "(outliers). Suggest concrete next steps when relevant."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        if not await ctx.cache.exists("kpis"):
            return None
        kpis = await ctx.cache.get("kpis")
        bottlenecks = (
            await ctx.cache.get("bottlenecks") if await ctx.cache.exists("bottlenecks") else None
        )
        # Trim the heavy histograms - the LLM only needs ranking + numbers.
        slim_bottlenecks: list[dict[str, Any]] = []
        if isinstance(bottlenecks, dict):
            for item in bottlenecks.get("items", [])[:10]:
                slim_bottlenecks.append(
                    {
                        k: item.get(k)
                        for k in (
                            "rank",
                            "activity",
                            "frequency",
                            "avg_sojourn_s",
                            "p90_sojourn_s",
                            "share_of_total_time",
                        )
                    }
                )
        return {
            "summary": (kpis or {}).get("summary"),
            "top_activities_by_frequency": sorted(
                (kpis or {}).get("per_activity", []),
                key=lambda a: a.get("frequency", 0),
                reverse=True,
            )[:10],
            "bottlenecks": slim_bottlenecks,
        }

    @route.get("/kpis")
    async def kpis(self, ctx: ModuleContext) -> dict[str, Any]:
        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                kpi_rows = await log.duckdb_fetch(KPI_SQL)
                variants_rows = await log.duckdb_fetch(VARIANTS_SQL)
                freq_rows = await log.duckdb_fetch(PER_ACTIVITY_FREQ_SQL)
                df = await log.pandas()

            (
                cases,
                events,
                avg_cycle,
                median_cycle,
                p90_cycle,
                p95_cycle,
                min_cycle,
                max_cycle,
                earliest,
                latest,
            ) = kpi_rows[0]

            variants = int(variants_rows[0][0]) if variants_rows else 0
            throughput_per_day = compute_throughput_per_day(int(cases or 0), earliest, latest)

            per_activity = await _per_activity_sojourn(df, freq_rows)
            # Trim sample arrays out of the response - they're internal.
            per_activity_payload = [
                {
                    "activity": a["activity"],
                    "frequency": a["frequency"],
                    "avg_sojourn_s": a["avg_sojourn_s"],
                    "p90_sojourn_s": a["p90_sojourn_s"],
                }
                for a in per_activity
            ]

            return {
                "kind": "kpis",
                "summary": {
                    "cases": int(cases or 0),
                    "events": int(events or 0),
                    "variants": variants,
                    "avg_cycle_time_s": float(avg_cycle or 0.0),
                    "median_cycle_time_s": float(median_cycle or 0.0),
                    "p90_cycle_time_s": float(p90_cycle or 0.0),
                    "p95_cycle_time_s": float(p95_cycle or 0.0),
                    "min_cycle_time_s": float(min_cycle or 0.0),
                    "max_cycle_time_s": float(max_cycle or 0.0),
                    "throughput_cases_per_day": float(throughput_per_day),
                    "lead_time_s": float(avg_cycle or 0.0),
                },
                "per_activity": per_activity_payload,
            }

        return await _cached_or_compute(ctx, "kpis", _compute)

    @route.get("/bottlenecks")
    async def bottlenecks(self, ctx: ModuleContext) -> dict[str, Any]:
        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                freq_rows = await log.duckdb_fetch(PER_ACTIVITY_FREQ_SQL)
                df = await log.pandas()

            stats = await _per_activity_sojourn(df, freq_rows)
            total_time = sum(a["avg_sojourn_s"] * a["frequency"] for a in stats) or 1.0
            flagged = detect_bottlenecks(stats)

            items = []
            for rank, item in enumerate(flagged, start=1):
                histogram = log_spaced_histogram(item["samples"], buckets=24)
                items.append(
                    {
                        "rank": rank,
                        "activity": item["activity"],
                        "frequency": item["frequency"],
                        "avg_sojourn_s": item["avg_sojourn_s"],
                        "p90_sojourn_s": item["p90_sojourn_s"],
                        "share_of_total_time": (item["avg_sojourn_s"] * item["frequency"]) / total_time,
                        "histogram": histogram,
                    }
                )

            return {"kind": "bottlenecks", "items": items}

        return await _cached_or_compute(ctx, "bottlenecks", _compute)

    @route.get("/dfg-performance")
    async def dfg_performance(self, ctx: ModuleContext) -> dict[str, Any]:
        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                df = await log.pandas()

            def _run() -> dict[str, Any]:
                import pm4py

                renamed = _renamed(df)
                perf_dfg, start, end = pm4py.discover_performance_dfg(renamed)
                freq_dfg, _, _ = pm4py.discover_dfg(renamed)
                activities: dict[str, int] = {}
                for (src, tgt), freq in freq_dfg.items():
                    activities[src] = activities.get(src, 0) + int(freq)
                    activities[tgt] = activities.get(tgt, 0)
                for a, freq in start.items():
                    activities[a] = max(activities.get(a, 0), int(freq))
                for a, freq in end.items():
                    activities[a] = max(activities.get(a, 0), int(freq))
                def _perf_to_seconds(p: Any) -> float:
                    # pm4py returns either a scalar mean, or a dict of stats
                    # (mean / median / stdev / min / max / sum) depending on
                    # the chosen aggregation. Prefer mean when present.
                    if isinstance(p, dict):
                        for key in ("mean", "median", "average"):
                            if key in p:
                                return float(p[key])
                        # fall back to first numeric value
                        for v in p.values():
                            try:
                                return float(v)
                            except (TypeError, ValueError):
                                continue
                        return 0.0
                    return float(p)

                return {
                    "kind": "dfg_performance",
                    "activities": [
                        {"id": a, "label": a, "frequency": f} for a, f in activities.items()
                    ],
                    "edges": [
                        {
                            "id": f"{src}__{tgt}",
                            "source": src,
                            "target": tgt,
                            "frequency": int(freq_dfg.get((src, tgt), 0)),
                            "performance_seconds": _perf_to_seconds(perf),
                        }
                        for (src, tgt), perf in perf_dfg.items()
                    ],
                    "start_activities": {a: int(f) for a, f in start.items()},
                    "end_activities": {a: int(f) for a, f in end.items()},
                }

            return await asyncio.to_thread(_run)

        return await _cached_or_compute(ctx, "dfg_performance", _compute)

    @route.get("/cycle-time-distribution")
    async def cycle_time_distribution(self, ctx: ModuleContext) -> dict[str, Any]:
        async def _compute() -> dict[str, Any]:
            async with ctx.event_log as log:
                rows = await log.duckdb_fetch(CYCLE_HISTOGRAM_SQL)
                kpi_rows = await log.duckdb_fetch(KPI_SQL)

            (
                _cases,
                _events,
                avg_cycle,
                median_cycle,
                p90_cycle,
                p95_cycle,
                min_cycle,
                max_cycle,
                _earliest,
                _latest,
            ) = kpi_rows[0]

            buckets = [
                {
                    "bucket": int(r[0]),
                    "count": int(r[1]),
                    "bucket_min": float(r[2] or 0.0),
                    "bucket_max": float(r[3] or 0.0),
                }
                for r in rows
            ]

            return {
                "kind": "cycle_time_distribution",
                "buckets": buckets,
                "stats": {
                    "avg_cycle_time_s": float(avg_cycle or 0.0),
                    "median_cycle_time_s": float(median_cycle or 0.0),
                    "p90_cycle_time_s": float(p90_cycle or 0.0),
                    "p95_cycle_time_s": float(p95_cycle or 0.0),
                    "min_cycle_time_s": float(min_cycle or 0.0),
                    "max_cycle_time_s": float(max_cycle or 0.0),
                },
            }

        return await _cached_or_compute(ctx, "cycle_time_distribution", _compute)
