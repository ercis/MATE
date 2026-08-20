from __future__ import annotations

from mate.sdk import Module, ModuleContext, job, on_event


class PrecomputeModule(Module):
    id = "precompute_mod"

    @on_event("log.imported")
    @job(progress=True, title="Precompute on import")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        # The body is irrelevant to the processing-lifecycle tests - they assert
        # on the resulting `Job` row + log status, not on any output. Touch the
        # event log so the job does real (cheap) work and the import-time
        # availability gate (min_events/min_cases) is exercised.
        async with ctx.event_log as log:
            await log.duckdb_fetch("SELECT COUNT(*) FROM events")
