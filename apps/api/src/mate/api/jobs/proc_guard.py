"""Parent-death guard: a spawned child self-terminates — together with its
whole process group — the instant the platform parent dies, even on a SIGKILL
or crash where no graceful shutdown runs.

Two mechanisms, used together:

  - **Linux ``prctl(PR_SET_PDEATHSIG, SIGKILL)``** — the kernel SIGKILLs *this*
    process when its parent dies. Instant, but it only signals the immediate
    process (not the group) and it tracks the *direct* parent. Under
    multiprocessing ``forkserver`` the direct parent of an offload child is the
    fork-server, not the API, so PDEATHSIG alone is not enough there — hence the
    death-fd watchdog below.

  - **A watchdog thread** that fires when the parent goes away, then kills the
    whole process group (``killpg``) so grandchildren the payload spawned
    (joblib/loky pools, ``java``, MINERful shells) die too. PDEATHSIG signals one
    process; the watchdog is what guarantees the *group* dies. Two triggers:
      * ``death_conn`` — the read end of a pipe whose write end only the API
        holds. A blocking ``recv()`` returns ``EOFError`` the moment the API
        dies (or closes it). This tracks the API **directly**, so it is correct
        under both ``spawn`` and ``forkserver`` (it does not care about the
        intermediate fork-server). Used by CPU-offload children.
      * else ``os.getppid()`` polling — fires when the direct parent dies and
        the process is reparented. Correct only when the API is the direct
        parent (subprocess workers, directly-spawned children).

This module imports **nothing from ``mate``** (stdlib only) so it is safe to
import from an offload child on the platform interpreter without dragging in the
heavy app graph. The subprocess worker runs on the *module's* own venv where
``mate.api`` is not importable at all, so it inlines an equivalent guard
(`subprocess_worker._install_parent_death_guard`) — keep the two in sync.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from typing import Any

# linux/include/uapi/linux/prctl.h
_PR_SET_PDEATHSIG = 1


def set_pdeathsig_sigkill() -> None:
    """Linux only: ask the kernel to SIGKILL this process when its parent dies.

    No-op on non-Linux platforms or if the ``prctl`` call is unavailable/fails —
    the watchdog thread is the portable backstop.
    """
    if sys.platform != "linux":
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except Exception:
        pass


def kill_own_group_and_exit(code: int = 137) -> None:
    """SIGKILL this process's whole group (reaping grandchildren), then exit.

    Only ``killpg`` when we actually lead our own group
    (``getpgrp() == getpid()`` — true after ``setsid`` / ``start_new_session``);
    otherwise ``killpg(0)`` would hit the parent's group, which could include the
    API process, so fall back to exiting only ourselves. Mirrors the host-side
    ``_sigkill_proc`` safety rule.
    """
    try:
        if os.getpgrp() == os.getpid():
            os.killpg(0, signal.SIGKILL)
    except Exception:
        pass
    os._exit(code)


def _watch_death_fd(conn: Any) -> None:
    # We never send on this pipe; recv() blocks until the API closes the write
    # end (clean teardown) or dies (crash/SIGKILL) → EOFError. Any other error
    # means the channel is gone, which we treat the same way: assume the parent
    # is unreachable and tear down.
    with contextlib.suppress(Exception):
        conn.recv()
    kill_own_group_and_exit()


def _watch_getppid(original_ppid: int, interval: float = 0.5) -> None:
    while True:
        try:
            if os.getppid() != original_ppid:
                break
        except Exception:
            break
        time.sleep(interval)
    kill_own_group_and_exit()


def install_parent_death_guard(death_conn: Any | None = None) -> None:
    """Install both guards for the current child process.

    Call **once**, as early as possible, and **after** the child has its own
    session/group (``os.setsid()`` / ``start_new_session=True``) so the
    watchdog's group-kill targets the child's own tree, never the API's.

    ``death_conn``: read end of a parent-held pipe (preferred — tracks the API
    directly, ``forkserver``-safe). When ``None``, falls back to ``getppid()``
    polling (correct only when the API is the direct parent).
    """
    set_pdeathsig_sigkill()
    if death_conn is not None:
        t = threading.Thread(
            target=_watch_death_fd, args=(death_conn,), daemon=True, name="ff-parent-death"
        )
        t.start()
        return
    # getppid path: handle the race where the parent already died between
    # fork/exec and now (we'd otherwise capture ppid==1 and never fire).
    original_ppid = os.getppid()
    if original_ppid == 1:
        kill_own_group_and_exit()
    t = threading.Thread(
        target=_watch_getppid, args=(original_ppid,), daemon=True, name="ff-parent-death"
    )
    t.start()
