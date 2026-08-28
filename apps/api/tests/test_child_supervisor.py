"""The ChildProcessSupervisor SIGKILLs registered children by process group."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from mate.api.jobs.supervisor import ChildHandle, ChildProcessSupervisor

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"), reason="POSIX process-group semantics only"
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _spawn_sleeper() -> subprocess.Popen:
    # Own session/group (pgid == pid) so killpg(pid) reaps it, mirroring how the
    # platform spawns offload children (setsid) and workers (start_new_session).
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )


def test_kill_job_kills_only_that_jobs_children() -> None:
    sup = ChildProcessSupervisor()
    a, b = _spawn_sleeper(), _spawn_sleeper()
    try:
        sup.register(ChildHandle(pid=a.pid, kind="offload", job_id="A"))
        sup.register(ChildHandle(pid=b.pid, kind="offload", job_id="B"))

        assert sup.kill_job("A") == 1
        assert a.wait(timeout=5) is not None  # killed + reaped
        assert _pid_alive(b.pid)  # other job untouched
        assert len(sup.snapshot()) == 1  # the dead handle was dropped
    finally:
        b.kill()
        b.wait(timeout=5)


def test_kill_all_kills_every_child() -> None:
    sup = ChildProcessSupervisor()
    procs = [_spawn_sleeper() for _ in range(3)]
    try:
        for i, p in enumerate(procs):
            sup.register(ChildHandle(pid=p.pid, kind="offload", job_id=str(i)))

        assert sup.kill_all() == 3
        for p in procs:
            assert p.wait(timeout=5) is not None
        assert sup.snapshot() == []
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def test_unregister_keeps_child_alive() -> None:
    sup = ChildProcessSupervisor()
    p = _spawn_sleeper()
    try:
        h = sup.register(ChildHandle(pid=p.pid, kind="offload", job_id="X"))
        sup.unregister(h)
        assert sup.kill_job("X") == 0  # nothing registered → no-op
        assert _pid_alive(p.pid)
    finally:
        p.kill()
        p.wait(timeout=5)
