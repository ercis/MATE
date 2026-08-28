from __future__ import annotations

from mate.sdk import Module, ModuleContext, on_event, route

_received: list[dict] = []
_last_called_path: list[str] = []


def get_received() -> list[dict]:
    return list(_received)


def get_calls() -> list[str]:
    return list(_last_called_path)


class SampleModule(Module):
    id = "sample_mod"

    @route.get("/ping")
    async def ping(self, ctx: ModuleContext) -> dict[str, str]:
        _last_called_path.append("ping")
        return {"module_id": ctx.module_id, "status": "pong"}

    @route.get("/count")
    async def count(self, ctx: ModuleContext) -> dict[str, int]:
        """Row count of the (filter-aware) events view - lets tests prove a
        per-request dashboard filter narrows what a module sees."""
        async with ctx.event_log as log:
            rows = await log.duckdb_fetch("SELECT COUNT(*) FROM events")
        return {"events": int(rows[0][0])}

    @route.get("/cached-count")
    async def cached_count(self, ctx: ModuleContext) -> dict[str, int]:
        """Like /count but memoised in the result cache - proves an ephemeral
        dashboard filter gets its own cache namespace instead of being served
        the first (unfiltered) cached result."""
        cached = await ctx.cache.get("count")
        if cached is not None:
            return {"events": int(cached["events"])}
        async with ctx.event_log as log:
            rows = await log.duckdb_fetch("SELECT COUNT(*) FROM events")
        result = {"events": int(rows[0][0])}
        await ctx.cache.set("count", result)
        return result

    @route.get("/open-other")
    async def open_other(
        self, ctx: ModuleContext, other_id: str, only_activity: str = "", raw: bool = False
    ) -> dict[str, object]:
        """Open a *second* log via the ownership-checked cross-log accessor and
        return its event count. Returns ``{"denied": True}`` when the accessor
        refuses (missing log or another tenant's) - lets a test prove the
        tenant-isolation invariant on ``ctx.open_event_log``.

        ``only_activity`` / ``raw`` exercise the per-call filter override: an
        explicit list replaces the log's committed Events-tab filter for that
        view (``raw`` sends ``[]`` - no filter at all), which is what lets one
        module read two differently filtered views of the same log."""
        filters: list[dict[str, object]] | None = None
        if only_activity:
            filters = [{"field": "activity", "op": "equals", "value": only_activity}]
        elif raw:
            filters = []
        try:
            other = await ctx.open_event_log(other_id, filters)
        except PermissionError:
            return {"denied": True}
        async with other as log:
            rows = await log.duckdb_fetch("SELECT COUNT(*) FROM events")
        return {"events": int(rows[0][0])}

    @on_event("test.shout")
    async def on_shout(self, ctx: ModuleContext, payload: dict) -> None:
        _received.append(payload)
