from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from mate.sdk import Module, ModuleContext, job, route


def _to_pcomp_df(df: Any) -> Any:
    import pm4py

    df = df.copy()
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    return pm4py.format_dataframe(
        df,
        case_id="case_id",
        activity_key="activity",
        timestamp_key="timestamp",
    )


class PermutationTestRequest(BaseModel):
    other_log_id: str
    distribution_size: int = 1000
    seed: int = 42
    weighted_time_cost: bool = True


class BootstrapTestRequest(BaseModel):
    other_log_id: str
    bootstrapping_dist_size: int = 1000
    resample_size: float = 1.0
    seed: int = 42


class PcompModule(Module):
    id = "pcomp"

    async def _load_pair(
        self, ctx: ModuleContext, other_log_id: str
    ) -> tuple[Any, Any]:
        async with ctx.event_log as log:
            df_baseline = await log.pandas()
        try:
            other_access = await ctx.open_event_log(other_log_id)
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        async with other_access as other_log:
            df_other = await other_log.pandas()
        return df_baseline, df_other

    @route.post("/permutation-test")
    @job(progress=True, title="Pcomp - Permutation Test")
    async def permutation_test(
        self, ctx: ModuleContext, body: PermutationTestRequest
    ) -> dict[str, Any]:
        await ctx.progress.update(0.0, "Loading event logs")
        df_baseline, df_other = await self._load_pair(ctx, body.other_log_id)

        await ctx.progress.update(0.15, "Running permutation test")
        dist_size = body.distribution_size
        seed = body.seed
        weighted = body.weighted_time_cost
        log_id = ctx.log_id
        other_id = body.other_log_id

        def _run() -> dict[str, Any]:
            from pcomp.emd.comparators.permutation_test import (
                Timed_Levenshtein_PermutationComparator,
            )

            result = Timed_Levenshtein_PermutationComparator(
                _to_pcomp_df(df_baseline),
                _to_pcomp_df(df_other),
                distribution_size=dist_size,
                seed=seed,
                weighted_time_cost=weighted,
            ).compare()
            return {
                "kind": "pcomp_permutation_test",
                "pvalue": result.pvalue,
                "baseline_log_id": log_id,
                "other_log_id": other_id,
                "distribution_size": dist_size,
                "seed": seed,
                "weighted_time_cost": weighted,
            }

        result = await asyncio.to_thread(_run)
        await ctx.progress.update(0.95, "Caching result")
        await ctx.cache.set(f"permutation__{body.other_log_id}", result)
        await ctx.progress.update(1.0, "Done")
        return result

    @route.post("/bootstrap-test")
    @job(progress=True, title="Pcomp - Bootstrap Test")
    async def bootstrap_test(
        self, ctx: ModuleContext, body: BootstrapTestRequest
    ) -> dict[str, Any]:
        await ctx.progress.update(0.0, "Loading event logs")
        df_baseline, df_other = await self._load_pair(ctx, body.other_log_id)

        await ctx.progress.update(0.15, "Running bootstrap test")
        dist_size = body.bootstrapping_dist_size
        resample_size = body.resample_size
        seed = body.seed
        log_id = ctx.log_id
        other_id = body.other_log_id

        def _run() -> dict[str, Any]:
            from pcomp.emd.comparators.bootstrap import ControlFlowBootstrapComparator

            result = ControlFlowBootstrapComparator(
                _to_pcomp_df(df_baseline),
                _to_pcomp_df(df_other),
                bootstrapping_dist_size=dist_size,
                resample_size=resample_size,
                seed=seed,
            ).compare()
            return {
                "kind": "pcomp_bootstrap_test",
                "pvalue": result.pvalue,
                "baseline_log_id": log_id,
                "other_log_id": other_id,
                "bootstrapping_dist_size": dist_size,
                "resample_size": resample_size,
                "seed": seed,
            }

        result = await asyncio.to_thread(_run)
        await ctx.progress.update(0.95, "Caching result")
        await ctx.cache.set(f"bootstrap__{body.other_log_id}", result)
        await ctx.progress.update(1.0, "Done")
        return result

    @route.get("/results")
    async def results(
        self,
        ctx: ModuleContext,
        other_log_id: str = "",
        test: str = "permutation",
    ) -> dict[str, Any]:
        if not other_log_id:
            raise HTTPException(status_code=422, detail="other_log_id is required")
        prefix = "permutation" if test == "permutation" else "bootstrap"
        cached = await ctx.cache.get(f"{prefix}__{other_log_id}")
        if cached is None:
            raise HTTPException(
                status_code=404, detail="No result cached - run the test first."
            )
        return cached
