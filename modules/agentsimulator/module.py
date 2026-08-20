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
see the real signature). Every handler swallows them.
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


class AgentSimulatorModule(Module):
    id = "agentsimulator"

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
        """One representative simulated log as CSV text (download button)."""
        csv = await ctx.cache.get("download_csv")
        if not csv:
            return {"status": "empty"}
        suffix = (ctx.log_id or "log")[:8]
        return {"status": "ready", "filename": f"agentsim_simulated_{suffix}.csv", "csv": csv}

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
        await ctx.cache.set("result", result)
        download_csv = await asyncio.to_thread(adapter.to_download_csv, sim_dfs[0])
        await ctx.cache.set("download_csv", download_csv)

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
