"""Shared host-side ctx.* JSON-RPC layer for worker-isolated handlers.

A handler running in a worker process (a persistent `SubprocessBridge` for
`isolation: subprocess`, or a one-shot `JobWorker` for an in-process module's
`execution: worker` jobs) reaches its real `ModuleContext` by RPC: the host
keeps the real ctx and answers each `ctx.*` call the worker makes over the
socket. Both hosts share this dispatcher so the ctx surface is defined once.

- `build_ctx_meta(ctx)` snapshots the small, immutable bits the worker can
  answer locally without a round-trip (ids, config, capability names, and the
  shared-filesystem paths `events_path` / `cases_path` / `cache.dir` + the active
  filter). Guarded: an unbound/restricted log raises on access → key omitted.
- `make_ctx_dispatcher(ctx_for, is_cancelled)` returns the `{method: handler}`
  map. Every handler is wrapped so that the moment its job is soft-cancelled the
  call raises the cancel sentinel — making each ctx touch a cooperative poll
  point the worker turns back into `Cancelled`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import pickle
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Sentinel string carried by the RPC error the host raises for every ctx call
# made by a soft-cancelled job. The worker (`subprocess_worker.WireConnection`)
# recognises it and reconstructs `Cancelled` so the handler unwinds even under a
# broad `except Exception`. Must stay in sync across host + worker.
CANCEL_RPC_MSG = "__ff_job_cancelled__"


def cache_envelope(value: Any, workdir: str) -> dict[str, Any]:
    """Wrap a cache value for the JSON-RPC: JSON-native inline; anything else
    (bytes, DataFrame, ...) pickled to a shared-workdir file by path. Mirror of
    `subprocess_worker._cache_envelope` - keep the wire shape in sync. Lets a
    worker-run handler cache the same bytes/DataFrames `ResultCache` accepts
    in-process (plain JSON can't carry them)."""
    try:
        json.dumps(value)
        return {"kind": "json", "value": value}
    except (TypeError, ValueError):
        path = Path(workdir) / f"_cacheval_{uuid.uuid4().hex}.pkl"
        path.write_bytes(pickle.dumps(value))
        return {"kind": "pickle", "path": str(path)}


def cache_unenvelope(env: Any) -> Any:
    if isinstance(env, dict) and env.get("kind") == "pickle":
        return pickle.loads(Path(env["path"]).read_bytes())
    if isinstance(env, dict) and env.get("kind") == "json":
        return env["value"]
    return env


def jsonify(value: Any) -> Any:
    """Best-effort JSON-native form for a handler arg crossing the socket.
    Pydantic models dump to dicts; everything else passes through."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def registry_snapshot(ctx: Any) -> list[str]:
    """Module + capability names visible to this ctx's user, so the worker's
    synchronous ctx.registry.has() can answer locally."""
    reg = getattr(ctx, "registry", None)
    if reg is None:
        return []
    names: set[str] = set()
    if hasattr(reg, "installed_modules"):
        names.update(reg.installed_modules())
    if hasattr(reg, "visible_capabilities"):
        names.update(reg.visible_capabilities())
    return sorted(names)


def build_ctx_meta(ctx: Any) -> dict[str, Any]:
    """Immutable ctx bits the worker answers locally (no round-trip)."""
    meta: dict[str, Any] = {
        "log_id": ctx.log_id,
        "module_id": ctx.module_id,
        "workdir": str(ctx.workdir),
        "config": ctx.config.value if hasattr(ctx.config, "value") else {},
        "capabilities": registry_snapshot(ctx),
    }
    # Shared-filesystem paths + the committed filter, snapshotted so the worker's
    # event_log/cache proxies answer `events_path`/`cases_path`/`active_filter`/
    # `dir` locally. Each is guarded: an unbound or AI-restricted log raises on
    # access, leaving the key absent (the proxy then errors on use, as it should).
    el = getattr(ctx, "event_log", None)
    if el is not None:
        with contextlib.suppress(Exception):
            meta["events_path"] = str(el.events_path)
        with contextlib.suppress(Exception):
            meta["cases_path"] = str(el.cases_path)
        with contextlib.suppress(Exception):
            meta["active_filter"] = el.active_filter
    cache_dir = getattr(getattr(ctx, "cache", None), "dir", None)
    if cache_dir is not None:
        meta["cache_dir"] = str(cache_dir)
    return meta


def make_ctx_dispatcher(
    ctx_for: Callable[[str], Any],
    is_cancelled: Callable[[str], bool],
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """`{rpc_method: handler}` wiring ctx.* RPCs to the real ModuleContext.

    `ctx_for(token)` resolves the live ctx for a call; `is_cancelled(token)`
    reports whether its job has been soft-cancelled (→ the call raises the
    cancel sentinel so the handler winds down).
    """

    async def event_log_duckdb_fetch(params: dict[str, Any]) -> list[list[Any]]:
        ctx = ctx_for(params["ctx_token"])
        async with ctx.event_log as log_access:
            rows = await log_access.duckdb_fetch(params["sql"], params.get("params"))
        return [list(r) for r in rows]

    async def event_log_materialize(params: dict[str, Any]) -> str:
        # Write the (filter-applied) log to a Parquet under the per-call workdir
        # (shared filesystem) and hand the worker the path; it loads locally via
        # pandas/polars/pm4py. Rides the workdir's auto-cleanup.
        ctx = ctx_for(params["ctx_token"])
        async with ctx.event_log as log_access:
            df = await log_access.pandas()
        out = Path(ctx.workdir) / f"_eventlog_{uuid.uuid4().hex}.parquet"
        await asyncio.to_thread(df.to_parquet, str(out))
        return str(out)

    async def bus_emit(params: dict[str, Any]) -> None:
        ctx = ctx_for(params["ctx_token"])
        await ctx.bus.emit(params["topic"], params["payload"])

    async def cache_get(params: dict[str, Any]) -> Any:
        ctx = ctx_for(params["ctx_token"])
        value = await ctx.cache.get(params["key"])
        return cache_envelope(value, str(ctx.workdir))

    async def cache_set(params: dict[str, Any]) -> None:
        ctx = ctx_for(params["ctx_token"])
        await ctx.cache.set(params["key"], cache_unenvelope(params["value"]))

    async def cache_exists(params: dict[str, Any]) -> bool:
        ctx = ctx_for(params["ctx_token"])
        return await ctx.cache.exists(params["key"])

    async def cache_delete(params: dict[str, Any]) -> None:
        ctx = ctx_for(params["ctx_token"])
        await ctx.cache.delete(params["key"])

    async def registry_call(params: dict[str, Any]) -> Any:
        ctx = ctx_for(params["ctx_token"])
        return await ctx.registry.call(params["capability"], **params.get("kwargs", {}))

    async def progress_update(params: dict[str, Any]) -> None:
        ctx = ctx_for(params["ctx_token"])
        await ctx.progress.update(
            params["current"],
            params.get("message"),
            total=params.get("total"),
            stage=params.get("stage"),
        )

    async def logger_log(params: dict[str, Any]) -> None:
        # Fire-and-forget on the worker side: a log line emitted at the very
        # end of a handler can arrive after the call completed and its ctx
        # token was unregistered. A trailing log is expendable - drop it
        # instead of erroring into the worker's unawaited send task.
        ctx = ctx_for(params["ctx_token"])
        if ctx is None:
            return
        level = params.get("level", "info")
        payload = params.get("payload", {})
        event = payload.pop("event", "")
        getattr(ctx.logger, level, ctx.logger.info)(event, **payload)

    async def cancel_check(_params: dict[str, Any]) -> bool:
        # Dedicated, side-effect-free poll for ctx.check_cancelled(); the guard
        # below raises the sentinel when the job is flagged, else this is False.
        return False

    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        "ctx.event_log.duckdb_fetch": event_log_duckdb_fetch,
        "ctx.event_log.materialize": event_log_materialize,
        "ctx.bus.emit": bus_emit,
        "ctx.cache.get": cache_get,
        "ctx.cache.set": cache_set,
        "ctx.cache.exists": cache_exists,
        "ctx.cache.delete": cache_delete,
        "ctx.registry.call": registry_call,
        "ctx.progress.update": progress_update,
        "ctx.logger.log": logger_log,
        "ctx.cancel.check": cancel_check,
    }

    def guard(fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        async def wrapped(params: dict[str, Any]) -> Any:
            if is_cancelled(params.get("ctx_token", "")):
                raise RuntimeError(CANCEL_RPC_MSG)
            result = fn(params)
            if asyncio.iscoroutine(result):
                return await result
            return result

        return wrapped

    return {name: guard(fn) for name, fn in handlers.items()}
