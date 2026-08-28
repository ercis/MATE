"""Subprocess worker entry point for `isolation: subprocess` modules (§5.4).

Spawned by the host (`SubprocessHost`) with::

    python -m mate.api.modules.subprocess_worker <socket_path> <module_folder>

Connects to the host's listening Unix socket, imports the module from
``<module_folder>/module.py``, walks the resulting `Module` instance for
decorated handlers and sends a `ready` message describing them. The host
then sends `call` requests for each invocation; the worker executes the
handler in-process using a `ProxyContext` that translates every `ctx.*`
attribute access into an RPC back to the host over the same socket.

Wire protocol: line-delimited JSON, one message per line. Each message is
``{"id": int, "method": str, "params": dict}`` for requests, or
``{"id": int, "result": ...}`` / ``{"id": int, "error": {...}}`` for
responses. Both sides initiate requests; ids are local to the initiator.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import json
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

# RPC messages are newline-framed JSON read via StreamReader.readline(), whose
# asyncio default buffer limit is 64 KiB. Handler return values, ctx.cache.set
# payloads and duckdb_fetch rows routinely exceed that (a single >64 KiB line
# raises LimitOverrunError and tears the connection down, killing the worker), so
# raise it well beyond. DataFrames still cross via a Parquet file, not the
# socket, so this only bounds JSON metadata.
RPC_STREAM_LIMIT = 256 * 1024 * 1024  # 256 MiB

# Must match `subprocess_host.CANCEL_RPC_MSG`. The host raises an RPC error
# carrying this string for every ctx call made by a soft-cancelled job; the
# worker turns it into `Cancelled` (below) so the handler unwinds cleanly.
_CANCEL_RPC_MSG = "__ff_job_cancelled__"

# `asyncio.create_task` only weakly references its task, so a fire-and-forget
# task can be collected mid-flight and never finish - dropping a log line or a
# whole inbound RPC dispatch. This file runs on the *module's* interpreter and
# must stay stdlib-only (no `mate.api` imports), so it keeps its own reference
# set rather than using `mate.api.tasks.spawn`.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> None:
    """Schedule *coro* and hold a strong reference until it completes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _resolve_cancelled() -> type[BaseException]:
    """The `Cancelled` type to raise for a soft cancel.

    Prefer the SDK's `mate.sdk.Cancelled` (new workers). Fall back to a local
    BaseException subclass so an *older* worker SDK - which predates `Cancelled`
    - still unwinds under a broad `except Exception` (BaseException isn't caught
    by it). Version-skew-proof either way.
    """
    try:
        from mate.sdk import Cancelled as _Cancelled  # type: ignore[attr-defined]

        return _Cancelled
    except Exception:  # pragma: no cover - exercised only against an old SDK

        class Cancelled(BaseException):
            pass

        return Cancelled


Cancelled = _resolve_cancelled()


def _cache_envelope(value: Any, workdir: str) -> dict[str, Any]:
    """Wrap a cache value for the JSON-RPC: JSON-native passes inline; anything
    else (bytes, DataFrame, numpy, ...) is pickled to a file under the shared
    workdir and referenced by path - the host reads it back. Mirrors
    `ctx_rpc._cache_envelope`; the two MUST agree on the wire shape.

    `ResultCache` stores bytes/DataFrames/JSON by type in-process, so a handler
    moved to a worker must be able to cache the same values - plain JSON can't
    carry them. The pickle only ever crosses between the platform's own host and
    worker (same trust domain as the `run_in_process` offload pickle)."""
    try:
        json.dumps(value)
        return {"kind": "json", "value": value}
    except (TypeError, ValueError):
        import pickle
        import uuid

        path = Path(workdir) / f"_cacheval_{uuid.uuid4().hex}.pkl"
        path.write_bytes(pickle.dumps(value))
        return {"kind": "pickle", "path": str(path)}


def _cache_unenvelope(env: Any) -> Any:
    if isinstance(env, dict) and env.get("kind") == "pickle":
        import pickle

        return pickle.loads(Path(env["path"]).read_bytes())
    if isinstance(env, dict) and env.get("kind") == "json":
        return env["value"]
    return env


def _install_parent_death_guard() -> None:
    """Self-terminate (with our whole process group) when the API parent dies.

    Inlined, stdlib-only equivalent of ``mate.api.jobs.proc_guard`` - the worker
    runs on the *module's* venv where ``mate.api`` is not importable. The host
    spawns us with ``start_new_session=True``, so we lead our own group
    (``getpgrp() == getpid()``) and the API is our direct parent: ``getppid()``
    polling detects its death reliably, and a group-kill reaps any grandchildren
    the handler spawned (e.g. AgentSimulator's ``ProcessPoolExecutor``). Linux
    ``PR_SET_PDEATHSIG`` is the instant fast path on top. Without this, a worker
    wedged in a native ``to_thread`` call would keep burning a core after a hard
    API death (SIGKILL / ``--reload`` restart / crash), since the socket-EOF
    self-exit is delayed by ``asyncio.run`` joining that thread. Keep in sync
    with ``proc_guard.install_parent_death_guard``.
    """

    def _kill_group_and_exit() -> None:
        try:
            if os.getpgrp() == os.getpid():
                os.killpg(0, signal.SIGKILL)
        except Exception:
            pass
        os._exit(137)

    # Linux: kernel SIGKILLs us when the parent dies. No-op / best-effort else.
    if sys.platform == "linux":
        try:
            import ctypes

            ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)
        except Exception:
            pass

    original_ppid = os.getppid()
    if original_ppid == 1:  # parent already gone (race between spawn and now)
        _kill_group_and_exit()

    def _watch() -> None:
        while True:
            try:
                if os.getppid() != original_ppid:
                    break
            except Exception:
                break
            time.sleep(0.5)
        _kill_group_and_exit()

    threading.Thread(target=_watch, daemon=True, name="ff-parent-death").start()


def _import_module(folder: Path):
    """Import ``<folder>/module.py`` as a package so relative imports work."""
    py_path = folder / "module.py"
    if not py_path.exists():
        raise FileNotFoundError(f"Missing module.py at {py_path}")
    ns = f"_ff_subprocess_mod_{folder.name}"
    spec = importlib.util.spec_from_file_location(
        ns, py_path, submodule_search_locations=[str(folder)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {py_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ns] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_module_class(mod):
    from mate.sdk import Module

    for value in mod.__dict__.values():
        if (
            inspect.isclass(value)
            and issubclass(value, Module)
            and value is not Module
            and value.__module__ == mod.__name__
        ):
            return value
    raise RuntimeError(f"No Module subclass in {mod.__name__}")


def _collect_handlers(instance) -> list[dict[str, Any]]:
    """Walk the instance for route/event/job-decorated methods."""
    from mate.sdk.decorators import get_event_sub, get_job_spec, get_route_spec

    out: list[dict[str, Any]] = []
    for attr_name in dir(instance):
        unbound = getattr(type(instance), attr_name, None)
        route_spec = get_route_spec(unbound)
        event_sub = get_event_sub(unbound)
        job_spec = get_job_spec(unbound)
        if not (route_spec or event_sub or job_spec):
            continue
        entry: dict[str, Any] = {"attr": attr_name}
        if route_spec is not None:
            entry["route"] = {
                "method": route_spec.method,
                "path": route_spec.path,
                "name": route_spec.name,
            }
        if event_sub is not None:
            entry["on_event"] = {"topic": event_sub.topic}
        if job_spec is not None:
            # Serialize the static JobSpec fields so the host can rebuild it.
            # Callable title/subtitle can't cross the socket - leave them null
            # so the host falls back to a static label (loader's
            # `_resolve_dynamic`).
            entry["job"] = {
                "progress": job_spec.progress,
                "priority": job_spec.priority,
                "cancellable": job_spec.cancellable,
                "result_url": job_spec.result_url,
                "title": job_spec.title if isinstance(job_spec.title, str) else None,
                "subtitle": job_spec.subtitle if isinstance(job_spec.subtitle, str) else None,
            }
        out.append(entry)
    return out


class _ProxyContext:
    """ctx.* surface forwarded to the host via JSON-RPC.

    DataFrame views (`pandas`/`polars`/`pm4py`) are materialised by the host to
    a Parquet file under the shared workdir and loaded here, so heavy
    process-mining jobs work the same as in_process.
    """

    def __init__(self, conn: WireConnection, token: str, ctx_meta: dict[str, Any]):
        self._conn = conn
        self._token = token
        self.log_id: str = ctx_meta.get("log_id", "")
        self.module_id: str = ctx_meta.get("module_id", "")
        self.workdir = Path(ctx_meta.get("workdir", "/tmp"))
        self.event_log = _EventLogProxy(conn, token, ctx_meta)
        self.bus = _BusProxy(conn, token)
        self.registry = _RegistryProxy(conn, token, ctx_meta.get("capabilities"))
        self.cache = _CacheProxy(conn, token, ctx_meta)
        self.config = _ConfigProxy(ctx_meta.get("config", {}))
        self.progress = _ProgressProxy(conn, token)
        self.logger = _LoggerProxy(conn, token)
        self.cancellation = _CancellationProxy(conn, token)

    def is_cancelled(self) -> bool:
        return self.cancellation.is_cancelled()

    async def check_cancelled(self) -> None:
        await self.cancellation.check_cancelled()

    async def run_in_process(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        # We are already the isolated, killable process; just run it (off the
        # event loop so an async handler stays responsive to ctx RPCs). No
        # further child-process offload - the worker itself is the kill target.
        return await asyncio.to_thread(fn, *args, **kwargs)


class _EventLogProxy:
    def __init__(self, conn: WireConnection, token: str, ctx_meta: dict[str, Any] | None = None):
        self._conn = conn
        self._token = token
        self._meta = ctx_meta or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    # Shared-filesystem paths + committed filter, snapshotted host-side into
    # ctx_meta so these answer locally (no round-trip), matching the real
    # EventLogAccess surface. Absent key → not log-scoped → raise on use.
    @property
    def events_path(self) -> Path:
        p = self._meta.get("events_path")
        if p is None:
            raise RuntimeError("ctx.event_log.events_path is unavailable (handler not log-scoped).")
        return Path(p)

    @property
    def cases_path(self) -> Path:
        p = self._meta.get("cases_path")
        if p is None:
            raise RuntimeError("ctx.event_log.cases_path is unavailable (handler not log-scoped).")
        return Path(p)

    @property
    def active_filter(self) -> list | None:
        return self._meta.get("active_filter")

    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        result = await self._conn.send_request(
            "ctx.event_log.duckdb_fetch",
            {"ctx_token": self._token, "sql": sql, "params": list(params or [])},
        )
        return [tuple(row) for row in result]

    async def _materialize(self) -> str:
        """Ask the host to write the (filter-applied) log to a Parquet under the
        shared workdir; returns the path. Host and worker share the filesystem."""
        return await self._conn.send_request(
            "ctx.event_log.materialize", {"ctx_token": self._token}
        )

    async def pandas(self):
        import pandas as pd

        path = await self._materialize()
        return await asyncio.to_thread(pd.read_parquet, path)

    async def polars(self):
        import polars as pl

        path = await self._materialize()
        return await asyncio.to_thread(pl.read_parquet, path)

    async def pm4py(self):
        import pandas as pd

        path = await self._materialize()

        def _convert():
            import pm4py.utils as pmu

            df = pd.read_parquet(path).rename(
                columns={
                    "case_id": "case:concept:name",
                    "activity": "concept:name",
                    "timestamp": "time:timestamp",
                }
            )
            return pmu.format_dataframe(df)

        return await asyncio.to_thread(_convert)


class _BusProxy:
    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    async def emit(self, topic: str, payload: Any) -> None:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif not isinstance(payload, dict):
            payload = {"value": payload}
        await self._conn.send_request(
            "ctx.bus.emit", {"ctx_token": self._token, "topic": topic, "payload": payload}
        )


class _RegistryProxy:
    def __init__(self, conn: WireConnection, token: str, capabilities: list[str] | None = None):
        self._conn = conn
        self._token = token
        # The host snapshots the available capability names into ctx_meta at
        # call time so `has()` (sync per the SDK Protocol) answers locally
        # without a round-trip.
        self._caps = frozenset(capabilities or ())

    def has(self, cap: str) -> bool:
        return cap in self._caps

    async def call(self, capability: str, **kwargs: Any) -> Any:
        return await self._conn.send_request(
            "ctx.registry.call",
            {"ctx_token": self._token, "capability": capability, "kwargs": kwargs},
        )


class _CacheProxy:
    def __init__(self, conn: WireConnection, token: str, ctx_meta: dict[str, Any] | None = None):
        self._conn = conn
        self._token = token
        self._meta = ctx_meta or {}

    @property
    def dir(self) -> Path:
        # The host created this dir when it built the ctx and shares the
        # filesystem, so the worker writes/reads here directly. Files land in the
        # real cache dir; a subsequent ctx.cache.set RPC persists the whole dir
        # to the storage backend (host-side ResultCache.set) - same semantics as
        # an in-thread handler.
        d = self._meta.get("cache_dir")
        if d is None:
            raise RuntimeError("ctx.cache.dir is unavailable (handler not log-scoped).")
        return Path(d)

    async def get(self, key: str) -> Any:
        env = await self._conn.send_request("ctx.cache.get", {"ctx_token": self._token, "key": key})
        return _cache_unenvelope(env)

    async def set(self, key: str, value: Any) -> None:
        envelope = _cache_envelope(value, self._meta.get("workdir", "/tmp"))
        await self._conn.send_request(
            "ctx.cache.set", {"ctx_token": self._token, "key": key, "value": envelope}
        )

    async def exists(self, key: str) -> bool:
        return bool(
            await self._conn.send_request(
                "ctx.cache.exists", {"ctx_token": self._token, "key": key}
            )
        )

    async def delete(self, key: str) -> None:
        await self._conn.send_request("ctx.cache.delete", {"ctx_token": self._token, "key": key})


class _ConfigProxy:
    def __init__(self, value: dict[str, Any]):
        self._value = dict(value)

    @property
    def value(self) -> dict[str, Any]:
        return dict(self._value)

    def get(self, key: str, default: Any = None) -> Any:
        return self._value.get(key, default)


class _ProgressProxy:
    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    async def update(
        self,
        current: float,
        message: str | None = None,
        *,
        total: float | None = None,
        stage: str | None = None,
    ) -> None:
        await self._conn.send_request(
            "ctx.progress.update",
            {
                "ctx_token": self._token,
                "current": current,
                "total": total,
                "stage": stage,
                "message": message,
            },
        )


class _CancellationProxy:
    """ctx.cancellation surface for a subprocess handler (new-SDK workers).

    `check_cancelled()` makes a dedicated `ctx.cancel.check` RPC; the host's
    cancel guard turns it into the cancel sentinel when the job is flagged, which
    `WireConnection.run` reconstructs as `Cancelled` - so the call raises. When
    not cancelled the RPC just returns False. `is_cancelled()` is best-effort
    sync: it can't round-trip, so it reports False and authors should prefer the
    async `check_cancelled()` (or simply report progress, which also polls).
    """

    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    def is_cancelled(self) -> bool:
        return False

    async def check_cancelled(self) -> None:
        await self._conn.send_request("ctx.cancel.check", {"ctx_token": self._token})


class _LoggerProxy:
    """Minimal structlog-compatible logger that forwards to the host."""

    def __init__(self, conn: WireConnection, token: str, bound: dict[str, Any] | None = None):
        self._conn = conn
        self._token = token
        self._bound = dict(bound or {})

    def bind(self, **kwargs: Any) -> _LoggerProxy:
        merged = {**self._bound, **kwargs}
        return _LoggerProxy(self._conn, self._token, merged)

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        payload = {**self._bound, **kwargs, "event": event}
        _spawn(
            self._conn.send_request(
                "ctx.logger.log",
                {"ctx_token": self._token, "level": level, "payload": payload},
            )
        )

    def info(self, event: str, **kw: Any) -> None:
        self._log("info", event, **kw)

    def debug(self, event: str, **kw: Any) -> None:
        self._log("debug", event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._log("warning", event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._log("error", event, **kw)

    def exception(self, event: str, **kw: Any) -> None:
        self._log("error", event, exc_info=True, **kw)


class WireConnection:
    """Bidirectional line-delimited JSON-RPC over a stream pair."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._dispatcher: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        # Serialise the full write+drain so two coroutines (e.g. a dispatched
        # reply and an outbound request fired the same tick) can't interleave
        # their bytes on the wire and corrupt a JSON frame.
        self._write_lock = asyncio.Lock()

    def register(self, method: str, fn) -> None:
        self._dispatcher[method] = fn

    def fail_all_pending(self, exc: BaseException) -> None:
        """Reject every in-flight outbound request with `exc`.

        Called when the peer process dies (e.g. the host SIGKILLs the worker
        to cancel a job): without this, the futures returned by `send_request`
        never resolve and their awaiting tasks hang forever. Idempotent.
        """
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
                # By the time the peer dies during shutdown, the task that was
                # awaiting this future is usually already cancelled - nobody
                # ever retrieves the exception and asyncio logs a bare "Future
                # exception was never retrieved" at GC. Reading it here clears
                # that flag; a task still awaiting the future gets the
                # exception raised as normal.
                fut.exception()
        self._pending.clear()

    async def send_request(self, method: str, params: dict[str, Any]) -> Any:
        async with self._lock:
            rid = self._next_id
            self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write({"id": rid, "method": method, "params": params})
        return await fut

    async def _write(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg).encode("utf-8") + b"\n"
        async with self._write_lock:
            self._writer.write(line)
            await self._writer.drain()

    async def run(self) -> None:
        while not self._reader.at_eof():
            line = await self._reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if "method" in msg:
                _spawn(self._dispatch(msg))
            else:
                rid = msg.get("id")
                fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    if "error" in msg:
                        message = msg["error"].get("message", "remote error")
                        # The host signals a soft cancel via an RPC error carrying
                        # the cancel sentinel - turn it into `Cancelled` (a
                        # BaseException) so the awaiting handler unwinds even under
                        # a broad `except Exception`. Other errors stay RuntimeError.
                        if _CANCEL_RPC_MSG in message:
                            fut.set_exception(Cancelled())
                        else:
                            fut.set_exception(RuntimeError(message))
                    else:
                        fut.set_result(msg.get("result"))

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        method = msg["method"]
        params = msg.get("params", {})
        fn = self._dispatcher.get(method)
        if fn is None:
            await self._write({"id": rid, "error": {"message": f"unknown method {method!r}"}})
            return
        try:
            result = fn(params)
            if inspect.isawaitable(result):
                result = await result
            await self._write({"id": rid, "result": result})
        except Cancelled:
            # A handler unwound on a soft cancel (it raised Cancelled, or a ctx
            # RPC reconstructed one). Report it back carrying the cancel sentinel
            # so the host re-raises Cancelled too - the job records `cancelled`,
            # not `failed`. (Cancelled is a BaseException, so the generic
            # `except Exception` below would otherwise miss it and the host's
            # `call` future would hang.)
            await self._write({"id": rid, "error": {"message": _CANCEL_RPC_MSG}})
        except Exception as exc:
            await self._write(
                {
                    "id": rid,
                    "error": {
                        "message": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    },
                }
            )


async def _amain(socket_path: str, module_folder: str) -> int:
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=RPC_STREAM_LIMIT)
    conn = WireConnection(reader, writer)

    mod = _import_module(Path(module_folder))
    module_class = _find_module_class(mod)
    instance = module_class()
    handlers_meta = _collect_handlers(instance)

    async def handle_call(params: dict[str, Any]) -> Any:
        attr = params["handler"]
        ctx_token = params["ctx_token"]
        ctx_meta = params.get("ctx", {})
        args = params.get("args", []) or []
        kwargs = params.get("kwargs", {}) or {}
        bound = getattr(instance, attr)
        ctx = _ProxyContext(conn, ctx_token, ctx_meta)
        if inspect.iscoroutinefunction(bound):
            return await bound(ctx, *args, **kwargs)
        return await asyncio.to_thread(bound, ctx, *args, **kwargs)

    conn.register("call", handle_call)
    conn.register("shutdown", lambda _params: True)
    conn.register("ping", lambda _params: True)

    # Advertise duck-typed guidance support so the host shim only exposes a
    # `guidance_payload` stub for modules that actually implement one (the
    # AI/MCP path probes the instance with getattr, not the handler walk).
    guidance_meta = None
    if callable(getattr(instance, "guidance_payload", None)):
        guidance_meta = {
            "system_prompt": getattr(instance, "guidance_system_prompt", None),
            "user_prefix": getattr(instance, "guidance_user_prefix", None),
        }
    await conn._write(
        {
            "id": None,
            "method": "ready",
            # `protocol` versions the wire contract (modules/PROTOCOL.md §1);
            # the host rejects workers speaking a newer protocol than it knows.
            "params": {"protocol": 1, "handlers": handlers_meta, "guidance": guidance_meta},
        }
    )
    await conn.run()
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return 0


def main() -> None:
    if len(sys.argv) not in (3, 4):
        print(
            "usage: subprocess_worker.py <socket_path> <module_folder> [site_packages]",
            file=sys.stderr,
        )
        sys.exit(2)
    socket_path = sys.argv[1]
    module_folder = sys.argv[2]
    # `isolation: subprocess` runs under the module's own venv python (3 args).
    # An in-process module's `execution: worker` job runs under the *platform*
    # python (so inherited platform deps resolve) and passes its venv
    # site-packages (4th arg) so the module's own deps import too - the same
    # layered-path model as `ctx.run_in_process` offload.
    site_packages = sys.argv[3] if len(sys.argv) == 4 else None
    # Die with the platform: no worker may outlive a hard API death as an orphan.
    _install_parent_death_guard()
    if site_packages and os.path.isdir(site_packages):
        sys.path.insert(0, site_packages)
    # Make the module folder importable for relative `from .x import y`.
    sys.path.insert(0, module_folder)
    raise SystemExit(asyncio.run(_amain(socket_path, module_folder)))


if __name__ == "__main__":  # pragma: no cover - executed by spawned subprocess
    main()
