"""Live system-resource sampler for *Admin → System*.

A single background task samples host CPU / RAM every
``settings.metrics_sample_interval_seconds`` into a bounded ring buffer, and on
each tick recomputes a "where is the load coming from" breakdown by joining the
job runtime's running-job registry with measured per-process CPU/RAM from psutil.

Why server-side (not sampled per request):
- psutil's per-process ``cpu_percent(interval=None)`` is a *delta since the last
  call on the same Process object*, so the handles must persist across ticks.
- A shared ring buffer means every admin viewer sees identical history + running
  max, and a freshly opened tab immediately shows the whole window.

Measurement reality (one uvicorn process): all ``in_process`` module work shares
the single API PID, so psutil can't split it per module. Subprocess modules have
their own PID and are measured exactly; in-process module load is *estimated* by
splitting the API process's measured CPU/RSS across the in-process sources that
currently have running jobs (flagged ``estimated``). A ``System / other`` slice
absorbs the remainder up to the host total, so each pie sums to the live total.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import psutil
import structlog
from pydantic import BaseModel

if TYPE_CHECKING:
    from mate.api.config import Settings
    from mate.api.jobs.runtime import JobRuntime, RunningJobInfo
    from mate.api.modules.loader import ModuleLoader

log = structlog.get_logger(__name__)

BreakdownSource = Literal["module_subprocess", "module_inproc", "api_baseline", "system", "idle"]


# --------------------------------------------------------------------------- #
# Output schema (owned here so the route imports it without a circular dep).
# --------------------------------------------------------------------------- #
class PerCoreOut(BaseModel):
    index: int
    current_pct: float
    max_pct: float


class CpuOut(BaseModel):
    current_pct: float
    max_pct: float
    cores_logical: int
    cores_physical: int
    per_core: list[PerCoreOut]


class MemoryOut(BaseModel):
    used_bytes: int
    total_bytes: int
    max_used_bytes: int
    current_pct: float


class SampleOut(BaseModel):
    ts: float
    cpu_pct: float
    mem_used_bytes: int


class BreakdownOut(BaseModel):
    label: str
    module_id: str | None
    source: BreakdownSource
    # CPU breakdown: percent of the host (0-100). Memory breakdown: bytes.
    value: float
    estimated: bool


class RunningJobOut(BaseModel):
    id: str
    module_id: str | None
    user_id: str
    type: str
    title: str


class SystemResourcesOut(BaseModel):
    cpu: CpuOut
    memory: MemoryOut
    history: list[SampleOut]
    cpu_breakdown: list[BreakdownOut]
    memory_breakdown: list[BreakdownOut]
    running_jobs: list[RunningJobOut]
    sample_interval_seconds: float
    history_window_seconds: float


# --------------------------------------------------------------------------- #
# Internal sample + slice records.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Sample:
    ts: float
    cpu_pct: float
    mem_used_bytes: int


@dataclass(frozen=True)
class _Slice:
    label: str
    module_id: str | None
    source: BreakdownSource
    value: float
    estimated: bool


def _type_label(job_type: str) -> str:
    """Friendly label for a module-less job type, e.g. ``event_log.import`` →
    ``Import``."""
    return job_type.rsplit(".", 1)[-1].replace("_", " ").title() or job_type


class ResourceSampler:
    """Background CPU/RAM sampler + breakdown, behind a process-wide singleton."""

    def __init__(self, settings: Settings, *, loader: ModuleLoader, runtime: JobRuntime) -> None:
        self._loader = loader
        self._runtime = runtime
        self._interval = float(settings.metrics_sample_interval_seconds)
        self._maxlen = int(settings.metrics_history_samples)

        self._cpu_count_logical = psutil.cpu_count(logical=True) or 1
        self._cpu_count_physical = psutil.cpu_count(logical=False) or self._cpu_count_logical
        self._total_mem = int(psutil.virtual_memory().total)
        self._api_proc = psutil.Process(os.getpid())
        # Persistent per-PID handles for subprocess workers (deltas need the same
        # object across ticks). Reconciled against the live worker set each tick.
        self._proc_handles: dict[int, psutil.Process] = {}

        # All mutable state below is read by snapshot() on the event loop and
        # written by _sample_once() in a worker thread - guard with a lock.
        self._lock = threading.Lock()
        self._history: deque[_Sample] = deque(maxlen=self._maxlen)
        self._cpu_max = 0.0
        self._mem_max = 0
        self._percpu_current: list[float] = [0.0] * self._cpu_count_logical
        self._percpu_max: list[float] = [0.0] * self._cpu_count_logical
        self._cpu_breakdown: list[_Slice] = []
        self._mem_breakdown: list[_Slice] = []
        self._running: list[RunningJobOut] = []

        self._task: asyncio.Task[None] | None = None
        self._started = False

    # ----- lifecycle ------------------------------------------------------- #
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        # Prime psutil so the first real sample carries a true delta (the very
        # first call to any cpu_percent variant always returns 0.0/zeros).
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True)
        self._safe_cpu(self._api_proc)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._started = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                # Snapshot loader/runtime state on the event loop (don't touch
                # their in-memory maps from the worker thread), then do the
                # psutil /proc reads off-loop.
                running = self._runtime.running_jobs()
                bridge_pids = {
                    mid: b.worker_pid() for mid, b in self._loader.subprocess_bridges().items()
                }
                isolation = {
                    m.id: m.dependencies.python.isolation for m in self._loader.manifests()
                }
                await asyncio.to_thread(self._sample_once, running, bridge_pids, isolation)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("system.metrics.sample_failed", exc_info=True)

    # ----- sampling (worker thread) ---------------------------------------- #
    def _sample_once(
        self,
        running: list[RunningJobInfo],
        bridge_pids: dict[str, int | None],
        isolation: dict[str, str],
    ) -> None:
        cpu = float(psutil.cpu_percent(interval=None))
        percpu = [float(v) for v in psutil.cpu_percent(percpu=True)]
        vm = psutil.virtual_memory()
        mem_used = int(vm.used)
        now = time.time()

        cpu_slices, mem_slices = self._compute_breakdown(
            cpu, mem_used, running, bridge_pids, isolation
        )
        running_out = [
            RunningJobOut(
                id=j.id, module_id=j.module_id, user_id=j.user_id, type=j.type, title=j.title
            )
            for j in running
        ]

        with self._lock:
            self._history.append(_Sample(now, cpu, mem_used))
            self._cpu_max = max(self._cpu_max, cpu)
            self._mem_max = max(self._mem_max, mem_used)
            if len(self._percpu_max) != len(percpu):
                self._percpu_max = [0.0] * len(percpu)
            self._percpu_current = percpu
            for i, v in enumerate(percpu):
                self._percpu_max[i] = max(self._percpu_max[i], v)
            self._cpu_breakdown = cpu_slices
            self._mem_breakdown = mem_slices
            self._running = running_out

    def _compute_breakdown(
        self,
        host_cpu: float,
        host_mem_used: int,
        running: list[RunningJobInfo],
        bridge_pids: dict[str, int | None],
        isolation: dict[str, str],
    ) -> tuple[list[_Slice], list[_Slice]]:
        cores = self._cpu_count_logical

        # Drop handles for workers that have gone away (respawn → new PID).
        alive = {pid for pid in bridge_pids.values() if pid is not None}
        for dead in [p for p in self._proc_handles if p not in alive]:
            self._proc_handles.pop(dead, None)

        # API process = the whole in-process world (uvicorn + all in_process
        # modules + event loop). Per-process cpu_percent can exceed 100 on
        # multi-core; divide by core count to share the system 0-100 scale.
        api_cpu = self._safe_cpu(self._api_proc) / cores
        api_rss = self._safe_rss(self._api_proc)

        cpu_slices: list[_Slice] = []
        mem_slices: list[_Slice] = []

        # 1) Subprocess module workers - measured exactly, per module.
        sub_cpu_total = 0.0
        sub_rss_total = 0
        for module_id, pid in sorted(bridge_pids.items()):
            if pid is None:
                continue
            handle = self._handle(pid)
            if handle is None:
                continue
            c = self._safe_cpu(handle) / cores
            r = self._safe_rss(handle)
            sub_cpu_total += c
            sub_rss_total += r
            cpu_slices.append(_Slice(module_id, module_id, "module_subprocess", c, False))
            mem_slices.append(_Slice(module_id, module_id, "module_subprocess", r, False))

        # 2) In-process jobs split the API process's measured CPU/RSS evenly
        #    across the distinct in-process sources running (estimated). With
        #    none running, the whole API slice is the baseline.
        inproc: list[tuple[str | None, str]] = []
        seen: set[str] = set()
        for j in running:
            if isolation.get(j.module_id or "", "in_process") == "subprocess":
                continue
            label = j.module_id or _type_label(j.type)
            if label in seen:
                continue
            seen.add(label)
            inproc.append((j.module_id, label))

        if inproc:
            n = len(inproc)
            for module_id, label in inproc:
                cpu_slices.append(_Slice(label, module_id, "module_inproc", api_cpu / n, True))
                mem_slices.append(_Slice(label, module_id, "module_inproc", api_rss / n, True))
        else:
            cpu_slices.append(_Slice("API server", None, "api_baseline", api_cpu, False))
            mem_slices.append(_Slice("API server", None, "api_baseline", float(api_rss), False))

        # 3) System / other - everything not attributed above (Keycloak, web,
        #    Caddy, kernel, cache, CPU-offload pool), clamped non-negative.
        other_cpu = max(0.0, host_cpu - api_cpu - sub_cpu_total)
        other_mem = max(0, host_mem_used - api_rss - sub_rss_total)
        cpu_slices.append(_Slice("System / other", None, "system", other_cpu, False))
        mem_slices.append(_Slice("System / other", None, "system", float(other_mem), False))

        # 4) Idle / free capacity so each pie represents the whole host (CPU to
        #    100%, memory to total). The UI renders this remainder blank.
        cpu_slices.append(_Slice("Idle", None, "idle", max(0.0, 100.0 - host_cpu), False))
        free_mem = max(0, self._total_mem - host_mem_used)
        mem_slices.append(_Slice("Free", None, "idle", float(free_mem), False))

        return cpu_slices, mem_slices

    # ----- psutil helpers -------------------------------------------------- #
    def _handle(self, pid: int) -> psutil.Process | None:
        handle = self._proc_handles.get(pid)
        if handle is None:
            try:
                handle = psutil.Process(pid)
                handle.cpu_percent(None)  # prime; reads 0.0 this tick, true delta next
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None
            self._proc_handles[pid] = handle
        return handle

    @staticmethod
    def _safe_cpu(handle: psutil.Process) -> float:
        try:
            return float(handle.cpu_percent(None))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    @staticmethod
    def _safe_rss(handle: psutil.Process) -> int:
        try:
            return int(handle.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0

    # ----- read (event loop) ----------------------------------------------- #
    def snapshot(self) -> SystemResourcesOut:
        with self._lock:
            latest = self._history[-1] if self._history else None
            current_cpu = latest.cpu_pct if latest is not None else 0.0
            used = latest.mem_used_bytes if latest is not None else 0
            history = [
                SampleOut(ts=s.ts, cpu_pct=round(s.cpu_pct, 1), mem_used_bytes=s.mem_used_bytes)
                for s in self._history
            ]
            per_core = [
                PerCoreOut(
                    index=i,
                    current_pct=round(v, 1),
                    max_pct=round(self._percpu_max[i] if i < len(self._percpu_max) else 0.0, 1),
                )
                for i, v in enumerate(self._percpu_current)
            ]
            cpu_breakdown = [self._to_out(s, as_int=False) for s in self._cpu_breakdown]
            mem_breakdown = [self._to_out(s, as_int=True) for s in self._mem_breakdown]
            running = list(self._running)
            cpu_max = self._cpu_max
            mem_max = self._mem_max

        return SystemResourcesOut(
            cpu=CpuOut(
                current_pct=round(current_cpu, 1),
                max_pct=round(cpu_max, 1),
                cores_logical=self._cpu_count_logical,
                cores_physical=self._cpu_count_physical,
                per_core=per_core,
            ),
            memory=MemoryOut(
                used_bytes=used,
                total_bytes=self._total_mem,
                max_used_bytes=mem_max,
                current_pct=round(used / self._total_mem * 100, 1) if self._total_mem else 0.0,
            ),
            history=history,
            cpu_breakdown=cpu_breakdown,
            memory_breakdown=mem_breakdown,
            running_jobs=running,
            sample_interval_seconds=self._interval,
            history_window_seconds=round(self._interval * self._maxlen, 1),
        )

    @staticmethod
    def _to_out(s: _Slice, *, as_int: bool) -> BreakdownOut:
        value = float(int(s.value)) if as_int else round(s.value, 1)
        return BreakdownOut(
            label=s.label,
            module_id=s.module_id,
            source=s.source,
            value=value,
            estimated=s.estimated,
        )


_sampler: ResourceSampler | None = None


def set_resource_sampler(sampler: ResourceSampler | None) -> None:
    global _sampler
    _sampler = sampler


def get_resource_sampler() -> ResourceSampler:
    if _sampler is None:
        raise RuntimeError("Resource sampler is not initialised - startup did not run.")
    return _sampler
