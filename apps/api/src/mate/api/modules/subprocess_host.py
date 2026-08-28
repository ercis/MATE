"""Host-side wrapper for worker-bridged modules (§5.4, modules/PROTOCOL.md).

Spawns the module's worker process (argv/env/cwd come from the runtime's
`WorkerLaunchSpec` - a Python worker on the module's `.venv`, a JVM worker
via `java -jar`, ...), listens on a Unix socket for the worker's connection,
and exposes a `SubprocessModule` object that mimics the in-process `Module`
instance: each handler is a sync stub that the loader picks up via the same
`_collect_handlers` machinery, but calling it routes through JSON-RPC to the
worker.

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
from pathlib import Path
from typing import Any

import structlog

from mate.api.config import get_settings
from mate.api.jobs.supervisor import ChildHandle, get_child_supervisor
from mate.api.modules.ctx_rpc import (
    CANCEL_RPC_MSG,
    build_ctx_meta,
    jsonify,
    make_ctx_dispatcher,
)
from mate.api.modules.job_logs import get_job_log_buffer
from mate.api.modules.runtimes.base import WorkerLaunchSpec
from mate.api.modules.subprocess_worker import RPC_STREAM_LIMIT, WireConnection
from mate.api.tasks import spawn
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

# Re-exported from `ctx_rpc` (single source of truth, shared with JobWorker).
__all__ = ["CANCEL_RPC_MSG", "SubprocessBridge", "SubprocessHostError", "SubprocessModule"]

# Newest wire-protocol version this host understands (modules/PROTOCOL.md §1).
# A worker advertising a higher `protocol` in `ready` fails to mount instead of
# misbehaving subtly; a missing field means 1 (pre-versioning workers).
SUPPORTED_PROTOCOL_MAX = 1

# A worker that stayed ready this long is considered stable: the next crash
# starts the respawn-backoff ladder from scratch instead of continuing where a
# long-ago incident left off (a once-a-day OOM kill must self-heal forever).
_STABLE_UPTIME_RESET_SECONDS = 60.0


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
        self,
        manifest_id: str,
        handlers_meta: list[dict[str, Any]],
        bridge: SubprocessBridge,
        guidance_meta: dict[str, Any] | None = None,
    ) -> None:
        self.id = manifest_id
        self._bridge = bridge
        self._handlers_meta = handlers_meta
        self._install_stubs()
        self._install_guidance(guidance_meta)

    def _install_guidance(self, guidance_meta: dict[str, Any] | None) -> None:
        """Forward ``guidance_payload`` when the worker's instance has one.

        Instance-level only (never on the class): guidance is duck-typed via
        ``getattr(loaded.instance, "guidance_payload", ...)`` in the AI/MCP
        path, not collected by the loader's type-level handler walk - and a
        class attribute would leak across every subprocess module sharing this
        shim class. Bound to the bridge's generic ``call`` RPC, so the worker
        dispatches it like any handler; ``build_ctx_meta`` already omits the
        raw-log paths for a restricted ctx, keeping the data wall intact
        across the process boundary (cache reads still work).
        """
        if guidance_meta is None:
            return
        bridge = self._bridge

        async def guidance_payload(ctx: Any) -> Any:
            return await bridge.call_handler("guidance_payload", ctx, (), {})

        guidance_payload.__name__ = "guidance_payload"
        guidance_payload.__qualname__ = f"SubprocessModule.{self.id}.guidance_payload"
        self.guidance_payload = guidance_payload
        if guidance_meta.get("system_prompt"):
            self.guidance_system_prompt = str(guidance_meta["system_prompt"])
        if guidance_meta.get("user_prefix"):
            self.guidance_user_prefix = str(guidance_meta["user_prefix"])

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

    def __init__(self, manifest: Manifest, folder: Path, launch: WorkerLaunchSpec) -> None:
        self.manifest = manifest
        self.folder = folder
        self._launch = launch
        self._server: asyncio.base_events.Server | None = None
        self._conn: WireConnection | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._socket_dir = Path(tempfile.mkdtemp(prefix=f"ff-sock-{manifest.id}-"))
        self._socket_path = self._socket_dir / "rpc.sock"
        self._ready_evt = asyncio.Event()
        self._handlers_meta: list[dict[str, Any]] = []
        self._guidance_meta: dict[str, Any] | None = None
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
        # Ready-handshake rejection (e.g. unsupported protocol version) - set by
        # `_on_ready` alongside `_ready_evt` so waiters wake and see the error.
        self._ready_error: str | None = None
        # Terminal crash-loop state: once set, every call fails immediately with
        # this message until the module is fixed and reloaded.
        self._failed: str | None = None
        # Crash-respawn bookkeeping (modules/PROTOCOL.md §8): consecutive respawn
        # attempts since the last stable run, and when the worker last signalled
        # ready (monotonic clock) for the stable-uptime reset.
        self._respawn_attempts = 0
        self._last_ready_monotonic: float | None = None
        settings = get_settings()
        self._respawn_max_attempts: int = settings.subprocess_respawn_max_attempts
        self._respawn_backoff_cap: float = settings.subprocess_respawn_backoff_cap_seconds
        # The worker's registration with the platform child supervisor, so a
        # global `kill_all()` (shutdown) reaps it too. Re-set on every (re)spawn.
        self._sup_handle: ChildHandle | None = None

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
        if self._ready_error is not None:
            error = self._ready_error
            await self.stop()
            raise SubprocessHostError(error)

        return SubprocessModule(
            self.manifest.id, self._handlers_meta, self, guidance_meta=self._guidance_meta
        )

    async def _spawn_worker(self) -> None:
        """Spawn (or respawn) the worker process against the live socket.

        `start_new_session=True` puts the worker in its own process group, so
        `cancel_active()` can `killpg` the whole subtree - the worker *and* any
        grandchildren it forked - without touching the API process group.
        """
        # The runtime's launch prefix (venv python + worker script, `java -jar
        # module.jar`, ...) plus the two positional protocol args every worker
        # receives: the socket to connect to and its module folder.
        cmd = [
            *self._launch.argv,
            str(self._socket_path),
            str(self.folder),
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **self._launch.env},
            cwd=str(self._launch.cwd) if self._launch.cwd is not None else None,
            start_new_session=True,
        )
        # Register (replacing any prior handle from a respawn) so the platform
        # controller can reap this worker's whole group on shutdown. Per-job
        # cancel still goes through `cancel_active()` (kill + respawn) - this
        # worker is shared across jobs, so it carries no single job_id.
        get_child_supervisor().unregister(self._sup_handle)
        self._sup_handle = get_child_supervisor().register(
            ChildHandle(pid=self._proc.pid, kind="subprocess_worker", module_id=self.manifest.id)
        )

        # Pipe worker stderr to our log so author tracebacks aren't lost. These
        # outlive the call, so they need a strong reference (see `tasks.spawn`)
        # or the GC can collect them and the drain stops silently.
        spawn(self._drain_pipe(self._proc.stderr, "stderr"))
        spawn(self._drain_pipe(self._proc.stdout, "stdout"))

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
        self._schedule_respawn(deliberate=True)

    def _schedule_respawn(self, *, deliberate: bool = False) -> None:
        """Start the respawn loop unless one is already in flight.

        `deliberate=True` (cancel kill) respawns immediately and doesn't count
        toward the crash-loop attempt cap - frequent job cancels must never
        push a healthy module into the failed state. `deliberate=False`
        (unexpected exit) walks the backoff ladder.
        """
        if self._stopping or self._failed is not None:
            return
        if self._respawn_task is not None and not self._respawn_task.done():
            # A respawn is already underway (e.g. `cancel_active` scheduled one
            # and the killed connection's EOF arrived right after).
            return
        self._respawn_task = asyncio.create_task(self._respawn_loop(first_immediate=deliberate))

    async def _respawn_loop(self, *, first_immediate: bool) -> None:
        """Respawn the worker until it signals ready or the attempt cap trips.

        Each failed start increments the attempt counter and backs off
        exponentially (capped); a worker that stayed ready for
        `_STABLE_UPTIME_RESET_SECONDS` resets the ladder, so isolated crashes
        self-heal forever while a boot-crash loop lands in the terminal failed
        state instead of burning CPU.
        """
        first = first_immediate
        while not self._stopping and self._failed is None:
            if not first:
                now = asyncio.get_running_loop().time()
                if (
                    self._last_ready_monotonic is not None
                    and now - self._last_ready_monotonic >= _STABLE_UPTIME_RESET_SECONDS
                ):
                    self._respawn_attempts = 0
                if self._respawn_attempts >= self._respawn_max_attempts:
                    self._enter_failed(
                        f"Worker for {self.manifest.id!r} crash-looped "
                        f"({self._respawn_attempts} consecutive failed starts) - fix the "
                        "module and reload it."
                    )
                    return
                delay = min(0.5 * (2**self._respawn_attempts), self._respawn_backoff_cap)
                self._respawn_attempts += 1
                if delay:
                    await asyncio.sleep(delay)
                if self._stopping:
                    return
            first = False
            try:
                await self._spawn_worker()
                await asyncio.wait_for(self._ready_evt.wait(), timeout=30.0)
            except Exception:
                log.exception(
                    "modules.subprocess.worker_restart_failed",
                    module_id=self.manifest.id,
                    attempt=self._respawn_attempts,
                )
                continue
            if self._ready_error is not None:
                # A protocol mismatch won't fix itself by respawning.
                self._enter_failed(self._ready_error)
                return
            log.info(
                "modules.subprocess.worker_restarted",
                module_id=self.manifest.id,
                attempt=self._respawn_attempts,
            )
            return

    def _enter_failed(self, message: str) -> None:
        """Terminal state: stop respawning, fail every call fast. Cleared only
        by a module reload (which builds a fresh bridge)."""
        self._failed = message
        # Wake any `call_handler` blocked on the ready event so it fails now.
        self._ready_evt.set()
        log.error("modules.subprocess.worker_failed", module_id=self.manifest.id, error=message)

    def _kill_worker_group(self) -> None:
        """SIGKILL the worker's whole process group. SIGKILL (not TERM) because
        a thread deep in a native numpy/pm4py call won't service a handler in
        time - only an unconditional kill guarantees the CPU stops now."""
        get_child_supervisor().unregister(self._sup_handle)
        self._sup_handle = None
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
        if self._respawn_task is not None and not self._respawn_task.done():
            # The loop may be asleep in a backoff window - don't wait it out.
            self._respawn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._respawn_task
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._conn.send_request("shutdown", {}), timeout=2.0)
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                self._kill_worker_group()
        # Drop the supervisor registration whichever way the worker stopped (the
        # graceful terminate path above doesn't go through `_kill_worker_group`).
        get_child_supervisor().unregister(self._sup_handle)
        self._sup_handle = None
        if self._server is not None:
            self._server.close()
            # Belt-and-braces bound: `_on_connect` closes its writer on EOF, but
            # a wedged connection must never hang platform shutdown/reload.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), timeout=5.0)
        shutil.rmtree(self._socket_dir, ignore_errors=True)
        # Leave the bridge unmistakably dead. `_on_connect`'s teardown skips
        # clearing these while `_stopping` is set, so a stopped bridge used to
        # keep a set `_ready_evt` and a closed `_conn` - `call_handler` sailed
        # past both guards and wrote to the dead transport, and the caller got
        # uvloop's `unable to perform operation on <UnixTransport closed=True
        # ...>; the handler is closed` instead of a diagnosis. Callers should no
        # longer reach a stopped bridge at all (handlers resolve through
        # `ModuleLoader._live_handler`), but a stale reference must fail loudly.
        self._conn = None
        self._ready_evt.clear()

    async def _drain_pipe(self, stream, label: str) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            log.info(
                "modules.subprocess.worker_output",
                module_id=self.manifest.id,
                stream=label,
                line=text,
            )
            # Mirror into the per-job ring too (module authors' compute code
            # usually talks to stdout/stderr directly, not `ctx.logger`), same as
            # JobWorker's one-shot path. This worker is shared across calls, so
            # attribute the line to whichever job(s) currently have a call in
            # flight (`_token_job`) - unambiguous in practice, since heavy
            # subprocess runs are exclusive (see `cancel_active`). Idle output
            # (e.g. import-time chatter before any call) has no job to attach to.
            level = "warning" if label == "stderr" else "info"
            for job_id in {*self._token_job.values()}:
                get_job_log_buffer().append(job_id, level, text, {"stream": label})

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = WireConnection(reader, writer)
        self._conn = conn

        # Worker → host RPCs for ctx.* (shared dispatcher; each call is a
        # cooperative cancel poll point via `_is_cancelled`).
        for method, handler in make_ctx_dispatcher(
            self._ctx_registry.get, self._is_cancelled
        ).items():
            conn.register(method, handler)
        conn.register("ready", self._on_ready)

        # A worker that dies mid-read (our own SIGKILL on cancel/shutdown, or a
        # crash) tears the socket down under `readline`. That's the *expected*
        # end of this connection, not a fault: let it fall through to the
        # teardown below instead of escaping into asyncio's
        # `client_connected_cb` handler, which logged a bare BrokenPipeError
        # traceback on every reload.
        try:
            await conn.run()
        except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError) as exc:
            log.debug(
                "modules.subprocess.connection_lost",
                module_id=self.manifest.id,
                error=str(exc) or type(exc).__name__,
            )

        # The connection ended - the worker exited (cancel kill, crash, or
        # clean shutdown). Close our transport half too: Python 3.12's
        # `Server.wait_closed()` waits for every connection to fully close, so
        # a lingering half-open writer would hang `stop()` forever.
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        # Fail outstanding calls so awaiting handler tasks don't hang; if this
        # was the live worker and we're not deliberately stopping, drop ready
        # and auto-respawn (a spontaneous crash used to strand the module
        # until the next hard cancel).
        conn.fail_all_pending(SubprocessHostError(f"Worker for {self.manifest.id!r} exited."))
        if conn is self._conn and not self._stopping:
            self._ready_evt.clear()
            self._schedule_respawn()

    async def _on_ready(self, params: dict[str, Any]) -> Any:
        try:
            protocol = int(params.get("protocol", 1))
        except (TypeError, ValueError):
            protocol = -1
        if protocol > SUPPORTED_PROTOCOL_MAX or protocol < 1:
            self._ready_error = (
                f"Worker for {self.manifest.id!r} speaks wire protocol "
                f"{params.get('protocol')!r}; this host supports <= {SUPPORTED_PROTOCOL_MAX}. "
                "Upgrade the platform or build the module against an older SDK."
            )
            self._ready_evt.set()  # wake waiters; they check _ready_error
            return False
        self._ready_error = None
        self._handlers_meta = params.get("handlers", [])
        self._guidance_meta = params.get("guidance")
        self._last_ready_monotonic = asyncio.get_running_loop().time()
        self._ready_evt.set()
        return True

    async def ping(self, timeout: float = 5.0) -> bool:
        """Round-trip liveness probe (protocol `ping`). False on any failure -
        no worker, not ready, failed state, or no reply within *timeout*."""
        if self._failed is not None or self._conn is None or not self._ready_evt.is_set():
            return False
        try:
            result = await asyncio.wait_for(self._conn.send_request("ping", {}), timeout)
        except Exception:
            return False
        return result is True

    async def call_handler(self, attr: str, ctx, args: tuple, kwargs: dict[str, Any]) -> Any:
        # Crash-looped workers fail fast with the diagnosis instead of burning
        # 35s per call waiting for a respawn that will never come.
        if self._failed is not None:
            raise SubprocessHostError(self._failed)
        # Same for a torn-down bridge: `stop()` will never signal ready again,
        # so waiting on `_ready_evt` below could only time out after 35s.
        if self._stopping:
            raise SubprocessHostError(
                f"Worker for {self.manifest.id!r} was stopped (module unloaded or "
                "reloaded). Retry the call against the reloaded module."
            )
        # A cancel/crash may be mid-respawn - wait for the fresh worker rather
        # than dispatching onto a dead connection.
        if not self._ready_evt.is_set():
            try:
                await asyncio.wait_for(self._ready_evt.wait(), timeout=35.0)
            except TimeoutError as exc:
                raise SubprocessHostError(
                    f"Worker for {self.manifest.id!r} is not ready (restart timed out)."
                ) from exc
        if self._failed is not None:
            raise SubprocessHostError(self._failed)
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
            return await self._conn.send_request(
                "call",
                {
                    "handler": attr,
                    "ctx_token": token,
                    "ctx": build_ctx_meta(ctx),
                    "args": [jsonify(a) for a in args],
                    "kwargs": {k: jsonify(v) for k, v in kwargs.items()},
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

    def _is_cancelled(self, ctx_token: str) -> bool:
        """Whether the job behind *ctx_token* has been soft-cancelled - the
        cooperative-poll predicate the shared ctx dispatcher checks before every
        ctx.* call (a flagged job makes the call raise the cancel sentinel)."""
        job_id = self._token_job.get(ctx_token)
        return job_id is not None and job_id in self._cancelled_job_ids

    async def soft_cancel(self, job_id: str) -> None:
        """Phase-1 cancel: flag *job_id* so its worker's next ctx RPC raises the
        cancel sentinel (cooperative wind-down). Returns immediately - no kill."""
        self._cancelled_job_ids.add(job_id)

    def clear_cancel(self, job_id: str) -> None:
        """Drop a job's soft-cancel flag (e.g. after a hard escalation) so a
        worker reused for a later call isn't poisoned by the stale flag."""
        self._cancelled_job_ids.discard(job_id)
