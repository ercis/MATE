from __future__ import annotations

from mate.sdk import Module, ModuleContext, job, on_event


class ChainedModule(Module):
    id = "chained_mod"

    @on_event("precompute_mod.completed")
    @job(progress=True, title="Precompute after precompute_mod")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        # Triggered by the platform's auto-emitted `precompute_mod.completed`, so
        # this job only exists once the upstream succeeded. Body is irrelevant to
        # the ordering tests - they assert on the `Job` row + log status.
        async with ctx.event_log as log:
            await log.duckdb_fetch("SELECT COUNT(*) FROM events")
