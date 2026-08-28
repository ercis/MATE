"""AgentSimulator - multi-agent business-process simulation as a Mate module.

Runs `isolation: subprocess` (manifest): the upstream code pins numpy 1.x /
pandas 2.x, incompatible with the platform's numpy 2.x / pandas 3.x, so it needs
its own venv (built on 3.12 - the SDK requires >=3.12; the pinned deps ship
cp312 wheels). The platform spawns a worker on that venv; handlers run there and
reach the event log + cache + progress over the SDK's RPC bridge.

The heavy work happens in a child `simulate.py` process (see `adapter.py`); this
class only orchestrates: load log → run → score fidelity → cache for the panel.

Note on `**_kw`: in subprocess mode the host forwards the route stub's
``*args/**kwargs`` to the handler as ``args=None, kwargs=None`` (the worker can't
see the real signature). Every handler swallows them; `/simulated-log` also
*reads* them - `?args=<i>` is the only query-param channel into a subprocess
route, and it carries the requested run index (see `_parse_log_index`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, route

MODULE_DIR = Path(__file__).resolve().parent

# Bump whenever the shape of the cached `result` changes. `/results` rejects any
# cache stamped with an older schema so the panel shows the run prompt instead of
# half-rendering a stale result (an old cache held only `handover`, leaving the
# other four tabs blank). See `_result_is_current`.
RESULT_SCHEMA = 2

# The five distribution keys every current result carries (compute_summaries
# always returns all five). A cache missing any of them is partial/stale.
_REQUIRED_RESULT_KEYS = ("cycle_time", "arrivals", "circadian", "activities", "handover")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _result_is_current(cached: Any) -> bool:
    """True only for a cache written by this code version: right `schema` stamp
    and all five distribution keys present. Pure (no ctx) so it's unit-testable.
    """
    if not isinstance(cached, dict):
        return False
    if cached.get("schema") != RESULT_SCHEMA:
        return False
    return all(k in cached for k in _REQUIRED_RESULT_KEYS)


def _parse_log_index(kw: dict[str, Any]) -> int:
    """Requested simulated-log index from a subprocess route's query params.

    A subprocess route stub's FastAPI signature is derived from the stub's own
    ``(ctx, *args, **kwargs)``, so the only query params that reach the handler
    are the two literal passthroughs ``args`` and ``kwargs``. The panel sends the
    run index as ``?args=<i>`` and the export format as ``?kwargs=<fmt>`` (see
    ``_parse_format``). Defaults to 0, clamped to the manifest's
    ``num_simulations`` ceiling (10). Pure (no ctx) so it's unit-testable.
    """
    v = kw.get("args")
    try:
        return max(0, min(9, int(str(v).strip())))
    except (TypeError, ValueError):
        return 0


def _parse_format(kw: dict[str, Any]) -> str:
    """Requested export format from a subprocess route's query params.

    Shares the ``args``/``kwargs`` passthrough channel with the run index: the
    panel sends the format as ``?kwargs=<csv|xes>``. Anything other than ``xes``
    (a missing value, ``csv``, junk) falls back to ``csv`` so callers predating
    the format param keep receiving CSV. Pure (no ctx) so it's unit-testable.
    """
    v = kw.get("kwargs")
    if v is not None and str(v).strip().lower() == "xes":
        return "xes"
    return "csv"


class AgentSimulatorModule(Module):
    id = "agentsimulator"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting an AgentSimulator run: "
        "a multi-agent simulation trained on an event log and scored against "
        "it with distance measures (lower is better, 0 = identical) such as "
        "NGD (n-gram distance) and cycle-time / arrival-time distances. "
        "Relate the fidelity scores to the run parameters and log sizes. Only "
        "aggregate run statistics are available here - never individual "
        "simulated events."
    )
    guidance_user_prefix = "Interpret this simulation run summary:"

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        """Compact, cache-only simulation run summary for AI guidance.

        Whitelists aggregate fields from the cached ``result``: run params,
        input / simulated log sizes and per-measure fidelity scores. The
        distribution arrays, the row ``preview`` and the ``download_csv*``
        entries are deliberately excluded - the AI data wall forbids event
        rows, simulated or real. Reads only ``ctx.cache`` (restricted-context
        safe). Returns ``None`` until a current-schema run is cached.
        """
        cached = await ctx.cache.get("result")
        if not _result_is_current(cached):
            return None

        fidelity: dict[str, Any] = {}
        metrics_raw = cached.get("metrics")
        if isinstance(metrics_raw, dict):
            for measure, stats in metrics_raw.items():
                if isinstance(stats, dict):
                    fidelity[str(measure)] = {
                        k: stats.get(k) for k in ("mean", "std") if k in stats
                    }

        runs: list[dict[str, Any]] = []
        downloads = cached.get("downloads")
        if isinstance(downloads, list):
            for entry in downloads:
                if isinstance(entry, dict):
                    runs.append({k: entry.get(k) for k in ("index", "cases", "events")})

        return {
            "generated_at": cached.get("generated_at"),
            "runtime_seconds": cached.get("runtime_seconds"),
            "params": cached.get("params"),
            "input": cached.get("input"),
            "test": cached.get("test"),
            "simulation": cached.get("simulation"),
            "fidelity": fidelity,
            "simulated_runs": runs,
        }

    # ── results the panel reads ────────────────────────────────────────────

    @route.get("/results")
    async def results(self, ctx: ModuleContext, **_kw: Any) -> dict[str, Any]:
        """Latest simulation result for this log, or `{status: empty}`.

        A cache from an older schema (or one missing a distribution key) is
        treated as empty so the panel prompts for a fresh run rather than
        rendering a partial result.
        """
        cached = await ctx.cache.get("result")
        return cached if _result_is_current(cached) else {"status": "empty"}

    @route.get("/simulated-log")
    async def simulated_log(self, ctx: ModuleContext, **_kw: Any) -> dict[str, Any]:
        """One simulated log as CSV or XES text (per-run download buttons).

        The run index arrives as ``?args=<i>`` and the export format as
        ``?kwargs=<csv|xes>`` (see `_parse_log_index` / `_parse_format`); format
        defaults to ``csv`` for backward compatibility. Index 0 falls back to the
        legacy single `download_csv` key so CSV caches written before per-run
        downloads still serve their one log. XES is only served from caches
        written by the current code - older caches have no XES key -> `empty`.
        """
        idx = _parse_log_index(_kw)
        fmt = _parse_format(_kw)
        content = await ctx.cache.get(f"download_{fmt}_{idx}")
        if not content and fmt == "csv" and idx == 0:
            content = await ctx.cache.get("download_csv")
        if not content:
            return {"status": "empty"}
        suffix = (ctx.log_id or "log")[:8]
        return {
            "status": "ready",
            "index": idx,
            "format": fmt,
            "filename": f"agentsim_simulated_{suffix}_run{idx + 1}.{fmt}",
            "content": content,
        }

    # ── the simulation run ─────────────────────────────────────────────────

    @route.post("/simulate")
    @job(progress=True, title="AgentSimulator - generate logs", cancellable=True)
    async def simulate(self, ctx: ModuleContext, **_kw: Any) -> dict[str, Any]:
        import time

        from . import adapter, metrics

        t0 = time.time()
        cfg = ctx.config
        num_simulations = max(1, min(10, int(cfg.get("num_simulations", 5) or 5)))
        central = bool(cfg.get("central_orchestration", False))
        extr = bool(cfg.get("extr_delays", False))
        auto = bool(cfg.get("determine_automatically", False))
        mode = adapter.mode_name(central_orchestration=central, determine_automatically=auto)
        ctx.logger.info(
            "agentsimulator.run.start",
            num_simulations=num_simulations,
            mode=mode,
            extr_delays=extr,
            determine_automatically=auto,
        )

        await ctx.progress.update(0.02, "Loading event log")
        async with ctx.event_log as log:
            df = await log.pandas()

        run_dir = Path(ctx.workdir) / "run"
        input_csv = run_dir / "input.csv"
        run_dir.mkdir(parents=True, exist_ok=True)
        in_stats = await asyncio.to_thread(adapter.build_input_csv, df, input_csv)
        await ctx.progress.update(
            0.08, f"Prepared {in_stats['events']} events / {in_stats['cases']} cases"
        )

        async def progress_cb(done: int, total: int, stage: str) -> None:
            frac = 0.10 + 0.70 * (done / max(total, 1))
            await ctx.progress.update(min(frac, 0.80), f"{stage} ({done}/{total} logs)")

        out_dir = await adapter.run_simulate(
            module_dir=MODULE_DIR,
            input_csv=input_csv,
            run_dir=run_dir,
            num_simulations=num_simulations,
            central_orchestration=central,
            extr_delays=extr,
            determine_automatically=auto,
            progress_cb=progress_cb,
        )

        await ctx.progress.update(0.82, "Reading simulated logs")
        test_df, sim_dfs = await asyncio.to_thread(adapter.load_outputs, out_dir, num_simulations)

        await ctx.progress.update(0.86, "Building distributions")
        summaries = await asyncio.to_thread(adapter.compute_summaries, test_df, sim_dfs)

        await ctx.progress.update(0.90, "Scoring fidelity (5 measures)")
        fidelity = await asyncio.to_thread(metrics.compute_fidelity, test_df, sim_dfs)
        ngd = fidelity.get("NGD", {}).get("mean")

        result: dict[str, Any] = {
            "status": "ready",
            "schema": RESULT_SCHEMA,
            "generated_at": _utcnow_iso(),
            "runtime_seconds": round(time.time() - t0, 1),
            "params": {
                "num_simulations": num_simulations,
                "mode": mode,
                "central_orchestration": central,
                "extr_delays": extr,
                "determine_automatically": auto,
            },
            "input": in_stats,
            "metrics": fidelity,
            **summaries,
        }
        # Every simulated run is downloadable as CSV or XES
        # (`/simulated-log?args=<i>&kwargs=<fmt>`). Write both *before* the result
        # so the panel never lists a download that isn't in the cache yet.
        # `download_csv` (= run 0) predates per-run downloads and stays for old
        # caches/panels; there is no legacy single-XES key (XES is newer).
        downloads: list[dict[str, Any]] = []
        for i, sim_df in enumerate(sim_dfs):
            csv_i = await asyncio.to_thread(adapter.to_download_csv, sim_df)
            await ctx.cache.set(f"download_csv_{i}", csv_i)
            xes_i = await asyncio.to_thread(adapter.to_download_xes, sim_df)
            await ctx.cache.set(f"download_xes_{i}", xes_i)
            if i == 0:
                await ctx.cache.set("download_csv", csv_i)
            act_col = "activity_name" if "activity_name" in sim_df.columns else "activity"
            downloads.append(
                {
                    "index": i,
                    "cases": int(sim_df["case_id"].nunique()),
                    "events": int((sim_df[act_col].astype(str) != "zzz_end").sum()),
                }
            )
        result["downloads"] = downloads
        await ctx.cache.set("result", result)

        try:
            await ctx.bus.emit(
                "agentsimulator.simulation.completed",
                {
                    "log_id": ctx.log_id,
                    "num_simulations": num_simulations,
                    "mode": mode,
                    "ngd_mean": ngd,
                    "runtime_seconds": result["runtime_seconds"],
                },
            )
        except Exception:
            ctx.logger.warning("agentsimulator.emit_failed")

        await ctx.progress.update(1.0, "Done")
        ctx.logger.info(
            "agentsimulator.run.done", runtime_seconds=result["runtime_seconds"], ngd=ngd
        )
        return {
            "status": "ready",
            "ngd_mean": ngd,
            "runtime_seconds": result["runtime_seconds"],
            "num_simulations": num_simulations,
        }
