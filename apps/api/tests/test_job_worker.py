"""Phase 4: in-process `execution: worker` jobs run in a killable child process.

Real end-to-end (no `uv` needed - the worker runs under the platform interpreter
that's already running the tests): a tiny module is spawned in a JobWorker, a
handler round-trips a `ctx.progress` RPC and returns a value, and a sync busy
handler is hard-killed mid-run via the supervisor (what `runtime.cancel` does),
proving the cancel that the `asyncio.to_thread` path could never deliver.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from mate.api.jobs.supervisor import get_child_supervisor
from mate.api.modules.job_worker import JobWorker
from mate.sdk import Cancelled as SdkCancelled

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"), reason="POSIX process-group semantics only"
)

_MODULE_PY = """
from mate.sdk.module import Module
from mate.sdk.decorators import job


class WTest(Module):
    id = "wtest"
    name = "Worker Test"
    version = "0.1.0"

    @job()
    async def echo(self, ctx, value):
        await ctx.progress.update(0.5, "halfway")
        return {"echoed": value}

    @job()
    async def cache_blob(self, ctx):
        # Non-JSON bytes (a stand-in for cv4cdd's overlay PNGs) must survive the
        # worker<->host bridge via the pickle-file envelope.
        blob = bytes(range(256)) * 4
        await ctx.cache.set("blob", blob)
        got = await ctx.cache.get("blob")
        return {"len": len(got), "match": got == blob}

    @job()
    def spin(self, ctx):
        import time

        # Signal that the (uncancellable) handler is actually running, then spin.
        (ctx.workdir / "started").write_text("1")
        while True:
            time.sleep(0.02)
"""


class _FakeProgress:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def update(self, current, message=None, *, total=None, stage=None) -> None:
        self.calls.append(current)


class _FakeConfig:
    def __init__(self) -> None:
        self.value: dict[str, Any] = {}


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def set(self, key: str, value: Any) -> None:
        self.store[key] = value

    async def get(self, key: str) -> Any:
        return self.store.get(key)


class _FakeCtx:
    """Minimal host-side ModuleContext the JobWorker dispatches RPCs against."""

    def __init__(self, workdir: Path) -> None:
        self.log_id = "log1"
        self.module_id = "wtest"
        self.workdir = workdir
        self.config = _FakeConfig()
        self.progress = _FakeProgress()
        self.cache = _FakeCache()
        self.registry = None
        self._cancelled = False

    def is_cancelled(self) -> bool:
        return self._cancelled


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _module_dir(tmp_path: Path) -> Path:
    d = tmp_path / "wtest"
    d.mkdir()
    (d / "module.py").write_text(_MODULE_PY)
    return d


async def _wait_registered(job_id: str, timeout: float = 30.0) -> int:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        for h in get_child_supervisor().snapshot():
            if h.job_id == job_id and h.kind == "job_worker":
                return h.pid
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("job worker never registered")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_job_worker_runs_handler_and_round_trips_ctx(tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path)
    worker = JobWorker(
        module_id="wtest", folder=str(_module_dir(tmp_path)), site_packages="", job_id="J1"
    )
    result = await worker.run("echo", ctx, ("hello",), {})
    assert result == {"echoed": "hello"}
    assert ctx.progress.calls == [0.5]  # the ctx.progress RPC reached the host
    # Worker disposed: nothing left registered for this job.
    assert not any(h.job_id == "J1" for h in get_child_supervisor().snapshot())


@pytest.mark.asyncio
async def test_job_worker_caches_bytes_round_trip(tmp_path: Path) -> None:
    # cv4cdd caches PNG bytes via ctx.cache.set; plain JSON-RPC can't carry
    # bytes (the original "Object of type bytes is not JSON serializable"). The
    # pickle-file envelope must round-trip them worker -> host -> worker.
    ctx = _FakeCtx(tmp_path)
    worker = JobWorker(
        module_id="wtest", folder=str(_module_dir(tmp_path)), site_packages="", job_id="J3"
    )
    result = await worker.run("cache_blob", ctx, (), {})
    assert result == {"len": 1024, "match": True}
    # The host stored the real bytes (unenveloped), not the envelope dict.
    assert ctx.cache.store["blob"] == bytes(range(256)) * 4


@pytest.mark.asyncio
async def test_job_worker_sync_handler_hard_killed_on_cancel(tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path)
    worker = JobWorker(
        module_id="wtest", folder=str(_module_dir(tmp_path)), site_packages="", job_id="J2"
    )
    task = asyncio.create_task(worker.run("spin", ctx, (), {}))
    pid = await _wait_registered("J2")
    assert _alive(pid)

    # Wait until the sync handler is actually running (it writes a sentinel),
    # so we're hard-killing a live uncancellable busy loop - not a worker still
    # importing.
    started = ctx.workdir / "started"
    deadline = asyncio.get_running_loop().time() + 30
    while not started.exists():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("spin handler never started")
        await asyncio.sleep(0.05)

    # What runtime.cancel() does for a running job: flip the cooperative token
    # and SIGKILL the job's process tree via the supervisor.
    ctx._cancelled = True
    assert get_child_supervisor().kill_job("J2") == 1

    # The uncancellable busy loop is stopped because the worker process is gone;
    # run() surfaces it as the SDK cancel (→ recorded `cancelled`, not `failed`).
    with pytest.raises(SdkCancelled):
        await asyncio.wait_for(task, timeout=10)

    deadline = asyncio.get_running_loop().time() + 5
    while _alive(pid):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("worker survived the kill")
        await asyncio.sleep(0.05)
