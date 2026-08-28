"""The platform's single owner of every OS child it spawns — the "main worker"
that controls all sub-workers.

CPU-offload children, subprocess module workers and (Phase 4) per-job workers
all register here. That gives the platform two things the previously-scattered
tracking (`JobRuntime._offload_procs` per job, `SubprocessBridge._proc` per
module, untracked module grandchildren) could not:

  - ``kill_all()`` — one authoritative "stop everything" for shutdown, so no
    child can outlive the platform regardless of which subsystem spawned it.
  - ``kill_job(job_id)`` — hard-stop a job's whole process tree on demand
    (admin "Kill"), bypassing any cooperative grace.

Every child we spawn leads its own process group (``os.setsid()`` for offload
children, ``start_new_session=True`` for workers), so ``pgid == pid`` and a
single ``killpg(pid)`` reaps the child **and** every grandchild it forked
(joblib/loky pools, ``java``, MINERful shells) that didn't itself ``setsid``.

All registry mutations happen on the asyncio event-loop thread (offload spawn,
worker spawn, cancel, shutdown), so no locking is needed; the kills themselves
are plain signals.
"""

from __future__ import annotations

import contextlib
import os
import signal
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)


@dataclass(eq=False)
class ChildHandle:
    """A spawned child the platform owns. Identified by pid; killed by group."""

    pid: int
    kind: str  # "offload" | "subprocess_worker" | "job_worker"
    job_id: str | None = None
    module_id: str | None = None


def kill_process_group(pid: int) -> None:
    """SIGKILL the process group led by ``pid``, reaping grandchildren too.

    Our children ``setsid``/``start_new_session`` so their group id equals their
    pid: ``killpg(pid)`` hits exactly that group. If the child never became a
    group leader (``setsid`` failed) no group has id == pid → ``killpg`` raises
    ``ProcessLookupError`` and we fall back to a single-process ``kill``. The API
    process's own group id can never equal a child's pid, so this can never
    signal the platform itself — the same safety rule as ``runtime._sigkill_proc``.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)


class ChildProcessSupervisor:
    def __init__(self) -> None:
        self._children: set[ChildHandle] = set()

    def register(self, handle: ChildHandle) -> ChildHandle:
        self._children.add(handle)
        return handle

    def unregister(self, handle: ChildHandle | None) -> None:
        if handle is not None:
            self._children.discard(handle)

    def kill_job(self, job_id: str) -> int:
        """Hard-kill every registered child of ``job_id`` (its whole tree)."""
        victims = [h for h in self._children if h.job_id == job_id]
        for h in victims:
            kill_process_group(h.pid)
            self._children.discard(h)
        if victims:
            log.info("child_supervisor.kill_job", job_id=job_id, count=len(victims))
        return len(victims)

    def kill_all(self) -> int:
        """Hard-kill every registered child. The shutdown nuke."""
        victims = list(self._children)
        for h in victims:
            kill_process_group(h.pid)
        self._children.clear()
        if victims:
            log.info("child_supervisor.kill_all", count=len(victims))
        return len(victims)

    def snapshot(self) -> list[ChildHandle]:
        """Live children, for admin diagnostics."""
        return list(self._children)


_supervisor: ChildProcessSupervisor | None = None


def get_child_supervisor() -> ChildProcessSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = ChildProcessSupervisor()
    return _supervisor


def set_child_supervisor(supervisor: ChildProcessSupervisor | None) -> None:
    """Swap the process-global supervisor (tests; teardown)."""
    global _supervisor
    _supervisor = supervisor
