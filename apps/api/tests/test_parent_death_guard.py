"""Phase 1: a spawned child self-terminates when the platform parent dies.

Covers the offload death-pipe path end-to-end: the child installs the
parent-death guard with the read end of a pipe whose write end only the parent
holds; dropping that write end (≈ a hard API death) must SIGKILL the child's
whole group within a beat, so an offload can never orphan into a CPU-burning
zombie. See `mate.api.jobs.proc_guard`.
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"), reason="POSIX process-group semantics only"
)


def _guarded_child(death_r, ready_w) -> None:
    # Mirror offload_child_main: lead our own group, then install the guard so
    # its group-kill targets us, not the test runner.
    with contextlib.suppress(OSError):
        os.setsid()
    from mate.api.jobs.proc_guard import install_parent_death_guard

    install_parent_death_guard(death_r)
    ready_w.send(os.getpid())
    ready_w.close()
    while True:  # spin until the guard kills us
        time.sleep(0.05)


def test_death_pipe_guard_kills_child_when_parent_drops_channel() -> None:
    ctx = mp.get_context("spawn")
    death_r, death_w = ctx.Pipe(duplex=False)
    ready_r, ready_w = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_guarded_child, args=(death_r, ready_w), daemon=False)
    proc.start()
    death_r.close()  # only the parent keeps the write end
    ready_w.close()

    assert ready_r.poll(timeout=20), "child never signalled ready"
    ready_r.recv()
    assert proc.is_alive()

    # Drop the death channel (≈ the API dying without graceful teardown). The
    # child's watchdog sees EOF and SIGKILLs its own group.
    death_w.close()
    proc.join(timeout=5)
    assert not proc.is_alive(), "child survived parent-channel close (orphan!)"
    assert proc.exitcode != 0
