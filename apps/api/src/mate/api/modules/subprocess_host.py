"""Host-side wrapper for subprocess-isolated modules (§5.4).

Spawns `subprocess_worker.py` inside the module's `.venv`, listens on a
Unix socket for the worker's connection, and exposes a `SubprocessModule`
object that mimics the in-process `Module` instance: each handler is a
sync stub that the loader picks up via the same `_collect_handlers`
machinery, but calling it routes through JSON-RPC to the worker.

When the worker runs the handler, every `ctx.*` call comes back over the
same socket as a request; this host dispatches them against a registered
real `ModuleContext` looked up by `ctx_token`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from mate.api.modules.subprocess_worker import RPC_STREAM_LIMIT, WireConnection
from mate.sdk.decorators import (
    _ATTR_JOB,
    _ATTR_ON_EVENT,
    _ATTR_ROUTE,
    EventSubscription,
    JobSpec,
    RouteSpec,
)
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)

# Sentinel message carried by the RPC error the host raises for every ctx call
# made by a soft-cancelled job. The worker recognises it (see
# `subprocess_worker.WireConnection.run`) and rejects the pending future with
# `Cancelled` instead of a plain `RuntimeError`, so the handler unwinds even
# under a broad `except Exception`. Must stay in sync across host + worker.
CANCEL_RPC_MSG = "__ff_job_cancelled__"


class SubprocessHostError(RuntimeError):
    pass


class SubprocessModule:
    """Stand-in for the actual `Module` instance - the worker holds the real
    one. We synthesise stub methods carrying the same decorator metadata so
    the loader's `_collect_handlers` walk picks them up untouched.

    Intentionally NOT a subclass of `mate.sdk.Module`: the SDK's
    `__init_subclass__` validates `id` at class-definition time, which is
    fine for module authors but pointless for this duck-typed shim. The
    loader's `_bind` only reads `dir(instance)` + callable attrs + decorator
    metadata via `getattr(type(instance), name, None)`, so duck typing is
    enough.
    """

    def __init__(
        self, manifest_id: str, handlers_meta: list[dict[str, Any]], bridge: SubprocessBridge
    ) -> None:
        self.id = manifest_id
        self._bridge = bridge
        self._handlers_meta = handlers_meta
        self._install_stubs()

    def _install_stubs(self) -> None:
        for entry in self._handlers_meta:
            attr = entry["attr"]
            stub = self._make_handler_stub(attr)
            installed = False
            route_meta = entry.get("route")
            if route_meta:
                setattr(
                    stub,
                    _ATTR_ROUTE,
                    RouteSpec(
                        method=route_meta["method"],
                        path=route_meta["path"],
                        name=route_meta.get("name"),
                    ),
                )
                installed = True
            event_meta = entry.get("on_event")
            if event_meta:
                setattr(stub, _ATTR_ON_EVENT, EventSubscription(topic=event_meta["topic"]))
                installed = True
            job_meta = entry.get("job")
            if job_meta:
                # title/subtitle are static strings or None; a None means the
                # author used a callable (which can't cross the socket), so the
                # loader falls back to its default label via `_resolve_dynamic`.
                setattr(
                    stub,
                    _ATTR_JOB,
                    JobSpec(
                        progress=job_meta.get("progress", False),
                        title=job_meta.get("title"),
                        subtitle=job_meta.get("subtitle"),
                        priority=job_meta.get("priority", 0),
                        cancellable=job_meta.get("cancellable", True),
                        result_url=job_meta.get("result_url"),
                    ),
                )
                installed = True
            if not installed:
                continue
            # Bind on the instance AND the type so the loader's `_bind` walk
            # (which reads decorator metadata off `type(instance)`) picks them
            # up exactly like an in_process module.
            setattr(self, attr, stub)
            setattr(type(self), attr, stub)

    def _make_handler_stub(self, attr: str):
        """One stub for @route/@job/@on_event alike - forwards the call (with
        any positional payload + kwargs) to the worker over the bridge."""
        bridge = self._bridge

        # The stub is stored on the *instance* dict (see `_install_stubs`), so
        # attribute access returns it **unbound** - Python does not strip a
        # leading `self`. Its first parameter must therefore be `ctx`, matching
        # what the loader's `_extra_handler_params` drops. A leading `_self` here
        # shifts everything by one, leaking `ctx` into the forwarded request
        # params and raising "got multiple values for argument 'ctx'" on call.
        async def stub(ctx, *args, **kwargs):
            return await bridge.call_handler(attr, ctx, args, kwargs)

        stub.__name__ = attr
        stub.__qualname__ = f"SubprocessModule.{attr}"
        return stub


class SubprocessBridge:
    """Owns the worker process + socket for one module."""

    def __init__(self, manifest: Manifest, folder: Path) -> None:
        self.manifest = manifest
        self.folder = folder
        self._server: asyncio.base_events.Server | None = None
        self._conn: WireConnection | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._socket_dir = Path(tempfile.mkdtemp(prefix=f"ff-sock-{manifest.id}-"))
        self._socket_path = self._socket_dir / "rpc.sock"
        self._ready_evt = asyncio.Event()
        self._handlers_meta: list[dict[str, Any]] = []
        self._ctx_registry: dict[str, Any] = {}
        # Soft-cancel bookkeeping. `_cancelled_job_ids` holds jobs asked to wind
        # down; `_token_job` maps a per-call RPC token → its job id so a ctx RPC
        # can tell whether *its* job is cancelled. Cleared when a job ends so a
        # reused worker isn't poisoned by a stale flag.
        self._cancelled_job_ids: set[str] = set()
        self._token_job: dict[str, str] = {}
        # Set once `stop()` is called so a concurrent cancel-triggered respawn
        # doesn't resurrect the worker during teardown.
        self._stopping = False
        # Hold the respawn task so it isn't garbage-collected mid-flight.
        self._respawn_task: asyncio.Task[None] | None = None

    def worker_pid(self) -> int | None:
        """PID of the live worker process, or None if not running/exited.

        Read-only - used by the admin resource sampler to attribute measured
        CPU/RAM to this module's subprocess worker.
        """
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return None
        return proc.pid

    async def start(self) -> SubprocessModule:
        # Spawn the worker, wait for its `ready` (which carries the handler
        # list), then hand back a SubprocessModule whose stubs the loader binds
        # like any in_process module - @route, @job and @on_event all work.
        self._server = await asyncio.start_unix_server(
            self._on_connect, path=str(self._socket_path), limit=RPC_STREAM_LIMIT
        )
        os.chmod(self._socket_path, 0o600)

        await self._spawn_worker()
        try:
            await asyncio.wait_for(self._ready_evt.wait(), timeout=30.0)
        except TimeoutError as exc:
            await self.stop()
            raise SubprocessHostError(
                f"Subprocess module {self.manifest.id!r} did not signal ready in 30s."
            ) from exc

        return SubprocessModule(self.manifest.id, self._handlers_meta, self)

    async def _spawn_worker(self) -> None:
        """Spawn (or respawn) the worker process against the live socket.

        `start_new_session=True` puts the worker in its own process group, so
        `cancel_active()` can `killpg` the whole subtree - the worker *and* any
        grandchildren it forked - without touching the API process group.
        """
        worker_py = _worker_python(self.folder)
        # Run the worker by file path (not `-m`) so we don't import the whole
        # `mate.api` package chain under the module's venv Python - the worker
        # only needs `mate.sdk`, which the installer installs into the venv.
        worker_script = Path(__file__).with_name("subprocess_worker.py")
        cmd = [
            str(worker_py),
            str(worker_script),
            str(self._socket_path),
            str(self.folder),
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=True,
        )

        # Pipe worker stderr to our log so author tracebacks aren't lost.
        asyncio.create_task(self._drain_pipe(self._proc.stderr, "stderr"))
        asyncio.create_task(self._drain_pipe(self._proc.stdout, "stdout"))

    async def cancel_active(self) -> None:
        """Hard-stop whatever the worker is running by killing its process
        group, then respawn a fresh worker.

        Subprocess handlers - especially native/threaded ones like
        AgentSimulator's pm4py/Mesa pipeline, which runs via
        `asyncio.to_thread` with no poll point - cannot be cancelled
        cooperatively: a Python thread can't be interrupted and the upstream
        call never yields. Killing the OS process is the only reliable stop.

        Collateral: there is one shared worker per module, so any *other* call
        in flight on it dies too. Those host-side awaits are failed with a
        clear, retryable error rather than left hanging. Heavy subprocess runs
        are exclusive in practice, so overlap is rare.
        """
        if self._stopping:
            return
        self._kill_worker_group()
        if self._conn is not None:
            self._conn.fail_all_pending(
                SubprocessHostError(
                    f"Worker for {self.manifest.id!r} was restarted to cancel a running job."
                )
            )
        # Respawn off the request path so cancel returns immediately; new calls
        # block on `_ready_evt` (see `call_handler`) until the worker is back.
        self._ready_evt.clear()
        self._respawn_task = asyncio.create_task(self._respawn())

    async def _respawn(self) -> None:
        if self._stopping:
            return
        try:
            await self._spawn_worker()
            await asyncio.wait_for(self._ready_evt.wait(), timeout=30.0)
            log.info("modules.subprocess.worker_restarted", module_id=self.manifest.id)
        except Exception:
            log.exception("modules.subprocess.worker_restart_failed", module_id=self.manifest.id)

    def _kill_worker_group(self) -> None:
        """SIGKILL the worker's whole process group. SIGKILL (not TERM) because
        a thread deep in a native numpy/pm4py call won't service a handler in
        time - only an unconditional kill guarantees the CPU stops now."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def stop(self) -> None:
        # Block any in-flight `cancel_active()` respawn from resurrecting the
        # worker mid-teardown.
        self._stopping = True
        if self._conn is not None:
            try:
                await asyncio.wait_for(self._conn.send_request("shutdown", {}), timeout=2.0)
            except Exception:
                pass
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                self._kill_worker_group()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        shutil.rmtree(self._socket_dir, ignore_errors=True)

    async def _drain_pipe(self, stream, label: str) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            log.info(
                "modules.subprocess.worker_output",
                module_id=self.manifest.id,
                stream=label,
                line=line.decode("utf-8", errors="replace").rstrip(),
            )

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = WireConnection(reader, writer)
        self._conn = conn

        # Worker → host RPCs for ctx.*
        for method, handler in self._ctx_handlers().items():
            conn.register(method, handler)
        conn.register("ready", self._on_ready)

        await conn.run()

        # The connection ended - the worker exited (cancel kill, crash, or
        # clean shutdown). Fail outstanding calls so awaiting handler tasks
        # don't hang; if this was the live worker and we're not deliberately
        # stopping, drop ready so the next call waits for a respawn.
        conn.fail_all_pending(SubprocessHostError(f"Worker for {self.manifest.id!r} exited."))
        if conn is self._conn and not self._stopping:
            self._ready_evt.clear()

    async def _on_ready(self, params: dict[str, Any]) -> Any:
        self._handlers_meta = params.get("handlers", [])
        self._ready_evt.set()
        return True

    async def call_handler(self, attr: str, ctx, args: tuple, kwargs: dict[str, Any]) -> Any:
        # A cancel may be mid-respawn - wait for the fresh worker rather than
        # dispatching onto a dead connection.
        if not self._ready_evt.is_set():
            try:
                await asyncio.wait_for(self._ready_evt.wait(), timeout=35.0)
            except TimeoutError as exc:
                raise SubprocessHostError(
                    f"Worker for {self.manifest.id!r} is not ready (restart timed out)."
                ) from exc
        if self._conn is None:
            raise SubprocessHostError(f"Worker for {self.manifest.id!r} is not connected.")
        token = uuid.uuid4().hex
        self._ctx_registry[token] = ctx
        # Map this call's token → job id (when the loader tagged the ctx) so a
        # soft cancel of that job makes every ctx RPC on this token raise.
        job_id = getattr(ctx, "_ff_job_id", None)
        if job_id is not None:
            self._token_job[token] = job_id
        try:
            ctx_meta = {
                "log_id": ctx.log_id,
                "module_id": ctx.module_id,
                "workdir": str(ctx.workdir),
                "config": ctx.config.value if hasattr(ctx.config, "value") else {},
                # Snapshot the visible capability names so the worker's
                # (synchronous) ctx.registry.has() answers without a round-trip.
                "capabilities": _registry_snapshot(ctx),
            }
            return await self._conn.send_request(
                "call",
                {
                    "handler": attr,
                    "ctx_token": token,
                    "ctx": ctx_meta,
                    "args": [_jsonify(a) for a in args],
                    "kwargs": {k: _jsonify(v) for k, v in kwargs.items()},
                },
            )
        finally:
            self._ctx_registry.pop(token, None)
            self._token_job.pop(token, None)
            # The call returned (completed, cooperatively cancelled, or killed):
            # drop the job's cancel flag so a later call reusing this worker
            # isn't immediately rejected by a stale flag.
            if job_id is not None:
                self._cancelled_job_ids.discard(job_id)

    def _ctx_handlers(self) -> dict[str, Callable]:
        """Wire ctx.* RPC names to real ModuleContext methods."""

        async def event_log_duckdb_fetch(params: dict[str, Any]) -> list[list[Any]]:
            ctx = self._ctx_registry[params["ctx_token"]]
            async with ctx.event_log as log_access:
                rows = await log_access.duckdb_fetch(params["sql"], params.get("params"))
            return [list(r) for r in rows]

        async def event_log_materialize(params: dict[str, Any]) -> str:
            # Write the (filter-applied) log to a Parquet under the per-call
            # workdir (shared filesystem) and hand the worker the path, so its
            # ctx.event_log.pandas()/polars()/pm4py() load it locally. The file
            # rides the workdir's auto-cleanup when the handler finishes.
            ctx = self._ctx_registry[params["ctx_token"]]
            async with ctx.event_log as log_access:
                df = await log_access.pandas()
            out = Path(ctx.workdir) / f"_eventlog_{uuid.uuid4().hex}.parquet"
            await asyncio.to_thread(df.to_parquet, str(out))
            return str(out)

        async def bus_emit(params: dict[str, Any]) -> None:
            ctx = self._ctx_registry[params["ctx_token"]]
            await ctx.bus.emit(params["topic"], params["payload"])

        async def cache_get(params: dict[str, Any]) -> Any:
            ctx = self._ctx_registry[params["ctx_token"]]
            return await ctx.cache.get(params["key"])

        async def cache_set(params: dict[str, Any]) -> None:
            ctx = self._ctx_registry[params["ctx_token"]]
            await ctx.cache.set(params["key"], params["value"])

        async def cache_exists(params: dict[str, Any]) -> bool:
            ctx = self._ctx_registry[params["ctx_token"]]
            return await ctx.cache.exists(params["key"])

        async def cache_delete(params: dict[str, Any]) -> None:
            ctx = self._ctx_registry[params["ctx_token"]]
            await ctx.cache.delete(params["key"])

        async def registry_call(params: dict[str, Any]) -> Any:
            ctx = self._ctx_registry[params["ctx_token"]]
            return await ctx.registry.call(params["capability"], **params.get("kwargs", {}))

        async def progress_update(params: dict[str, Any]) -> None:
            ctx = self._ctx_registry[params["ctx_token"]]
            await ctx.progress.update(
                params["current"],
                params.get("message"),
                total=params.get("total"),
                stage=params.get("stage"),
            )

        async def logger_log(params: dict[str, Any]) -> None:
            ctx = self._ctx_registry[params["ctx_token"]]
            level = params.get("level", "info")
            payload = params.get("payload", {})
            event = payload.pop("event", "")
            getattr(ctx.logger, level, ctx.logger.info)(event, **payload)

        async def cancel_check(params: dict[str, Any]) -> bool:
            # Dedicated, side-effect-free poll for ctx.check_cancelled() on a
            # new-SDK worker. The guard below raises CANCEL_RPC_MSG when flagged;
            # if not flagged this just returns False.
            return False

        handlers = {
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
        # Wrap every ctx RPC so it raises the cancel sentinel the moment its
        # job is soft-cancelled - making *each* ctx touch (progress/cache/duckdb/
        # registry/bus/logger/cancel-check) a cooperative poll point. The worker
        # reconstructs the sentinel as `Cancelled` and unwinds the handler.
        return {name: self._guard_cancel(fn) for name, fn in handlers.items()}

    def _guard_cancel(self, fn: Callable) -> Callable:
        async def wrapped(params: dict[str, Any]) -> Any:
            job_id = self._token_job.get(params.get("ctx_token", ""))
            if job_id is not None and job_id in self._cancelled_job_ids:
                raise SubprocessHostError(CANCEL_RPC_MSG)
            result = fn(params)
            if asyncio.iscoroutine(result):
                return await result
            return result

        return wrapped

    async def soft_cancel(self, job_id: str) -> None:
        """Phase-1 cancel: flag *job_id* so its worker's next ctx RPC raises the
        cancel sentinel (cooperative wind-down). Returns immediately - no kill."""
        self._cancelled_job_ids.add(job_id)

    def clear_cancel(self, job_id: str) -> None:
        """Drop a job's soft-cancel flag (e.g. after a hard escalation) so a
        worker reused for a later call isn't poisoned by the stale flag."""
        self._cancelled_job_ids.discard(job_id)


def _worker_python(folder: Path) -> Path:
    """Path to the module's venv python (with platform sdk available via the
    MetaPathFinder shim during in_process - for subprocess we use the venv
    python directly since it's isolated)."""
    candidates = [folder / ".venv" / "bin" / "python3", folder / ".venv" / "bin" / "python"]
    for c in candidates:
        if c.exists():
            return c
    raise SubprocessHostError(
        f"No .venv/bin/python3 under {folder} - install must run before starting the subprocess."
    )


def _jsonify(value: Any) -> Any:
    """Best-effort JSON-native form for a handler arg crossing the socket.
    Pydantic models dump to dicts; everything else passes through (the worker
    receives JSON-native types, not reconstructed models)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _registry_snapshot(ctx: Any) -> list[str]:
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
