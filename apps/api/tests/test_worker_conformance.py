"""Cross-runtime worker conformance suite (modules/PROTOCOL.md §10).

Drives a REAL `SubprocessBridge` + worker process for every bundled runtime -
the Python fixture (tests/fixtures/conformance_worker/, run on the platform
interpreter so no venv build is needed) and the JVM fixture
(packages/module-sdk-jvm/conformance-fixture, `make sdk-jvm`) - through one
shared test matrix: every ctx.* method, soft + hard cancellation, crash &
auto-respawn, the data wall, cache envelopes, big frames, ping, and the ready
handshake metadata that the loader mounts from.

JVM cases skip cleanly when no `java` or no built fixture jar is available.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest

from mate.api.modules import subprocess_worker
from mate.api.modules.runtimes.base import WorkerLaunchSpec
from mate.api.modules.subprocess_host import SubprocessBridge, SubprocessHostError
from mate.sdk.errors import Cancelled
from mate.sdk.manifest import Manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
PY_FIXTURE = Path(__file__).parent / "fixtures" / "conformance_worker"
JVM_JAR = (
    REPO_ROOT
    / "packages"
    / "module-sdk-jvm"
    / "conformance-fixture"
    / "build"
    / "libs"
    / "conformance-worker-all.jar"
)

RUNTIMES = ["python", "jvm"]


def _manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "id": "conformance_worker",
            "name": "Conformance",
            "version": "0.1.0",
            "category": "other",
            "dependencies": {"python": {"isolation": "subprocess"}},
        }
    )


def _launch_for(runtime: str) -> tuple[WorkerLaunchSpec, Path]:
    if runtime == "python":
        return (
            WorkerLaunchSpec(
                argv=(sys.executable, str(Path(subprocess_worker.__file__))),
                env={"PYTHONUNBUFFERED": "1"},
            ),
            PY_FIXTURE,
        )
    java = shutil.which("java")
    if java is None:
        pytest.skip("no `java` on PATH - install a JRE 17+ to run JVM conformance")
    if not JVM_JAR.exists():
        pytest.skip("JVM conformance jar not built - run `make sdk-jvm`")
    return WorkerLaunchSpec(argv=(java, "-jar", str(JVM_JAR))), PY_FIXTURE


# -- host-side ctx stub -------------------------------------------------------


class _StubEventLog:
    def __init__(self, walled: bool = False) -> None:
        self._walled = walled
        self.df = pd.DataFrame(
            {
                "case_id": ["c1", "c1", "c2"],
                "activity": ["a", "b", "a"],
                "timestamp": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            }
        )
        self.active_filter = None
        self.queries: list[str] = []

    @property
    def events_path(self) -> Path:
        if self._walled:
            raise RuntimeError("restricted")
        return Path("/tmp/events.parquet")

    @property
    def cases_path(self) -> Path:
        raise RuntimeError("no cases file")

    async def __aenter__(self) -> _StubEventLog:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def duckdb_fetch(self, sql: str, params: Any = None) -> list[tuple[Any, ...]]:
        self.queries.append(sql)
        return [(1, "two")]

    async def pandas(self) -> pd.DataFrame:
        return self.df


class _StubCache:
    def __init__(self, workdir: Path) -> None:
        self.store: dict[str, Any] = {}
        self.dir = workdir / "cache"
        self.dir.mkdir(parents=True, exist_ok=True)

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.store[key] = value

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _StubRegistry:
    def installed_modules(self) -> list[str]:
        return ["discovery"]

    def visible_capabilities(self) -> list[str]:
        return []

    async def call(self, capability: str, **kwargs: Any) -> Any:
        raise LookupError(f"capability {capability!r} is not provided")


class _StubBus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, Any]] = []

    async def emit(self, topic: str, payload: Any) -> None:
        self.emitted.append((topic, payload))


class _StubProgress:
    def __init__(self) -> None:
        self.ticks: list[dict[str, Any]] = []

    async def update(
        self,
        current: Any,
        message: Any = None,
        *,
        total: Any = None,
        stage: Any = None,
    ) -> None:
        self.ticks.append({"current": current, "message": message, "total": total, "stage": stage})


class _RecordingLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kw: Any) -> None:
        self.entries.append((level, event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self._record("debug", event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        self._record("info", event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._record("warning", event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._record("error", event, **kw)


class _StubConfig:
    value: ClassVar[dict[str, Any]] = {"threshold": 3}

    def get(self, key: str, default: Any = None) -> Any:
        return self.value.get(key, default)


class StubCtx:
    def __init__(self, workdir: Path, *, walled: bool = False, job_id: str | None = None) -> None:
        self.log_id = "log1"
        self.module_id = "conformance_worker"
        self.workdir = workdir
        self.config = _StubConfig()
        self.event_log = _StubEventLog(walled=walled)
        self.cache = _StubCache(workdir)
        self.registry = _StubRegistry()
        self.bus = _StubBus()
        self.progress = _StubProgress()
        self.logger = _RecordingLogger()
        if job_id is not None:
            self._ff_job_id = job_id


# -- fixtures -----------------------------------------------------------------


@pytest.fixture(params=RUNTIMES)
def runtime(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
async def bridge(runtime: str):
    launch, folder = _launch_for(runtime)
    b = SubprocessBridge(_manifest(), folder, launch)
    # Fast respawn ladder so the crash test doesn't sleep through backoff.
    b._respawn_backoff_cap = 0.05
    b._respawn_max_attempts = 5
    module = await b.start()
    try:
        yield b, module
    finally:
        await b.stop()


@pytest.fixture
def ctx(tmp_path: Path) -> StubCtx:
    return StubCtx(tmp_path)


# -- ready handshake / mounting ------------------------------------------------


@pytest.mark.asyncio
async def test_ready_metadata_mounts_equivalently(bridge) -> None:
    b, module = bridge
    meta = {entry["attr"]: entry for entry in b._handlers_meta}

    route = meta["echo"]["route"]
    assert (route["method"], route["path"]) == ("GET", "/echo")

    job_route = meta["cancel_loop"]
    assert job_route["route"]["method"] == "POST"
    assert job_route["job"]["progress"] is True
    assert job_route["job"]["cancellable"] is True
    assert job_route["job"]["title"] == "Cancel loop"

    precompute = meta["precompute"]
    assert precompute["on_event"] == {"topic": "log.imported"}
    assert precompute["job"]["title"] == "Conformance precompute"

    # The loader mounts from decorator metadata on the shim - assert it's there.
    from mate.sdk.decorators import get_job_spec, get_route_spec

    echo_stub = type(module).echo
    spec = get_route_spec(echo_stub)
    assert spec is not None and spec.path == "/echo"
    cancel_stub = type(module).cancel_loop
    job_spec = get_job_spec(cancel_stub)
    assert job_spec is not None and job_spec.progress is True

    # Guidance advertised + callable through the bridge.
    assert b._guidance_meta is not None
    assert b._guidance_meta["system_prompt"].startswith("Conformance")
    assert callable(getattr(module, "guidance_payload", None))


@pytest.mark.asyncio
async def test_ping(bridge) -> None:
    b, _module = bridge
    assert await b.ping() is True


# -- ctx.* round-trips ----------------------------------------------------------


@pytest.mark.asyncio
async def test_echo_args_kwargs_and_scalars(bridge, ctx) -> None:
    b, _ = bridge
    result = await b.call_handler("echo", ctx, ("pos1", 2), {"k": "v", "n": 3})
    assert result["args"] == ["pos1", 2]
    assert result["kwargs"] == {"k": "v", "n": 3}
    assert result["log_id"] == "log1"
    assert result["module_id"] == "conformance_worker"


@pytest.mark.asyncio
async def test_snapshot_serves_locally(bridge, ctx) -> None:
    b, _ = bridge
    result = await b.call_handler("snapshot", ctx, (), {})
    assert result["log_id"] == "log1"
    assert result["workdir_exists"] is True
    assert result["config"] == {"threshold": 3}


@pytest.mark.asyncio
async def test_cache_roundtrip(bridge, ctx) -> None:
    b, _ = bridge
    value = {"nested": [1, 2, {"x": True}]}
    result = await b.call_handler("cache_roundtrip", ctx, (), {"value": value})
    assert result["got"] == value
    assert result["exists_after_set"] is True
    assert result["exists_after_delete"] is False
    assert "conf_key" not in ctx.cache.store


@pytest.mark.asyncio
async def test_cache_pickle_read(bridge, ctx, runtime) -> None:
    b, _ = bridge
    ctx.cache.store["pickled"] = b"\x00\x01"  # not JSON -> pickle envelope
    result = await b.call_handler("cache_pickle", ctx, (), {})
    if runtime == "python":
        assert result == {"read_type": "bytes"}
    else:
        assert result == "unsupported"


@pytest.mark.asyncio
async def test_bus_emit(bridge, ctx) -> None:
    b, _ = bridge
    assert await b.call_handler("bus_emit", ctx, (), {}) == "ok"
    assert ctx.bus.emitted == [("conformance.pinged", {"n": 1})]


@pytest.mark.asyncio
async def test_progress_ticks(bridge, ctx) -> None:
    b, _ = bridge
    assert await b.call_handler("progress_ticks", ctx, (), {}) == "ok"
    currents = [t["current"] for t in ctx.progress.ticks]
    assert currents == [0.25, 0.5, 1.0]
    mid = ctx.progress.ticks[1]
    assert mid["total"] == 1.0 and mid["stage"] == "mid"


@pytest.mark.asyncio
async def test_logger_lines_arrive(bridge, ctx) -> None:
    b, _ = bridge
    assert await b.call_handler("log_lines", ctx, (), {}) == "ok"
    # logger is fire-and-forget - give the host a beat to process both RPCs.
    for _ in range(100):
        if len(ctx.logger.entries) >= 2:
            break
        await asyncio.sleep(0.01)
    events = {(level, event) for level, event, _ in ctx.logger.entries}
    assert ("info", "conformance_started") in events
    assert ("warning", "conformance_warned") in events
    started = next(kw for level, event, kw in ctx.logger.entries if event == "conformance_started")
    assert started.get("n") == 1


@pytest.mark.asyncio
async def test_duckdb_fetch_rows(bridge, ctx) -> None:
    b, _ = bridge
    result = await b.call_handler("duckdb", ctx, (), {})
    assert result == [[1, "two"]]
    assert ctx.event_log.queries  # SQL executed host-side


@pytest.mark.asyncio
async def test_materialize_parquet_handoff(bridge, ctx, runtime) -> None:
    b, _ = bridge
    result = await b.call_handler("materialize_info", ctx, (), {})
    if runtime == "python":
        assert result == {"rows": 3}
    else:
        assert result["size"] > 0
        assert result["path"].startswith(str(ctx.workdir))


@pytest.mark.asyncio
async def test_data_wall_absent_key_errors_typed(bridge, tmp_path) -> None:
    b, _ = bridge
    walled = StubCtx(tmp_path, walled=True)
    assert await b.call_handler("datawall_events_path", walled, (), {}) == "walled"
    # And with the key present, the path is served locally.
    open_ctx = StubCtx(tmp_path)
    result = await b.call_handler("datawall_events_path", open_ctx, (), {})
    assert result.endswith("events.parquet")


@pytest.mark.asyncio
async def test_registry_snapshot(bridge, ctx) -> None:
    b, _ = bridge
    result = await b.call_handler("registry_visible", ctx, (), {})
    if isinstance(result, dict):  # python fixture shape
        assert result == {"has_discovery": True, "has_missing": False}
    else:  # jvm fixture returns the visible list
        assert "discovery" in result


@pytest.mark.asyncio
async def test_big_frame(bridge, ctx) -> None:
    b, _ = bridge
    result = await b.call_handler("big_result", ctx, (), {"bytes": 2_000_000})
    assert len(result) == 2_000_000


@pytest.mark.asyncio
async def test_handler_error_propagates_message(bridge, ctx) -> None:
    b, _ = bridge
    with pytest.raises(RuntimeError, match="boom"):
        await b.call_handler("boom", ctx, (), {})


@pytest.mark.asyncio
async def test_overlapping_calls(bridge, ctx, tmp_path) -> None:
    b, _ = bridge
    slow = asyncio.create_task(
        b.call_handler("busy_sleep", StubCtx(tmp_path), (), {"seconds": 1.0})
    )
    await asyncio.sleep(0.2)
    fast = await b.call_handler("echo", ctx, (), {"quick": True})
    assert fast["kwargs"] == {"quick": True}
    assert await asyncio.wait_for(slow, timeout=10.0) == "done"


# -- cancellation + crash --------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_cancel_unwinds_as_cancelled(bridge, tmp_path) -> None:
    b, _ = bridge
    job_ctx = StubCtx(tmp_path, job_id="job-soft")
    task = asyncio.create_task(b.call_handler("cancel_loop", job_ctx, (), {}))
    await asyncio.sleep(0.5)  # let the worker enter its poll loop
    assert not task.done()
    await b.soft_cancel("job-soft")
    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, timeout=10.0)


@pytest.mark.asyncio
async def test_hard_cancel_kills_and_respawns(bridge, tmp_path, ctx) -> None:
    b, _ = bridge
    job_ctx = StubCtx(tmp_path, job_id="job-hard")
    task = asyncio.create_task(b.call_handler("busy_sleep", job_ctx, (), {"seconds": 60}))
    await asyncio.sleep(0.5)
    await b.cancel_active()
    with pytest.raises(SubprocessHostError):
        await asyncio.wait_for(task, timeout=10.0)
    # The bridge respawned - the module keeps serving.
    result = await b.call_handler("echo", ctx, (), {"after": "hard-cancel"})
    assert result["kwargs"] == {"after": "hard-cancel"}


@pytest.mark.asyncio
async def test_spontaneous_crash_fails_call_and_auto_respawns(bridge, ctx, tmp_path) -> None:
    b, _ = bridge
    with pytest.raises(SubprocessHostError):
        await asyncio.wait_for(b.call_handler("crash", StubCtx(tmp_path), (), {}), timeout=10.0)
    # No cancel involved - the EOF-triggered auto-respawn must bring it back.
    result = await b.call_handler("echo", ctx, (), {"after": "crash"})
    assert result["kwargs"] == {"after": "crash"}
    assert b._failed is None


# -- guidance ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guidance_payload_roundtrip(bridge, ctx) -> None:
    _b, module = bridge
    payload = await module.guidance_payload(ctx)
    assert payload["guidance"] is True
    assert payload["log_id"] == "log1"
