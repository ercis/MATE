"""One-shot per-job worker for an in-process module's `execution: worker` jobs.

Runs a single `@job`/`@on_event` handler invocation in a throwaway child process
so the platform can SIGKILL it - and its whole tree (joblib/loky pools, ``java``,
MINERful shells) - the instant the job is cancelled, instead of leaving an
uncancellable thread burning a core inside the API (the `asyncio.to_thread`
path). It reuses `subprocess_worker.py`'s IPC and the shared `ctx_rpc`
dispatcher; the only differences from a persistent `SubprocessBridge` are:

  - it runs under the **platform** interpreter with the module's venv
    site-packages layered (the in-process import model, so `inherit:`ed platform
    deps resolve - never the module's own venv python, which lacks them);
  - it serves exactly one call, then disposes;
  - it registers with the child supervisor under the **job_id**, so the existing
    cancel path (`runtime.cancel` → `_kill_offload` → `supervisor.kill_job`)
    SIGKILLs it with no extra wiring, while the cooperative token still reaches
    the handler (every ctx.* RPC is a cancel poll point via `is_cancelled`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

import structlog

from mate.api.jobs.supervisor import ChildHandle, get_child_supervisor
from mate.api.modules.ctx_rpc import build_ctx_meta, jsonify, make_ctx_dispatcher
from mate.api.modules.job_logs import get_job_log_buffer
from mate.api.modules.subprocess_worker import RPC_STREAM_LIMIT, WireConnection
from mate.sdk import Cancelled as SdkCancelled

log = structlog.get_logger(__name__)

_WORKER_SCRIPT = Path(__file__).with_name("subprocess_worker.py")
# One ctx lives for this worker's single call; the token only routes RPCs back.
_CALL_TOKEN = "job"
# Generous: a cold worker imports the module + its heavy deps (e.g. cv4cdd's
# TensorFlow) before signalling ready. Still bounded so a broken worker can't
# wedge the job slot forever (the wall-clock reaper is the outer backstop).
_READY_TIMEOUT_S = 120.0


class JobWorkerError(RuntimeError):
    pass


class JobWorker:
    def __init__(self, *, module_id: str, folder: str, site_packages: str, job_id: str) -> None:
        self.module_id = module_id
        self.folder = folder
        self.site_packages = site_packages
        self.job_id = job_id
        self._socket_dir = Path(tempfile.mkdtemp(prefix=f"ff-jobw-{module_id}-"))
        self._socket_path = self._socket_dir / "rpc.sock"
        self._server: asyncio.base_events.Server | None = None
        self._conn: WireConnection | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._ready = asyncio.Event()
        self._ctx: Any = None
        self._handle: ChildHandle | None = None
        self._pipe_tasks: list[asyncio.Task[None]] = []

    def _ctx_for(self, _token: str) -> Any:
        return self._ctx

    def _is_cancelled(self, _token: str) -> bool:
        try:
            return bool(self._ctx.is_cancelled())
        except Exception:
            return False

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = WireConnection(reader, writer)
        self._conn = conn
        self._writer = writer
        for method, handler in make_ctx_dispatcher(self._ctx_for, self._is_cancelled).items():
            conn.register(method, handler)
        conn.register("ready", self._on_ready)
        try:
            await conn.run()
        finally:
            # Worker exited (done, crash, or SIGKILL on cancel). Release the
            # awaiting `call` so `run()` doesn't hang, and close our half of the
            # connection so `server.wait_closed()` in `_dispose` can complete -
            # an abruptly-SIGKILLed worker never closes it for us.
            conn.fail_all_pending(JobWorkerError(f"Worker for job {self.job_id} exited."))
            writer.close()

    def _on_ready(self, _params: dict[str, Any]) -> bool:
        self._ready.set()
        return True

    async def _drain_pipe(self, stream: Any, label: str) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            log.info(
                "modules.job_worker.output",
                module_id=self.module_id,
                job_id=self.job_id,
                stream=label,
                line=text,
            )
            # Most module authors' compute code talks to stdout/stderr directly
            # (print, TF/joblib chatter) rather than `ctx.logger` - mirror it into
            # the per-job ring too, or the admin Jobs tab log panel stays empty
            # for exactly the modules worth watching mid-run.
            get_job_log_buffer().append(
                self.job_id,
                "warning" if label == "stderr" else "info",
                text,
                {"stream": label},
            )

    async def run(self, attr: str, ctx: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self._ctx = ctx
        try:
            self._server = await asyncio.start_unix_server(
                self._on_connect, path=str(self._socket_path), limit=RPC_STREAM_LIMIT
            )
            os.chmod(self._socket_path, 0o600)
            cmd = [
                sys.executable,
                str(_WORKER_SCRIPT),
                str(self._socket_path),
                self.folder,
                self.site_packages,
            ]
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=True,
            )
            # Own the child immediately so cancel/shutdown reaps it even if the
            # handshake below fails.
            self._handle = get_child_supervisor().register(
                ChildHandle(
                    pid=self._proc.pid,
                    kind="job_worker",
                    job_id=self.job_id,
                    module_id=self.module_id,
                )
            )
            self._pipe_tasks = [
                asyncio.create_task(self._drain_pipe(self._proc.stderr, "stderr")),
                asyncio.create_task(self._drain_pipe(self._proc.stdout, "stdout")),
            ]
            # Wait for the worker to signal ready, but bail the moment it dies
            # instead - a worker SIGKILLed (cancel) or crashed (bad import)
            # before ready must not hang `run()` for the full ready timeout.
            ready_task = asyncio.create_task(self._ready.wait())
            dead_task = asyncio.create_task(self._proc.wait())
            done, pending = await asyncio.wait(
                {ready_task, dead_task},
                timeout=_READY_TIMEOUT_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if not self._ready.is_set():
                if self._is_cancelled(_CALL_TOKEN):
                    raise SdkCancelled()
                if dead_task in done:
                    raise JobWorkerError(
                        f"Job worker for {self.module_id!r} exited before ready "
                        f"(rc={self._proc.returncode})."
                    )
                raise JobWorkerError(
                    f"Job worker for {self.module_id!r} did not start in {_READY_TIMEOUT_S:.0f}s."
                )
            if self._conn is None:
                raise JobWorkerError("Job worker connected but no RPC channel.")
            try:
                return await self._conn.send_request(
                    "call",
                    {
                        "handler": attr,
                        "ctx_token": _CALL_TOKEN,
                        "ctx": build_ctx_meta(ctx),
                        "args": [jsonify(a) for a in args],
                        "kwargs": {k: jsonify(v) for k, v in kwargs.items()},
                    },
                )
            except Exception:
                # The worker died mid-call. If a cancel was requested, that's the
                # expected hard stop - surface it as the SDK cancel so the runtime
                # records `cancelled`, not `failed`.
                if self._is_cancelled(_CALL_TOKEN):
                    raise SdkCancelled() from None
                raise
        finally:
            await self._dispose()

    async def _dispose(self) -> None:
        get_child_supervisor().unregister(self._handle)
        for t in self._pipe_tasks:
            t.cancel()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        if proc is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        shutil.rmtree(self._socket_dir, ignore_errors=True)
