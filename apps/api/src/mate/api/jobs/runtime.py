"""In-process asyncio job runtime.

What's wired (phase 4):

  - SQLite-persisted Job rows; UUID v7 ids.
  - Configurable worker pool (asyncio tasks) with cooperative cancellation
    via a per-job `asyncio.Event`.
  - Lifecycle events emitted on the platform `EventBus`: `job.queued`,
    `job.started`, `job.progress`, `job.completed`, `job.failed`,
    `job.cancelled`, `job.queue.paused`, `job.queue.resumed`.
  - Per-user pause/resume: a paused user's dequeued jobs are parked until
    they resume; other tenants keep flowing and already-running jobs of the
    paused user finish (the spec calls pause/resume out in §7.9.5).
  - Progress is throttled to SQLite (every `progress_persist_every` ticks),
    but every call broadcasts on the bus - this keeps the drawer's per-job
    `WS /jobs/{id}/stream` smooth without writing to disk thousands of times
    per import.
  - Retry: re-enqueue with the same payload but a fresh job id.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from mate.api.config import Settings, get_settings
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Job, SystemSetting
from mate.api.events import EventBus, get_event_bus
from mate.api.uuid7 import uuid7_str
from mate.sdk import Cancelled as SdkCancelled

log = structlog.get_logger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# Set in `_run_one` before the handler task is created, so the handler's context -
# and any `ctx.run_in_process` it awaits, which runs in the same task - carries the
# owning job/user. `run_offloaded` reads them to register the spawned child for
# hard-kill and to enforce the per-user offload cap. None for offloads triggered
# outside a job (direct platform callers).
_CURRENT_JOB: ContextVar[str | None] = ContextVar("ff_current_job", default=None)
_CURRENT_USER: ContextVar[str | None] = ContextVar("ff_current_user", default=None)


class _OffloadKilledError(RuntimeError):
    """Raised host-side when an offload child exits without returning a result -
    a SIGKILL from the reaper/cancel path, or a native crash."""


def _recv_offload(conn: Any) -> tuple[bool, Any]:
    """Block (in a worker thread) until the offload child returns or its pipe
    closes. A clean run yields the child's ``(ok, value)``; a SIGKILL (from the
    reaper/cancel path) or a native crash closes the child's write end →
    ``EOFError`` here → `_OffloadKilledError`. No ``waitpid``/``is_alive`` polling, so
    this never races the host-side ``proc.join()`` on the same process object."""
    try:
        return conn.recv()
    except EOFError as exc:
        raise _OffloadKilledError("offload process exited without returning a result") from exc


def _sigkill_proc(proc: Any) -> None:
    """SIGKILL an offload child *and its process group*, so grandchildren the
    payload spawned (e.g. joblib/loky workers) die with it.

    The child ``setsid()``s, so its group id == its pid: ``killpg(pid)`` hits
    exactly that group. If ``setsid`` failed the child isn't a group leader and
    no group has id == pid → ``ProcessLookupError``, and we fall back to a
    single-process kill. The host's own group id can never equal a child pid, so
    this can never signal the API process.
    """
    pid = proc.pid
    if pid is None:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        with contextlib.suppress(Exception):
            proc.kill()


# Worker-pool sizing bounds - mirror ``Settings.worker_concurrency`` (ge=1, le=8).
# Surfaced to the UI by ``GET /system/jobs`` so the slider can't post out of range.
MIN_WORKERS = 1
MAX_WORKERS = 8

# ``system_settings`` key under which an admin's live worker-concurrency change is
# persisted so it survives a restart (re-applied in ``main.py`` at boot).
WORKER_CONCURRENCY_KEY = "worker_concurrency"

# Queue sentinel that asks one worker to retire (graceful scale-down). It can
# never collide with a real job id (those are UUIDv7 strings).
_RETIRE = object()


def _clamp_workers(n: int) -> int:
    return max(MIN_WORKERS, min(MAX_WORKERS, int(n)))


async def load_persisted_concurrency() -> int | None:
    """Read the admin-set worker concurrency from ``system_settings``.

    Returns ``None`` when nothing was persisted (fresh DB / never changed) so the
    caller keeps the env/default value. Tolerant of a missing table or bad value
    - boot must never fail because of this optional setting.
    """
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await session.get(SystemSetting, WORKER_CONCURRENCY_KEY)
        value = row.value_json if row is not None else None
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        return _clamp_workers(value)
    except Exception:
        log.warning("job_runtime.load_persisted_concurrency_failed", exc_info=True)
        return None


async def save_persisted_concurrency(n: int) -> None:
    """Upsert the worker-concurrency value into ``system_settings``."""
    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(SystemSetting, WORKER_CONCURRENCY_KEY)
        if row is None:
            session.add(SystemSetting(key=WORKER_CONCURRENCY_KEY, value_json=_clamp_workers(n)))
        else:
            row.value_json = _clamp_workers(n)
        await session.commit()


class JobCancelled(BaseException):
    """Raised inside a handler when the job is cancelled cooperatively.

    Derives from :class:`BaseException` (not :class:`Exception`) so a handler's
    broad ``except Exception:`` can't swallow a cooperative cancel - it mirrors
    :class:`asyncio.CancelledError`. ``_run_one`` catches it on a branch that
    precedes the generic ``except Exception``, turning it into ``job.cancelled``.
    """


# Cooperative-cancel exception types caught on the cancel branch of ``_run_one``,
# all deriving from BaseException so a handler's broad ``except Exception`` can't
# swallow them: ``JobCancelled`` (raised by JobHandle), ``asyncio.CancelledError``
# (task-cancel / shutdown), and the SDK's ``Cancelled`` - raised by a module via
# ``ctx.check_cancelled``, or reconstructed on the host from a subprocess
# worker's soft cancel (``subprocess_worker`` translates the cancel RPC error).
_COOPERATIVE_CANCEL_EXC: tuple[type[BaseException], ...] = (
    JobCancelled,
    asyncio.CancelledError,
    SdkCancelled,
)


JobHandler = Callable[["JobHandle"], Awaitable[None]]


@dataclass
class CancelToken:
    _flag: asyncio.Event = field(default_factory=asyncio.Event)

    def cancel(self) -> None:
        self._flag.set()

    @property
    def cancelled(self) -> bool:
        return self._flag.is_set()

    def raise_if_cancelled(self) -> None:
        if self._flag.is_set():
            raise JobCancelled()


@dataclass
class JobHandle:
    """Handed to a job handler. Provides progress reporting, payload access,
    a sessionmaker, the cancel token, and the bus.
    """

    id: str
    user_id: str
    type: str
    title: str
    subtitle: str | None
    module_id: str | None
    payload: dict[str, Any]
    sessionmaker: async_sessionmaker
    settings: Settings
    bus: EventBus
    cancel_token: CancelToken
    started_at: float
    _last_persist_count: int = 0

    @property
    def cancelled(self) -> bool:
        return self.cancel_token.cancelled

    def raise_if_cancelled(self) -> None:
        self.cancel_token.raise_if_cancelled()

    async def progress(
        self,
        current: int,
        total: int | None = None,
        *,
        stage: str | None = None,
        message: str | None = None,
        force: bool = False,
    ) -> None:
        # Auto-poll cooperative cancel on *every* progress tick - before any bus
        # publish or DB write. This makes any handler/module that reports progress
        # soft-cancellable for free (the import job, all in-process modules that
        # call ctx.progress.update, etc.) without each one wiring a poll itself.
        self.cancel_token.raise_if_cancelled()
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        rate = current / elapsed if current else None
        eta = ((total - current) / rate) if (rate and total and total > current) else None

        await self.bus.publish(
            "job.progress",
            {
                "id": self.id,
                "user_id": self.user_id,
                "type": self.type,
                "module_id": self.module_id,
                "current": current,
                "total": total,
                "stage": stage,
                "message": message,
                "rate": rate,
                "eta_seconds": eta,
            },
        )

        every = self.settings.progress_persist_every
        if not force and (current - self._last_persist_count) < every:
            return
        self._last_persist_count = current
        async with self.sessionmaker() as session:
            await session.execute(
                update(Job)
                .where(Job.id == self.id)
                .values(
                    progress_current=current,
                    progress_total=total,
                    stage=stage,
                    message=message,
                    rate=rate,
                    eta_seconds=eta,
                )
            )
            await session.commit()


@dataclass(frozen=True)
class RunningJobInfo:
    """Lightweight snapshot of an executing job for the admin resource sampler."""

    id: str
    user_id: str
    type: str
    title: str
    module_id: str | None
    started_at: float


def _mp_context():
    """Start method for the CPU-offload pool. Never plain ``fork``: the asyncio
    loop + DuckDB threads are already running, so a forked child can deadlock on
    an inherited lock. ``forkserver`` (Linux) forks from a clean server process;
    macOS uses ``spawn`` (its ``fork`` is unsafe with native libs)."""
    import multiprocessing as mp

    if sys.platform == "darwin":
        return mp.get_context("spawn")
    try:
        return mp.get_context("forkserver")
    except ValueError:  # pragma: no cover - forkserver unavailable
        return mp.get_context("spawn")


class JobRuntime:
    """Asyncio queue + worker pool. Handlers register by `type`."""

    def __init__(self, settings: Settings | None = None, bus: EventBus | None = None) -> None:
        self.settings = settings or get_settings()
        self._bus = bus
        # Carries job-id strings plus the ``_RETIRE`` sentinel (graceful scale-down).
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._handlers: dict[str, JobHandler] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._target_concurrency = _clamp_workers(self.settings.worker_concurrency)
        self._running = False
        # Pause is per-user (the queue itself is shared across all tenants). A
        # user in `_paused_users` has their dequeued jobs parked in `_deferred`
        # instead of run; everyone else keeps flowing. Resuming re-enqueues the
        # parked ids. Held in memory only - like queued jobs, deferred ids don't
        # survive a process restart (the queue isn't rebuilt from SQLite).
        self._paused_users: set[str] = set()
        self._deferred: dict[str, list[str]] = {}
        self._cancel_tokens: dict[str, CancelToken] = {}
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        # Live JobHandles for currently-executing jobs, keyed by job-id. Mirrors
        # `_running_tasks` (set in `_run_one`, popped in its finally); lets the
        # admin resource sampler attribute load to module/user without a DB hit.
        self._running_handles: dict[str, JobHandle] = {}
        # Killable CPU-offload (§8.3). One short-lived process per
        # `ctx.run_in_process` call (not a shared pool), so the reaper/cancel path
        # can SIGKILL a runaway offload without collateral-killing other tenants'
        # offloads. `_offload_procs[job_id]` holds a job's live children for
        # hard-kill; the two semaphores bound total and per-user concurrency (built
        # lazily on the running loop). `_running_by_user` mirrors live jobs/tenant.
        self._offload_procs: dict[str, set[Any]] = {}
        self._global_offload_sem: asyncio.Semaphore | None = None
        self._user_offload_sems: dict[str, asyncio.Semaphore] = {}
        self._running_by_user: Counter[str] = Counter()
        # Two-phase cancel hooks for subprocess-isolated module jobs (wired by
        # the module loader). The cooperative token + asyncio task-cancel only
        # unwind the host-side proxy await; the worker process keeps running the
        # handler. So on cancel we first call the *soft* hook (flag the worker so
        # its next ctx RPC raises and it unwinds cooperatively), then - after a
        # grace window - the *hard* hook (SIGKILL+respawn) if it hasn't stopped.
        self._subprocess_soft_canceller: Callable[[str, str], Awaitable[None]] | None = None
        self._subprocess_hard_canceller: Callable[[str, str], Awaitable[None]] | None = None
        # Grace-window watchdogs spawned by `cancel()`; tracked so they're
        # cleaned up when the job ends (`_run_one` finally) or the runtime stops.
        self._escalation_tasks: dict[str, asyncio.Task[None]] = {}
        # Wall-clock reaper watchdogs (one per running job) + the set of job-ids a
        # reaper has fired on, so `_run_one` records a timeout as a failure rather
        # than a user-cancel. Both cleared in `_run_one`'s finally.
        self._timeout_tasks: dict[str, asyncio.Task[None]] = {}
        self._timed_out: set[str] = set()

    def set_subprocess_soft_canceller(
        self, fn: Callable[[str, str], Awaitable[None]] | None
    ) -> None:
        """Register the soft `(job_id, module_id) -> None` cancel hook.

        Called immediately on cancel; should flag the worker so its next ctx RPC
        raises (cooperative wind-down) and return at once - never block.
        """
        self._subprocess_soft_canceller = fn

    def set_subprocess_hard_canceller(
        self, fn: Callable[[str, str], Awaitable[None]] | None
    ) -> None:
        """Register the hard `(job_id, module_id) -> None` cancel hook.

        Called only after the grace window elapses and the job is still running -
        the SIGKILL+respawn escalation for a worker that didn't wind down.
        """
        self._subprocess_hard_canceller = fn

    def set_subprocess_canceller(self, fn: Callable[[str, str], Awaitable[None]] | None) -> None:
        """Back-compat shim: register a single hook as the *hard* canceller.

        Older call sites wired one kill+respawn hook via this method; they keep
        working (the cancel path falls back to it when no soft hook is set).
        """
        self._subprocess_hard_canceller = fn

    def _ensure_bus(self) -> EventBus:
        return self._bus if self._bus is not None else get_event_bus()

    def _offload_limits(self) -> tuple[int, int]:
        """``(global, per_user)`` concurrent-offload caps. Per-user defaults to the
        global cap (no bite on a single-tenant box); `max_offloads_per_user`
        lowers it so one tenant can't hold every slot. Both clamped ``>= 1``."""
        glob = max(1, int(self.settings.module_process_pool_size))
        per_user = int(self.settings.max_offloads_per_user) or glob
        return glob, max(1, min(per_user, glob))

    def _ensure_global_offload_sem(self) -> asyncio.Semaphore:
        if self._global_offload_sem is None:
            self._global_offload_sem = asyncio.Semaphore(self._offload_limits()[0])
        return self._global_offload_sem

    def _ensure_user_offload_sem(self, user_id: str) -> asyncio.Semaphore:
        sem = self._user_offload_sems.get(user_id)
        if sem is None:
            sem = asyncio.Semaphore(self._offload_limits()[1])
            self._user_offload_sems[user_id] = sem
        return sem

    @contextlib.asynccontextmanager
    async def _offload_slot(self, user_id: str | None) -> AsyncGenerator[None, None]:
        """Admit one offload: take the per-user permit first (fairness gate), then
        a global permit (hard pool cap). Released in reverse on exit."""
        user_sem = self._ensure_user_offload_sem(user_id) if user_id else None
        if user_sem is not None:
            await user_sem.acquire()
        glob = self._ensure_global_offload_sem()
        try:
            await glob.acquire()
        except BaseException:
            if user_sem is not None:
                user_sem.release()
            raise
        try:
            yield
        finally:
            glob.release()
            if user_sem is not None:
                user_sem.release()

    async def run_offloaded(
        self,
        offload: tuple[str, str, str] | None,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run `fn` in a dedicated, killable offload process (§8.3).

        When `offload` is the calling module's `(folder, site_packages,
        module_file)` - bound per-module by the loader - the child imports that
        module by path and calls `fn` by name, so a spawned/forkserver child can
        reach a *dynamically-loaded* module's function (not importable by its
        synthetic `sys.modules` name). `offload=None` is the direct path for
        platform callers, where `fn` must be importable by qualified name. Args +
        return must be picklable either way.

        One short-lived process per call - **not** a warm pool. The wall-clock
        reaper (and any cancel) must be able to SIGKILL a runaway offload, which a
        `ProcessPoolExecutor` future can't do once it's running; per-call processes
        also keep killing one tenant's offload from collateral-killing another's.
        Concurrency is bounded by a global + per-user semaphore. The cost is a cold
        module import per call - acceptable for the once-per-job mining offloads
        this is built for.
        """
        job_id = _CURRENT_JOB.get()
        user_id = _CURRENT_USER.get()
        async with self._offload_slot(user_id):
            return await self._spawn_offload(job_id, offload, fn, args, kwargs)

    async def _spawn_offload(
        self,
        job_id: str | None,
        offload: tuple[str, str, str] | None,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        from mate.api.modules.process_offload import offload_child_main

        if offload is None:
            spec: dict[str, Any] = {"kind": "direct", "fn": fn, "args": args, "kwargs": kwargs}
        else:
            folder, site_packages, module_file = offload
            spec = {
                "kind": "module",
                "folder": folder,
                "site_packages": site_packages,
                "module_file": module_file,
                "qualname": fn.__qualname__,
                "args": args,
                "kwargs": kwargs,
            }

        mp_ctx = _mp_context()
        recv_conn, send_conn = mp_ctx.Pipe(duplex=False)
        # daemon=False: a payload may itself spawn (joblib/loky); daemonic procs
        # can't have children. We own teardown via `_sigkill_proc` + join instead.
        proc = mp_ctx.Process(target=offload_child_main, args=(send_conn, spec), daemon=False)
        proc.start()
        send_conn.close()  # host keeps only the read end; the child owns the write end
        if job_id is not None:
            self._offload_procs.setdefault(job_id, set()).add(proc)
        # The blocking recv runs in a thread we keep a handle to; cancelling the
        # job cancels the `shield` (we react by killing the child) but never the
        # recv task, so the pipe is always drained before we close it.
        recv_task = asyncio.create_task(asyncio.to_thread(_recv_offload, recv_conn))
        try:
            ok, value = await asyncio.shield(recv_task)
        except asyncio.CancelledError:
            # Reaper/cancel: SIGKILL the child so its blocked recv() EOFs and the
            # recv task settles, then wait it out before tearing down the pipe.
            if proc.is_alive():
                _sigkill_proc(proc)
            with contextlib.suppress(Exception):
                await recv_task
            raise
        finally:
            if job_id is not None:
                procs = self._offload_procs.get(job_id)
                if procs is not None:
                    procs.discard(proc)
                    if not procs:
                        self._offload_procs.pop(job_id, None)
            if proc.is_alive():
                _sigkill_proc(proc)
            # Reap the child (shielded so a re-cancel can't skip it → zombie), then
            # close our pipe end now that the recv task has settled.
            with contextlib.suppress(Exception):
                await asyncio.shield(asyncio.to_thread(proc.join))
            recv_conn.close()
        if ok:
            return value
        raise value  # exception raised inside the child, re-raised on the host

    def _kill_offload(self, job_id: str) -> None:
        """Hard-kill every live offload child of `job_id`. Called from `cancel()`
        (and thus the wall-clock reaper) so a runaway offloaded computation - which
        ignores the cooperative token and the asyncio task-cancel - actually stops
        burning CPU instead of running to natural completion past its timeout."""
        for proc in list(self._offload_procs.get(job_id, ())):
            _sigkill_proc(proc)

    async def run_in_process(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Direct CPU offload for platform callers (`fn` importable by qualified
        name). Module handlers instead get a per-module-bound `ctx.run_in_process`
        that also ships the module's import metadata (see `run_offloaded`)."""
        return await self.run_offloaded(None, fn, *args, **kwargs)

    def register(self, type_: str, handler: JobHandler) -> None:
        if type_ in self._handlers:
            raise RuntimeError(f"Job type already registered: {type_}")
        self._handlers[type_] = handler

    async def start(self) -> None:
        if self._running:
            return
        await self._reconcile_orphan_running()
        self._running = True
        self._target_concurrency = _clamp_workers(self.settings.worker_concurrency)
        for _ in range(self._target_concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop()))
        log.info("job_runtime.started", workers=self._target_concurrency)

    async def _reconcile_orphan_running(self) -> None:
        """Fail any rows left in `running` by a previous process.

        A worker can only ever crash mid-job (process killed, container
        restart) - there's no recovery thread to resume an in-flight job, so
        the row would otherwise stay `running` forever and the UI would show
        a phantom active task.
        """
        sm = get_sessionmaker()
        async with sm() as session:
            result = await session.execute(
                update(Job)
                .where(Job.status == "running")
                .values(
                    status="failed",
                    error="Worker terminated before job completed.",
                    finished_at=_utcnow_naive(),
                )
            )
            await session.commit()
            if result.rowcount:
                log.info(
                    "job_runtime.orphans_reconciled",
                    count=result.rowcount,
                )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        for task in self._running_tasks.values():
            task.cancel()
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
        self._running_tasks.clear()
        # Tear down any in-flight grace watchdogs so they don't fire (and try to
        # hard-kill a worker) after the runtime has stopped.
        for esc in self._escalation_tasks.values():
            esc.cancel()
        if self._escalation_tasks:
            await asyncio.gather(*self._escalation_tasks.values(), return_exceptions=True)
        self._escalation_tasks.clear()
        for reaper in self._timeout_tasks.values():
            reaper.cancel()
        if self._timeout_tasks:
            await asyncio.gather(*self._timeout_tasks.values(), return_exceptions=True)
        self._timeout_tasks.clear()
        self._timed_out.clear()
        for tok in self._cancel_tokens.values():
            tok.cancel()
        self._cancel_tokens.clear()
        # Hard-kill any offload children still alive - their owning jobs were
        # cancelled just above, so none should outlive the runtime.
        for procs in list(self._offload_procs.values()):
            for proc in list(procs):
                _sigkill_proc(proc)
        self._offload_procs.clear()
        self._global_offload_sem = None
        self._user_offload_sems.clear()
        self._running_by_user.clear()
        log.info("job_runtime.stopped")

    @contextlib.asynccontextmanager
    async def lifespan(self):
        await self.start()
        try:
            yield self
        finally:
            await self.stop()

    def concurrency(self) -> int:
        """Current target worker count - reflects live changes, not just boot."""
        return self._target_concurrency

    def live_stats(self) -> dict[str, int]:
        """In-memory runtime snapshot for the admin dashboard.

        Cross-user counts (the asyncio queue is shared across all tenants), so
        this is admin-only by convention - the route gates it.
        """
        return {
            "concurrency": self._target_concurrency,
            "live_workers": self._live_worker_count(),
            "queue_depth": self._queue.qsize(),
            "running": len(self._running_tasks),
            "paused_users": len(self._paused_users),
        }

    def running_jobs(self) -> list[RunningJobInfo]:
        """Snapshot of currently-executing jobs (admin resource breakdown).

        Cross-user and in-memory - admin-only by convention, like `live_stats`.
        """
        return [
            RunningJobInfo(
                id=h.id,
                user_id=h.user_id,
                type=h.type,
                title=h.title,
                module_id=h.module_id,
                started_at=h.started_at,
            )
            for h in self._running_handles.values()
        ]

    def _live_worker_count(self) -> int:
        return sum(1 for w in self._workers if not w.done())

    async def set_concurrency(self, n: int) -> int:
        """Resize the worker pool live (Settings → General → Jobs, admin-only).

        Scale-up spawns workers immediately. Scale-down is graceful: one retire
        sentinel is queued per surplus worker; an idle worker retires at once, a
        busy one only after it finishes its current job - so a resize never
        orphans a running job, and a backlog is drained before any worker is
        shed. The ProcessPool (CPU offload, §8.3) keeps its current size until
        next (re)created; the asyncio worker pool - the meaningful knob - is
        resized here. Returns the clamped value actually applied.
        """
        n = _clamp_workers(n)
        # Mutate the shared Settings singleton so a future ProcessPool and the
        # diagnostics blob reflect the live value.
        self.settings.worker_concurrency = n
        self._target_concurrency = n
        if not self._running:
            return n
        self._workers = [w for w in self._workers if not w.done()]
        live = len(self._workers)
        if n > live:
            for _ in range(n - live):
                self._workers.append(asyncio.create_task(self._worker_loop()))
        elif n < live:
            for _ in range(live - n):
                self._queue.put_nowait(_RETIRE)
        log.info("job_runtime.concurrency_changed", workers=n, live_before=live)
        return n

    def is_paused(self, user_id: str) -> bool:
        return user_id in self._paused_users

    def paused_user_ids(self) -> list[str]:
        """Snapshot of users whose queue is currently paused (admin monitoring)."""
        return sorted(self._paused_users)

    async def pause_queue(self, user_id: str) -> None:
        if user_id in self._paused_users:
            return
        self._paused_users.add(user_id)
        # user_id scopes the event so only the pausing user's sessions flip to
        # "Paused" - other tenants' jobs (and dock badges) are unaffected.
        await self._ensure_bus().publish("job.queue.paused", {"user_id": user_id})
        log.info("job_runtime.queue_paused", user_id=user_id)

    async def resume_queue(self, user_id: str) -> None:
        if user_id not in self._paused_users:
            return
        self._paused_users.discard(user_id)
        deferred = self._deferred.pop(user_id, [])
        for job_id in deferred:
            await self._queue.put(job_id)
        await self._ensure_bus().publish("job.queue.resumed", {"user_id": user_id})
        log.info("job_runtime.queue_resumed", user_id=user_id, requeued=len(deferred))

    async def submit(
        self,
        *,
        type_: str,
        user_id: str,
        title: str,
        payload: dict[str, Any],
        subtitle: str | None = None,
        module_id: str | None = None,
        priority: int = 0,
        parent_job_id: str | None = None,
        job_id: str | None = None,
    ) -> str:
        if type_ not in self._handlers:
            raise RuntimeError(f"No handler registered for job type: {type_}")
        job_id = job_id or uuid7_str()
        sm = get_sessionmaker()
        async with sm() as session:
            session.add(
                Job(
                    id=job_id,
                    user_id=user_id,
                    type=type_,
                    title=title,
                    subtitle=subtitle,
                    module_id=module_id,
                    payload_json=payload,
                    status="queued",
                    priority=priority,
                    parent_job_id=parent_job_id,
                )
            )
            await session.commit()

        await self._ensure_bus().publish(
            "job.queued",
            {
                "id": job_id,
                "user_id": user_id,
                "type": type_,
                "title": title,
                "subtitle": subtitle,
                "module_id": module_id,
                "priority": priority,
                "parent_job_id": parent_job_id,
            },
        )
        await self._queue.put(job_id)
        return job_id

    async def cancel(self, job_id: str) -> bool:
        """Mark a job cancelled. Returns True if it was queued or running.

        - If running: the cooperative `CancelToken` is set; the handler is
          expected to call `handle.raise_if_cancelled()` periodically (any
          progress tick polls it for free). The worker catches `JobCancelled`
          and updates the row + emits the event. For a subprocess module the
          token can't reach the worker, so we additionally run the two-phase
          soft→grace→hard cancel (see below).
        - If queued: we mark the row cancelled and emit the event right away;
          when the worker pulls the id off the queue it'll see the status and
          skip the work.
        """
        sm = get_sessionmaker()
        async with sm() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return False
            if job.status not in {"queued", "running"}:
                return False
            running = job.status == "running"
            owner_id = job.user_id
            module_id = job.module_id
            if not running:
                job.status = "cancelled"
                job.finished_at = _utcnow_naive()
                await session.commit()

        token = self._cancel_tokens.get(job_id)
        if token is not None:
            token.cancel()

        task = self._running_tasks.get(job_id)
        if task is not None:
            task.cancel()

        # An offloaded computation (`ctx.run_in_process`) is pure CPU in a separate
        # process: it ignores both the cooperative token and the asyncio
        # task-cancel above. Hard-kill its children so a reaper-fired timeout (or a
        # user cancel) actually stops the burn instead of orphaning the process to
        # run to natural completion long past its deadline.
        if running:
            self._kill_offload(job_id)

        # The token/task-cancel above only unwinds the host-side await for a
        # subprocess module - the worker keeps running. Two-phase stop: ask it to
        # wind down cooperatively now (soft), then escalate to kill+respawn after
        # a grace window if it's still running.
        if running and module_id:
            await self._begin_subprocess_cancel(job_id, module_id)

        if not running:
            await self._ensure_bus().publish(
                "job.cancelled",
                {"id": job_id, "user_id": owner_id, "reason": "queued"},
            )
        return True

    async def _begin_subprocess_cancel(self, job_id: str, module_id: str) -> None:
        """Soft-cancel a running subprocess job, then arm the grace watchdog.

        The soft hook only flags the worker (its next ctx RPC raises) and returns
        immediately, so cancel() stays responsive. If neither hook is wired (a
        plain in-process module - no bridge) this is a no-op: such jobs stop on
        the cooperative token alone.
        """
        soft = self._subprocess_soft_canceller
        if soft is not None:
            try:
                await soft(job_id, module_id)
            except Exception:
                logging.exception("subprocess soft-cancel hook failed for job %s", job_id)
        # Arm the escalation watchdog only if a hard hook exists and one isn't
        # already running for this job. The watchdog sleeps off the event loop in
        # its own task - it never blocks cancel() or the worker pool.
        if self._subprocess_hard_canceller is not None and job_id not in self._escalation_tasks:
            self._escalation_tasks[job_id] = asyncio.create_task(
                self._escalate_subprocess_cancel(job_id, module_id)
            )

    async def _escalate_subprocess_cancel(self, job_id: str, module_id: str) -> None:
        """Wait out the grace window, then hard-kill the worker iff still running.

        A worker that wound down cooperatively (its handler task left
        `_running_tasks` and `_run_one` cancelled this watchdog in its finally)
        is never hard-killed. One that ignored the soft signal - a native job
        with no poll point - gets the SIGKILL+respawn it would have gotten before
        this two-phase change, just delayed by the grace.
        """
        grace = self.settings.subprocess_cancel_grace_seconds
        try:
            await asyncio.sleep(grace)
        except asyncio.CancelledError:
            return
        # Still running after grace → escalate. (If it finished, `_run_one` has
        # already popped the task and cancelled this watchdog.)
        if job_id not in self._running_tasks:
            return
        hard = self._subprocess_hard_canceller
        if hard is None:
            return
        try:
            await hard(job_id, module_id)
        except Exception:
            logging.exception("subprocess hard-cancel hook failed for job %s", job_id)
        finally:
            self._escalation_tasks.pop(job_id, None)

    async def _reap_after_timeout(self, job_id: str, timeout: float) -> None:
        """Wall-clock backstop for one running job.

        A handler still running after `timeout` seconds is force-stopped via the
        normal `cancel()` path (cooperative token + asyncio task-cancel + the
        subprocess two-phase soft->hard kill), then recorded as a timeout failure
        by `_run_one`. This is what stops a wedged job from holding its worker slot
        forever - the slot leak behind cross-user job starvation. Cancelled by
        `_run_one`'s finally the instant the job ends, so a job that finishes
        within budget is never reaped.
        """
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        # Lost the race with normal completion - the finally already pulled it.
        if job_id not in self._running_tasks:
            return
        self._timed_out.add(job_id)
        log.warning("job_runtime.job_timed_out", job_id=job_id, timeout_seconds=timeout)
        await self.cancel(job_id)

    async def cancel_for_logs(self, log_ids: list[str]) -> int:
        """Cancel every queued/running job whose payload references one of `log_ids`.

        Jobs don't carry an indexed `log_id` column - the affiliation lives in
        `payload_json["log_id"]`. We pull all active jobs and filter in Python,
        which is fine because the active set is small (bounded by the worker
        pool + queue depth, not history).
        """
        if not log_ids:
            return 0
        wanted = set(log_ids)
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                await session.execute(
                    select(Job.id, Job.payload_json).where(Job.status.in_(("queued", "running")))
                )
            ).all()

        cancelled = 0
        for job_id, payload in rows:
            if isinstance(payload, dict) and payload.get("log_id") in wanted:
                if await self.cancel(job_id):
                    cancelled += 1
        return cancelled

    async def cancel_all(self) -> int:
        """Cancel every queued and running job. Returns the count cancelled.

        Issues per-job `cancel()` calls so the existing path (DB row flip,
        token signal, asyncio task cancel, `job.cancelled` event) runs for
        each one - keeps the UI in sync without a separate broadcast.
        """
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                (await session.execute(select(Job.id).where(Job.status.in_(("queued", "running")))))
                .scalars()
                .all()
            )

        cancelled = 0
        for job_id in rows:
            if await self.cancel(job_id):
                cancelled += 1
        return cancelled

    async def retry(self, job_id: str) -> str | None:
        """Re-enqueue a failed job with the same payload. Returns the new job id."""
        sm = get_sessionmaker()
        async with sm() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != "failed":
                return None
            new_id = await self.submit(
                type_=job.type,
                user_id=job.user_id,
                title=job.title,
                subtitle=job.subtitle,
                module_id=job.module_id,
                payload=dict(job.payload_json),
                priority=job.priority,
                parent_job_id=job.parent_job_id,
            )
        return new_id

    async def _worker_loop(self) -> None:
        sm = get_sessionmaker()
        while self._running:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                return

            try:
                if item is _RETIRE:
                    # Graceful scale-down signal: retire this worker iff we're
                    # still over target. A scale-up that arrived after the
                    # sentinel was queued cancels the need - then it's a no-op.
                    if self._live_worker_count() > self._target_concurrency:
                        current = asyncio.current_task()
                        self._workers = [w for w in self._workers if w is not current]
                        return
                    continue
                if not isinstance(item, str):
                    continue
                job_id = item
                # Pause is per-user: if this job's owner is paused, park it and
                # move on so other tenants' jobs keep running. resume_queue()
                # re-enqueues the parked ids.
                if await self._maybe_defer(job_id, sm):
                    continue
                await self._run_one(job_id, sm)
            except Exception as exc:
                log.exception("job_runtime.unexpected_error", job_id=item, error=str(exc))
            finally:
                self._queue.task_done()

    async def _maybe_defer(self, job_id: str, sm: async_sessionmaker) -> bool:
        """Park *job_id* if its owner is currently paused. Returns True if parked.

        Fast-paths the common case (nobody paused) without touching the DB.
        """
        if not self._paused_users:
            return False
        async with sm() as session:
            owner = await session.scalar(select(Job.user_id).where(Job.id == job_id))
        if owner is not None and owner in self._paused_users:
            self._deferred.setdefault(owner, []).append(job_id)
            return True
        return False

    async def _run_one(self, job_id: str, sm: async_sessionmaker) -> None:
        bus = self._ensure_bus()

        async with sm() as session:
            job = await session.get(Job, job_id)
            if job is None:
                log.warning("job_runtime.missing_job", job_id=job_id)
                return
            if job.status == "cancelled":
                # Cancelled while queued - already handled in `cancel()`.
                return
            handler = self._handlers.get(job.type)
            if handler is None:
                job.status = "failed"
                job.error = f"No handler registered for type {job.type!r}"
                job.finished_at = _utcnow_naive()
                await session.commit()
                await bus.publish(
                    "job.failed",
                    {
                        "id": job.id,
                        "user_id": job.user_id,
                        "type": job.type,
                        "error": job.error,
                    },
                )
                return
            job.status = "running"
            job.started_at = _utcnow_naive()
            await session.commit()

            handle_payload = dict(job.payload_json)
            handle_title = job.title
            handle_subtitle = job.subtitle
            handle_module_id = job.module_id
            handle_type = job.type
            handle_user_id = job.user_id

        token = CancelToken()
        self._cancel_tokens[job_id] = token

        await bus.publish(
            "job.started",
            {
                "id": job_id,
                "user_id": handle_user_id,
                "type": handle_type,
                "title": handle_title,
                "module_id": handle_module_id,
            },
        )

        handle = JobHandle(
            id=job_id,
            user_id=handle_user_id,
            type=handle_type,
            title=handle_title,
            subtitle=handle_subtitle,
            module_id=handle_module_id,
            payload=handle_payload,
            sessionmaker=sm,
            settings=self.settings,
            bus=bus,
            cancel_token=token,
            started_at=time.monotonic(),
        )

        # Stamp the owning job/user onto the context *before* the handler task is
        # created so the task (and any `ctx.run_in_process` it awaits) inherits
        # them - that's how `run_offloaded` knows which job to register the killable
        # child under and which user's offload cap to charge.
        _CURRENT_JOB.set(job_id)
        _CURRENT_USER.set(handle_user_id)
        self._running_by_user[handle_user_id] += 1
        handler_task = asyncio.create_task(self._handlers[handle_type](handle))
        self._running_tasks[job_id] = handler_task
        self._running_handles[job_id] = handle
        timeout = self.settings.job_execution_timeout_seconds
        if timeout and timeout > 0:
            self._timeout_tasks[job_id] = asyncio.create_task(
                self._reap_after_timeout(job_id, float(timeout))
            )
        try:
            await handler_task
        except _COOPERATIVE_CANCEL_EXC as exc:
            # A genuine task-cancel with no cooperative token set is a shutdown
            # signal (stop() cancels every running task) - propagate it so the
            # worker exits cleanly. JobCancelled / SDK Cancelled, by contrast,
            # only ever mean "this job was cancelled", so they always record the
            # cancelled outcome even if the token flip hasn't been observed yet.
            if isinstance(exc, asyncio.CancelledError) and not token.cancelled:
                handler_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await handler_task
                raise
            # A reaper-fired timeout reuses this cooperative-cancel path (the reaper
            # calls `cancel()`), but it's an operator-relevant failure, not a user
            # cancel - record it as failed-timeout so it surfaces in the jobs UI.
            if job_id in self._timed_out:
                error = (
                    f"Job exceeded the {self.settings.job_execution_timeout_seconds}s "
                    "execution timeout and was stopped."
                )
                async with sm() as session:
                    await session.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(status="failed", error=error, finished_at=_utcnow_naive()),
                    )
                    await session.commit()
                await bus.publish(
                    "job.failed",
                    {
                        "id": job_id,
                        "user_id": handle_user_id,
                        "type": handle_type,
                        "error": error,
                    },
                )
                return
            async with sm() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(status="cancelled", finished_at=_utcnow_naive()),
                )
                await session.commit()
            await bus.publish(
                "job.cancelled",
                {"id": job_id, "user_id": handle_user_id, "reason": "running"},
            )
            return
        except Exception as exc:
            logging.exception("Job handler failed for %s", job_id)
            async with sm() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="failed",
                        error=str(exc),
                        finished_at=_utcnow_naive(),
                    )
                )
                await session.commit()
            await bus.publish(
                "job.failed",
                {
                    "id": job_id,
                    "user_id": handle_user_id,
                    "type": handle_type,
                    "error": str(exc),
                },
            )
            return
        finally:
            self._running_tasks.pop(job_id, None)
            self._running_handles.pop(job_id, None)
            self._cancel_tokens.pop(job_id, None)
            self._timed_out.discard(job_id)
            remaining = self._running_by_user.get(handle_user_id, 0) - 1
            if remaining > 0:
                self._running_by_user[handle_user_id] = remaining
            else:
                self._running_by_user.pop(handle_user_id, None)
            # Any offload child still registered here leaked past its handler
            # (handler raised/was cancelled mid-offload before `_spawn_offload`'s
            # own finally ran). Kill + forget so a stuck process can't outlive its
            # job and keep burning a core / holding a slot.
            for proc in list(self._offload_procs.pop(job_id, ())):
                _sigkill_proc(proc)
            # The job ended (any outcome) - stop its wall-clock reaper so a job that
            # finished within budget is never reaped after the fact.
            reaper = self._timeout_tasks.pop(job_id, None)
            if reaper is not None:
                reaper.cancel()
            # The job ended (any outcome) - cancel its grace watchdog so a
            # cooperatively-cancelled (or just-completed) subprocess worker is
            # never hard-killed after the fact, and a reused worker isn't poisoned.
            escalation = self._escalation_tasks.pop(job_id, None)
            if escalation is not None:
                escalation.cancel()

        async with sm() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status="completed", finished_at=_utcnow_naive()),
            )
            await session.commit()

        await bus.publish(
            "job.completed",
            {
                "id": job_id,
                "user_id": handle_user_id,
                "type": handle_type,
                "module_id": handle_module_id,
            },
        )


_runtime: JobRuntime | None = None


def set_job_runtime(rt: JobRuntime | None) -> None:
    global _runtime
    _runtime = rt


def get_job_runtime() -> JobRuntime:
    if _runtime is None:
        raise RuntimeError("Job runtime is not initialised - startup did not run.")
    return _runtime
