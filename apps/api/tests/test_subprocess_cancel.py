"""Cancellation of subprocess-isolated module jobs (kill + respawn).

Subprocess handlers - especially native/threaded ones like AgentSimulator's
pm4py/Mesa pipeline run via `asyncio.to_thread` - can't be stopped by the
cooperative `CancelToken`: a Python thread can't be interrupted and the
upstream call has no poll point. The only reliable stop is to SIGKILL the
worker's process group and respawn it. These tests cover that path:

  * `WireConnection.fail_all_pending` rejects in-flight RPCs when the peer dies,
  * `SubprocessBridge._kill_worker_group` kills the *whole* group (grandchildren
    included), not just the worker leader,
  * `SubprocessBridge.cancel_active` kills, fails pending calls, and respawns,
  * `JobRuntime.cancel` invokes the wired canceller for a running subprocess
    job and leaves in-process jobs (no `module_id`) on the cooperative path.

The real venv+worker+DataFrame round-trip stays a manual/Docker smoke test (it
needs `uv`), matching `test_module_python_version.py`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

from mate.api.modules.subprocess_host import SubprocessBridge, SubprocessHostError
from mate.api.modules.subprocess_worker import WireConnection
from mate.sdk.manifest import Manifest


def _manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "id": "canceltest",
            "name": "Cancel Test",
            "version": "0.1.0",
            "category": "advanced",
            "dependencies": {"python": {"isolation": "subprocess", "requires-python": ">=3.12"}},
        }
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_dead(pid: int, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while _alive(pid):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"pid {pid} still alive after {timeout}s")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_fail_all_pending_rejects_outstanding_requests() -> None:
    """When the worker dies, every awaited `send_request` future must be failed
    so the awaiting handler tasks raise instead of hanging forever. Futures
    already resolved are left untouched."""
    conn = WireConnection(reader=None, writer=None)  # type: ignore[arg-type]
    loop = asyncio.get_running_loop()
    f1: asyncio.Future = loop.create_future()
    f2: asyncio.Future = loop.create_future()
    done: asyncio.Future = loop.create_future()
    done.set_result("already")
    conn._pending = {1: f1, 2: f2, 3: done}

    conn.fail_all_pending(SubprocessHostError("worker gone"))

    assert conn._pending == {}
    for f in (f1, f2):
        with pytest.raises(SubprocessHostError, match="worker gone"):
            f.result()
    assert done.result() == "already"  # resolved future not clobbered


@pytest.mark.asyncio
async def test_kill_worker_group_kills_grandchildren(tmp_path: Path) -> None:
    """`_kill_worker_group` must take down the worker's whole process group, so
    a simulation that forked helper processes dies with it - not just the
    worker leader."""
    bridge = SubprocessBridge(_manifest(), tmp_path)
    # Parent (group leader, own session) spawns a child sleeper in the SAME
    # group, prints the child's pid, then blocks. killpg must reap both.
    parent_code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "sys.stdout.write(str(c.pid) + '\\n'); sys.stdout.flush()\n"
        "c.wait()\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    bridge._proc = proc
    assert proc.stdout is not None
    child_pid = int((await asyncio.wait_for(proc.stdout.readline(), timeout=5)).decode())

    assert _alive(proc.pid) and _alive(child_pid)

    bridge._kill_worker_group()

    await asyncio.wait_for(proc.wait(), timeout=5)
    assert proc.returncode is not None  # worker leader reaped
    await _wait_dead(child_pid)  # grandchild went down with the group


@pytest.mark.asyncio
async def test_cancel_active_kills_fails_pending_and_respawns(monkeypatch, tmp_path: Path) -> None:
    """`cancel_active` must (1) kill the running worker, (2) fail every in-flight
    call so sibling handler tasks don't hang, and (3) respawn a fresh worker so
    the module keeps serving."""
    bridge = SubprocessBridge(_manifest(), tmp_path)
    spawned: list[asyncio.subprocess.Process] = []

    async def fake_spawn() -> None:
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True
        )
        bridge._proc = p
        spawned.append(p)
        bridge._ready_evt.set()  # stand in for the worker's `ready` handshake

    monkeypatch.setattr(bridge, "_spawn_worker", fake_spawn)

    await fake_spawn()  # initial worker
    first = bridge._proc
    assert first is not None

    # A never-resolving future stands in for the in-flight `call` RPC.
    loop = asyncio.get_running_loop()
    pending: asyncio.Future = loop.create_future()

    class _FakeConn:
        def fail_all_pending(self, exc: BaseException) -> None:
            if not pending.done():
                pending.set_exception(exc)

    bridge._conn = _FakeConn()  # type: ignore[assignment]

    await bridge.cancel_active()

    # (2) in-flight call failed, not left hanging
    with pytest.raises(SubprocessHostError):
        pending.result()
    # (1) the running worker was killed
    await asyncio.wait_for(first.wait(), timeout=5)
    assert first.returncode is not None
    # (3) a fresh worker is back and ready
    await asyncio.wait_for(bridge._ready_evt.wait(), timeout=5)
    assert bridge._proc is not first
    assert len(spawned) == 2

    bridge._conn = None
    await bridge.stop()


@pytest.mark.asyncio
async def test_cancel_active_is_a_noop_during_teardown(monkeypatch, tmp_path: Path) -> None:
    """Once `stop()` has set the teardown flag, a late cancel must not resurrect
    the worker."""
    bridge = SubprocessBridge(_manifest(), tmp_path)
    bridge._stopping = True
    monkeypatch.setattr(
        bridge,
        "_spawn_worker",
        lambda: (_ for _ in ()).throw(AssertionError("must not respawn during teardown")),
    )
    await bridge.cancel_active()  # returns immediately, never spawns


async def _add_running_job(module_id: str | None) -> str:
    """Insert a running Job row and return its id."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job

    from .conftest import TEST_USER_ID

    job_id = uuid.uuid4().hex
    sm = get_sessionmaker()
    async with sm() as s:
        s.add(
            Job(
                id=job_id,
                user_id=TEST_USER_ID,
                type="module.agentsimulator.simulate" if module_id else "ingest.import",
                title="sim" if module_id else "import",
                status="running",
                module_id=module_id,
                payload_json={},
            )
        )
        await s.commit()
    return job_id


@pytest.mark.asyncio
async def test_runtime_cancel_soft_immediate_then_hard_after_grace() -> None:
    """A running subprocess job is asked to wind down cooperatively (soft hook,
    fired immediately); the hard kill+respawn hook only fires after the grace
    window elapses and the job is still running."""
    from mate.api.jobs.runtime import JobRuntime

    rt = JobRuntime()
    # Shrink the grace window so the escalation watchdog fires fast in the test.
    rt.settings = rt.settings.model_copy(update={"subprocess_cancel_grace_seconds": 0.05})
    soft_calls: list[tuple[str, str]] = []
    hard_calls: list[tuple[str, str]] = []

    async def soft(job_id: str, module_id: str) -> None:
        soft_calls.append((job_id, module_id))

    async def hard(job_id: str, module_id: str) -> None:
        hard_calls.append((job_id, module_id))

    rt.set_subprocess_soft_canceller(soft)
    rt.set_subprocess_hard_canceller(hard)

    sub_id = await _add_running_job("agentsimulator")
    # Stand in for the still-running host-side handler task so the watchdog has a
    # live target to escalate against.
    rt._running_tasks[sub_id] = asyncio.get_running_loop().create_future()  # type: ignore[assignment]

    assert await rt.cancel(sub_id) is True
    assert soft_calls == [(sub_id, "agentsimulator")]  # soft fired at once
    assert hard_calls == []  # still inside the grace window

    await asyncio.sleep(0.15)  # let the grace window elapse
    assert hard_calls == [(sub_id, "agentsimulator")]  # escalated to hard kill

    rt._running_tasks.pop(sub_id, None)
    await rt.stop()


@pytest.mark.asyncio
async def test_runtime_no_escalation_when_job_finished() -> None:
    """If the handler task is gone by the time the grace window elapses (it wound
    down cooperatively after the soft signal), the hard hook must NOT fire."""
    from mate.api.jobs.runtime import JobRuntime

    rt = JobRuntime()
    rt.settings = rt.settings.model_copy(update={"subprocess_cancel_grace_seconds": 0.05})
    hard_calls: list[tuple[str, str]] = []

    async def soft(job_id: str, module_id: str) -> None:
        return None

    async def hard(job_id: str, module_id: str) -> None:
        hard_calls.append((job_id, module_id))

    rt.set_subprocess_soft_canceller(soft)
    rt.set_subprocess_hard_canceller(hard)

    sub_id = await _add_running_job("agentsimulator")
    # No entry in _running_tasks → the handler is already gone; the watchdog must
    # short-circuit before calling hard.
    assert await rt.cancel(sub_id) is True
    await asyncio.sleep(0.15)
    assert hard_calls == []

    await rt.stop()


@pytest.mark.asyncio
async def test_runtime_cancel_inprocess_job_skips_subprocess_hooks() -> None:
    """An in-process job (no `module_id`) takes neither cancel hook - it stays on
    the cooperative token path."""
    from mate.api.jobs.runtime import JobRuntime

    rt = JobRuntime()
    soft_calls: list[tuple[str, str]] = []

    async def soft(job_id: str, module_id: str) -> None:
        soft_calls.append((job_id, module_id))

    rt.set_subprocess_soft_canceller(soft)

    plain_id = await _add_running_job(None)
    assert await rt.cancel(plain_id) is True
    assert soft_calls == []

    await rt.stop()
