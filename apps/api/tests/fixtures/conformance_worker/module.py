"""Python conformance worker - the twin of the JVM fixture
(`packages/module-sdk-jvm/conformance-fixture/.../ConformanceWorker.java`).

One handler per protocol surface (modules/PROTOCOL.md); the conformance suite
(apps/api/tests/test_worker_conformance.py) drives both fixtures through a real
`SubprocessBridge` with the same assertions. Keep the handler names + shapes in
sync with the Java twin.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route


class ConformanceModule(Module):
    id = "conformance_worker"

    guidance_system_prompt = "Conformance module system prompt."
    guidance_user_prefix = "conformance:"

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"guidance": True, "log_id": ctx.log_id}

    @route.get("/echo")
    async def echo(self, ctx: ModuleContext, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "args": list(args),
            "kwargs": kwargs,
            "log_id": ctx.log_id,
            "module_id": ctx.module_id,
        }

    @route.get("/snapshot")
    async def snapshot(self, ctx: ModuleContext) -> dict[str, Any]:
        return {
            "log_id": ctx.log_id,
            "module_id": ctx.module_id,
            "workdir_exists": Path(ctx.workdir).is_dir(),
            "config": ctx.config.value,
        }

    @route.post("/cache")
    async def cache_roundtrip(self, ctx: ModuleContext, **kwargs: Any) -> dict[str, Any]:
        value = kwargs.get("value") or {"n": 1}
        await ctx.cache.set("conf_key", value)
        exists_after_set = await ctx.cache.exists("conf_key")
        got = await ctx.cache.get("conf_key")
        await ctx.cache.delete("conf_key")
        exists_after_delete = await ctx.cache.exists("conf_key")
        return {
            "got": got,
            "exists_after_set": exists_after_set,
            "exists_after_delete": exists_after_delete,
        }

    @route.get("/cache-pickle")
    async def cache_pickle(self, ctx: ModuleContext) -> Any:
        # Python CAN read pickled cache values - the suite asserts the type
        # round-trips here and that the JVM twin raises its typed error. (The
        # raw value may not be JSON, so only its type crosses back.)
        value = await ctx.cache.get("pickled")
        return {"read_type": type(value).__name__}

    @route.post("/bus")
    async def bus_emit(self, ctx: ModuleContext) -> str:
        await ctx.bus.emit("conformance.pinged", {"n": 1})
        return "ok"

    @route.post("/progress")
    async def progress_ticks(self, ctx: ModuleContext) -> str:
        await ctx.progress.update(0.25, "starting")
        await ctx.progress.update(0.5, "halfway", total=1.0, stage="mid")
        await ctx.progress.update(1.0)
        return "ok"

    @route.post("/log")
    async def log_lines(self, ctx: ModuleContext) -> str:
        ctx.logger.info("conformance_started", n=1)
        ctx.logger.warning("conformance_warned")
        # ctx.logger is fire-and-forget (a create_task around the RPC) - yield
        # so both frames hit the socket before the call result does, keeping
        # the arrival assertion deterministic.
        await asyncio.sleep(0.05)
        return "ok"

    @route.get("/duckdb")
    async def duckdb(self, ctx: ModuleContext, **kwargs: Any) -> Any:
        sql = kwargs.get("sql") or "SELECT 1, 'two'"
        async with ctx.event_log as log:
            rows = await log.duckdb_fetch(sql, None)
        return [list(r) for r in rows]

    @route.get("/materialize")
    async def materialize_info(self, ctx: ModuleContext) -> dict[str, Any]:
        df = await self._pandas(ctx)
        return {"rows": len(df)}

    @staticmethod
    async def _pandas(ctx: ModuleContext):
        async with ctx.event_log as log:
            return await log.pandas()

    @route.get("/datawall")
    async def datawall_events_path(self, ctx: ModuleContext) -> str:
        try:
            return str(ctx.event_log.events_path)
        except Exception:
            return "walled"

    @route.get("/registry")
    async def registry_visible(self, ctx: ModuleContext) -> dict[str, Any]:
        return {
            "has_discovery": ctx.registry.has("discovery"),
            "has_missing": ctx.registry.has("definitely_not_installed"),
        }

    @route.get("/big")
    async def big_result(self, ctx: ModuleContext, **kwargs: Any) -> str:
        size = int(kwargs.get("bytes") or 1_000_000)
        return "x" * max(1, size)

    @route.get("/boom")
    async def boom(self, ctx: ModuleContext) -> None:
        raise RuntimeError("boom")

    @route.get("/crash")
    async def crash(self, ctx: ModuleContext) -> None:
        os._exit(42)  # simulate a hard native crash mid-call

    @route.post("/cancel-loop")
    @job(progress=True, title="Cancel loop", cancellable=True)
    async def cancel_loop(self, ctx: ModuleContext) -> None:
        while True:
            await ctx.check_cancelled()
            await asyncio.sleep(0.025)

    @route.post("/busy-sleep")
    @job(title="Busy sleep")
    def busy_sleep(self, ctx: ModuleContext, **kwargs: Any) -> str:
        time.sleep(float(kwargs.get("seconds") or 30.0))
        return "done"

    @on_event("log.imported")
    @job(progress=True, title="Conformance precompute")
    async def precompute(self, ctx: ModuleContext, payload: Any) -> dict[str, Any]:
        await ctx.progress.update(1.0)
        return {"payload_log_id": (payload or {}).get("log_id")}
