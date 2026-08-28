"""Actor Performance - waiting-time decomposition by actor behavior (ICPM 2024).

Implements Klijn / Tentina / Fahland / Mannhardt, "Decomposing Process Performance
based on Actor Behavior": builds an Event Knowledge Graph of the log in the Neo4j
sidecar (compose profile "graph"), identifies task instances, classifies every case
transition by the behavior of the actors involved (continuation / interruption /
three handover types) and aggregates waiting times per transition and behavior.

Runs `isolation: subprocess` (manifest): promg needs pandas <3 and neo4j-driver <6,
both incompatible with the platform venv. The graph is transient scratch - wiped
before and after every run - and `_RUN_LOCK` serializes runs (Neo4j Community is a
single database), so per-user results only ever live in the per-user `ctx.cache`.

Note on `**_kw`: in subprocess mode the host forwards the route stub's
``*args/**kwargs`` to the handler as ``args=None, kwargs=None`` (the worker can't see
the real signature). Every handler swallows them.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, route

from .pipeline import connection as conn
from .pipeline import results as agg

MODULE_DIR = Path(__file__).resolve().parent

# Bump whenever the cached `result` shape changes; /results treats older stamps
# as empty so the panel re-prompts instead of half-rendering a stale cache.
RESULT_SCHEMA = 1

_REQUIRED_RESULT_KEYS = ("params", "input", "graph", "behavior_totals", "edges")

# One shared graph database -> one run at a time, across all users of this worker.
_RUN_LOCK = asyncio.Lock()


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _result_is_current(cached: Any) -> bool:
    """True only for a cache written by this code version. Pure, unit-testable."""
    if not isinstance(cached, dict):
        return False
    if cached.get("schema") != RESULT_SCHEMA:
        return False
    return all(k in cached for k in _REQUIRED_RESULT_KEYS)


def _setup_hint(detail: str, settings: conn.GraphSettings) -> str:
    """One actionable sentence for the run-failure toast / panel setup card."""
    if detail == "auth-failed":
        return (
            f"Neo4j at {settings.uri} rejected user '{settings.user}' - check the "
            "module settings password (or MATE_NEO4J_PASSWORD / NEO4J_PASSWORD in .env)."
        )
    return (
        f"Neo4j is not reachable at {settings.uri}. Start the graph sidecar "
        "(COMPOSE_PROFILES=graph, see DEPLOY.md §4b) or, in host-mode dev, the "
        "docker run one-liner in modules/actor_performance/README.md."
    )


class ActorPerformanceModule(Module):
    id = "actor_performance"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting an actor-behavior performance "
        "decomposition (Klijn et al., ICPM 2024). Waiting time on each transition is "
        "split by actor behavior: 'continuation' (same actor, back-to-back), "
        "'interruption' (same actor, other work in between), 'handover_idle' (next "
        "actor had been idle), 'handover_prioritized' (next actor squeezed it in "
        "mid-task) and 'handover_deprioritized' (next actor finished other work "
        "first). Relate mean waits per class to their share of instances - a rare "
        "class with huge waits reads differently from a common slightly-slow one. "
        "Only aggregate statistics are available here - never individual events."
    )
    guidance_user_prefix = "Interpret this actor-behavior decomposition summary:"

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        """Aggregates only (cache-read, restricted-context safe): run params, log
        sizes, graph sizes, the log-wide behavior totals and the top transitions."""
        cached = await ctx.cache.get("result")
        if not _result_is_current(cached):
            return None

        top_edges = []
        for edge in (cached.get("edges") or [])[:10]:
            behaviors = edge.get("behaviors") or {}
            top_edges.append(
                {
                    "transition": f"{edge.get('source_activity')} -> {edge.get('sink_activity')}",
                    "count": edge.get("count"),
                    "mean_hours_all": (behaviors.get("all") or {}).get("mean_hours"),
                    "dominant_behaviors": {
                        name: {
                            "percentage": stats.get("percentage"),
                            "mean_hours": stats.get("mean_hours"),
                        }
                        for name, stats in behaviors.items()
                        if name != "all" and isinstance(stats, dict)
                    },
                }
            )

        return {
            "generated_at": cached.get("generated_at"),
            "runtime_seconds": cached.get("runtime_seconds"),
            "params": cached.get("params"),
            "input": cached.get("input"),
            "graph": cached.get("graph"),
            "behavior_totals": cached.get("behavior_totals"),
            "top_transitions": top_edges,
        }

    # ── panel reads ─────────────────────────────────────────────────────────

    @route.get("/health")
    def health(self, ctx: ModuleContext, **_kw: Any) -> dict[str, Any]:
        """Sidecar reachability for the panel's setup card. Sync def -> threadpool."""
        settings = conn.resolve_settings(ctx.config.value)
        ok, detail = conn.ping(settings)
        import_dir_exists = settings.import_dir.is_dir()
        return {
            "status": "ok" if ok else detail,
            "uri": settings.uri,
            "import_dir": str(settings.import_dir),
            "import_dir_exists": import_dir_exists,
            "hint": None if ok else _setup_hint(detail, settings),
        }

    @route.get("/results")
    async def results(self, ctx: ModuleContext, **_kw: Any) -> dict[str, Any]:
        """Latest decomposition for this log, or `{status: empty}`."""
        cached = await ctx.cache.get("result")
        return cached if _result_is_current(cached) else {"status": "empty"}

    # ── the analysis run ────────────────────────────────────────────────────

    @route.post("/run")
    @job(progress=True, title="Actor performance - decompose waiting times", cancellable=True)
    async def run(self, ctx: ModuleContext, **_kw: Any) -> dict[str, Any]:
        from .pipeline import prep
        from .pipeline.run import GraphPipeline

        t0 = time.time()
        cfg = ctx.config
        settings = conn.resolve_settings(cfg.value)
        edge_min_freq = max(0, int(cfg.get("edge_min_freq", 0) or 0))
        max_edges = max(10, min(1000, int(cfg.get("max_edges", 150) or 150)))

        await ctx.progress.update(0.01, "Checking graph engine")
        ok, detail = await asyncio.to_thread(conn.ping, settings)
        if not ok:
            raise RuntimeError(_setup_hint(detail, settings))

        # The shared import dir is the CSV handoff to the server; missing it in
        # host-mode dev is normal (first run) - create rather than fail.
        def _probe_import_dir() -> None:
            settings.import_dir.mkdir(parents=True, exist_ok=True)
            probe = settings.import_dir / f".mate-probe-{uuid.uuid4().hex[:8]}"
            probe.write_text("")
            probe.unlink()

        try:
            await asyncio.to_thread(_probe_import_dir)
        except OSError as exc:
            raise RuntimeError(
                f"The shared import directory {settings.import_dir} is not writable "
                f"({exc}). It must be the same directory the Neo4j server mounts as "
                "/var/lib/neo4j/import - see DEPLOY.md §4b."
            ) from exc

        if _RUN_LOCK.locked():
            await ctx.progress.update(
                0.02, "Waiting for graph engine (another analysis is running)"
            )

        async with _RUN_LOCK:
            await ctx.check_cancelled()
            await ctx.progress.update(0.04, "Loading event log")
            async with ctx.event_log as log:
                df = await log.pandas()

            csv_dir = Path(ctx.workdir) / "input"
            csv_name = f"actor_perf_{(ctx.log_id or 'log')[:8]}_{uuid.uuid4().hex[:6]}.csv"
            stats = await asyncio.to_thread(prep.build_input_csv, df, csv_dir / csv_name)
            del df
            await ctx.progress.update(
                0.08,
                f"Prepared {stats.events} events / {stats.cases} cases / {stats.resources} actors",
            )
            if stats.events == 0:
                raise RuntimeError(
                    "No events left after dropping rows without a resource - the "
                    "analysis needs to know who performed each step."
                )

            pipeline = GraphPipeline(
                settings=settings,
                dataset_name=f"mate_{(ctx.log_id or 'log')[:8]}",
                workdir=Path(ctx.workdir),
            )
            try:
                await ctx.progress.update(0.10, "Clearing graph scratch space")
                await asyncio.to_thread(pipeline.clear)

                await ctx.check_cancelled()
                await ctx.progress.update(0.14, "Building Event Knowledge Graph")
                await asyncio.to_thread(pipeline.build_graph, csv_dir, csv_name)

                await ctx.check_cancelled()
                await ctx.progress.update(0.45, "Identifying task instances")
                await asyncio.to_thread(pipeline.build_tasks, csv_dir, csv_name)

                await ctx.check_cancelled()
                await ctx.progress.update(0.58, "Classifying actor behavior")
                await asyncio.to_thread(pipeline.classify_behavior)

                graph_stats = await asyncio.to_thread(pipeline.graph_stats)
                if not graph_stats["df_case_edges"] or not graph_stats["task_instances"]:
                    raise RuntimeError(
                        "The graph build produced no case transitions or task instances "
                        f"(stats: {graph_stats}). Check the Neo4j container logs "
                        "(docker logs mate-neo4j) - a failed batch import is the usual cause."
                    )

                await ctx.progress.update(0.62, "Listing transitions")
                edge_keys = await asyncio.to_thread(pipeline.list_edges, edge_min_freq, max_edges)

                edges: list[dict[str, Any]] = []
                for i, edge_key in enumerate(edge_keys):
                    await ctx.check_cancelled()
                    rows = await asyncio.to_thread(pipeline.extract_edge, edge_key)
                    edges.append(agg.aggregate_edge(edge_key, rows))
                    if i % 5 == 0 or i == len(edge_keys) - 1:
                        frac = 0.65 + 0.30 * ((i + 1) / max(len(edge_keys), 1))
                        await ctx.progress.update(
                            frac, f"Decomposing transitions ({i + 1}/{len(edge_keys)})"
                        )
            finally:
                # The graph is scratch space - never leave user data behind, even on
                # cancel/failure. Wipe errors must not mask the real exception.
                try:
                    await asyncio.to_thread(pipeline.wipe)
                except Exception:
                    ctx.logger.warning("actor_performance.wipe_failed")
                await asyncio.to_thread(pipeline.close)

            result: dict[str, Any] = {
                "status": "ready",
                "schema": RESULT_SCHEMA,
                "generated_at": _utcnow_iso(),
                "runtime_seconds": round(time.time() - t0, 1),
                "params": {
                    "edge_min_freq": edge_min_freq,
                    "max_edges": max_edges,
                    "bolt_uri": settings.uri,
                    "time_unit": "hours",
                },
                "input": stats.as_dict(),
                "graph": graph_stats,
                "behavior_totals": agg.behavior_totals(edges),
                "edges": edges,
                "truncated": len(edge_keys) >= max_edges,
            }
            await ctx.cache.set("result", result)

        try:
            await ctx.bus.emit(
                "actor_performance.analysis.completed",
                {
                    "log_id": ctx.log_id,
                    "edges": len(edges),
                    "task_instances": result["graph"]["task_instances"],
                    "runtime_seconds": result["runtime_seconds"],
                },
            )
        except Exception:
            ctx.logger.warning("actor_performance.emit_failed")

        await ctx.progress.update(1.0, "Done")
        ctx.logger.info(
            "actor_performance.run.done",
            runtime_seconds=result["runtime_seconds"],
            edges=len(edges),
            task_instances=result["graph"]["task_instances"],
        )
        return {
            "status": "ready",
            "edges": len(edges),
            "runtime_seconds": result["runtime_seconds"],
        }
