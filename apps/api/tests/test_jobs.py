from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_bus_schema_enforcement() -> None:
    """Topics with a registered Pydantic schema must reject malformed
    payloads at publish time (§5.7a). Topics without a schema pass through
    untouched so platform-emitted `job.*` events don't have to round-trip.
    """
    from pydantic import BaseModel

    from mate.api.events.bus import EventBus, EventSchemaError

    class KpiPayload(BaseModel):
        log_id: str
        rate: float

    bus = EventBus()
    bus.register_schema("kpi.computed", KpiPayload)

    # Valid - passes through and gets re-normalised by Pydantic.
    await bus.publish("kpi.computed", {"log_id": "abc", "rate": 1.5})

    # Missing required field - clear error at the publish site.
    with pytest.raises(EventSchemaError):
        await bus.publish("kpi.computed", {"log_id": "abc"})

    # Wrong type - same outcome.
    with pytest.raises(EventSchemaError):
        await bus.publish("kpi.computed", {"log_id": "abc", "rate": "fast"})

    # Untyped topic - bus stays out of the way.
    await bus.publish("anything.goes", {"whatever": 1})

    # Re-registering with a different model is a hard error.
    class Other(BaseModel):
        x: int

    with pytest.raises(EventSchemaError, match="already has a schema"):
        bus.register_schema("kpi.computed", Other)


@pytest.mark.asyncio
async def test_runtime_run_in_process_uses_worker_pid() -> None:
    """`JobRuntime.run_in_process` must execute the callable in a different
    process so GIL-bound work parallelises (§8.3). We compare PIDs as the
    direct evidence - `os.getpid` is picklable and returns the worker's PID
    when run inside the executor.
    """
    from mate.api.jobs.runtime import JobRuntime

    rt = JobRuntime()
    try:
        worker_pid = await rt.run_in_process(os.getpid)
        assert worker_pid != os.getpid()
        # Each call is its own short-lived, killable process now (no warm pool), so
        # the second call runs in a *different* child - we only assert it's off the
        # main process; a distinct pid is expected, not required.
        again = await rt.run_in_process(os.getpid)
        assert again != os.getpid()
        # kwargs path: max(a, b, key=...) is awkward to pickle; use a simple
        # picklable case to confirm kwargs route through.
        rounded = await rt.run_in_process(round, 1.55555, ndigits=2)
        assert rounded == 1.56
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_job_execution_timeout_reaps_hung_job() -> None:
    """A handler that runs past `job_execution_timeout_seconds` is force-stopped
    by the wall-clock reaper, recorded as a failed-timeout (not a user cancel),
    and its worker slot is freed - the slot leak behind cross-user starvation
    (one user's wedged precompute draining the shared pool). A handler that
    finishes within budget is never reaped.
    """
    from mate.api.config import get_settings
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.events.bus import EventBus
    from mate.api.jobs.runtime import JobRuntime

    from .conftest import TEST_USER_ID

    settings = get_settings().model_copy(
        update={"job_execution_timeout_seconds": 1, "worker_concurrency": 1}
    )
    rt = JobRuntime(settings=settings, bus=EventBus())

    async def hang(_handle: object) -> None:
        await asyncio.sleep(30)  # no cancel poll - only the reaper can stop it

    async def quick(_handle: object) -> None:
        return None

    rt.register("test.hang", hang)
    rt.register("test.quick", quick)
    sm = get_sessionmaker()

    async def _status(job_id: str) -> tuple[str, str | None]:
        async with sm() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            return job.status, job.error

    try:
        await rt.start()
        # worker_concurrency=1 → quick runs (and finishes) first, then hang.
        quick_id = await rt.submit(
            type_="test.quick", user_id=TEST_USER_ID, title="quick", payload={}
        )
        hang_id = await rt.submit(type_="test.hang", user_id=TEST_USER_ID, title="hang", payload={})

        quick_status = "queued"
        for _ in range(40):  # ≤2s: well inside the 1s budget, untouched by reaper
            quick_status, _ = await _status(quick_id)
            if quick_status == "completed":
                break
            await asyncio.sleep(0.05)
        assert quick_status == "completed"

        hang_status, hang_err = "running", None
        for _ in range(60):  # ≤6s: reaper fires at ~1s, then records the outcome
            hang_status, hang_err = await _status(hang_id)
            if hang_status in {"failed", "cancelled", "completed"}:
                break
            await asyncio.sleep(0.1)
        assert hang_status == "failed"
        assert hang_err is not None and "timeout" in hang_err.lower()

        # Slot freed - the whole point: a wedged job can't hold a worker forever.
        assert rt.live_stats()["running"] == 0
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_reaper_sigkills_offloaded_process() -> None:
    """The wall-clock reaper must actually *kill* a runaway `ctx.run_in_process`
    offload, not just flip the DB row. An offloaded computation runs in a separate
    process that never sees the cooperative token or the asyncio task-cancel, so
    before the kill wiring it ran to natural completion long past its deadline (the
    53-min-on-a-30-min-timeout symptom). Assert the child is SIGKILLed and gone.
    """
    import time

    from mate.api.config import get_settings
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.events.bus import EventBus
    from mate.api.jobs.runtime import JobRuntime

    from .conftest import TEST_USER_ID

    settings = get_settings().model_copy(
        update={"job_execution_timeout_seconds": 1, "worker_concurrency": 1}
    )
    rt = JobRuntime(settings=settings, bus=EventBus())

    async def offloaded_hang(_handle: object) -> None:
        # `time.sleep` is a stdlib (picklable, child-importable) stand-in for a long
        # pm4py mining call: it runs in a child the cooperative token can't reach,
        # so only a kill stops it. 300s >> the 1s timeout.
        await rt.run_in_process(time.sleep, 300)

    rt.register("test.offload_hang", offloaded_hang)
    sm = get_sessionmaker()

    async def _status(job_id: str) -> tuple[str, str | None]:
        async with sm() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            return job.status, job.error

    try:
        await rt.start()
        job_id = await rt.submit(
            type_="test.offload_hang", user_id=TEST_USER_ID, title="offload", payload={}
        )

        import os

        from mate.api.jobs.supervisor import get_child_supervisor

        def _pid_alive(pid: int) -> bool:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        # Grab the offload child's pid the supervisor registered for this job,
        # while it's running.
        pid: int | None = None
        for _ in range(100):  # ≤5s (covers spawn/forkserver child startup)
            for h in get_child_supervisor().snapshot():
                if h.job_id == job_id and h.kind == "offload":
                    pid = h.pid
                    break
            if pid is not None:
                break
            await asyncio.sleep(0.05)
        assert pid is not None, "offload child was never registered with the supervisor"
        assert _pid_alive(pid)

        # Reaper fires at ~1s → job recorded as a failed-timeout (not a user cancel).
        status, err = "running", None
        for _ in range(60):  # ≤6s
            status, err = await _status(job_id)
            if status in {"failed", "cancelled", "completed"}:
                break
            await asyncio.sleep(0.1)
        assert status == "failed"
        assert err is not None and "timeout" in err.lower()

        # The fix: the offloaded OS process is actually dead - SIGKILLed by the
        # reaper through the supervisor and reaped, not left burning a core to
        # completion (it was a 300s sleep; it cannot have finished in this window).
        for _ in range(40):  # ≤2s for the host-side join/reap to settle
            if not _pid_alive(pid):
                break
            await asyncio.sleep(0.05)
        assert not _pid_alive(pid)
        assert rt.live_stats()["running"] == 0
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_per_user_offload_cap_bounds_concurrency() -> None:
    """`max_offloads_per_user` caps how many offloads one tenant runs at once,
    below the global pool size - so a single user's burst of heavy mining can't
    hold every offload slot and starve other tenants (the multi-tenant fix). With
    the knob at 0 the per-user cap defaults to the pool size (no bite).
    """
    from mate.api.config import get_settings
    from mate.api.events.bus import EventBus
    from mate.api.jobs.runtime import _CURRENT_JOB, _CURRENT_USER, JobRuntime

    settings = get_settings().model_copy(
        update={"module_process_pool_size": 4, "max_offloads_per_user": 2}
    )
    rt = JobRuntime(settings=settings, bus=EventBus())
    assert rt._offload_limits() == (4, 2)  # (global, per-user)

    live = 0
    peak = 0
    release = asyncio.Event()

    async def fake_spawn(
        _job_id: object, _offload: object, _fn: object, _args: object, _kwargs: object
    ) -> None:
        # Stand in for the real per-call process so the test is deterministic and
        # spawns nothing; it just holds the admitted slot until released.
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await release.wait()
        finally:
            live -= 1

    rt._spawn_offload = fake_spawn  # type: ignore[method-assign]

    async def one_offload() -> None:
        jtok = _CURRENT_JOB.set("job-A")
        utok = _CURRENT_USER.set("user-A")  # all five charge the same tenant
        try:
            await rt.run_offloaded(None, os.getpid)
        finally:
            _CURRENT_JOB.reset(jtok)
            _CURRENT_USER.reset(utok)

    tasks = [asyncio.create_task(one_offload()) for _ in range(5)]
    try:
        for _ in range(50):  # let admission settle
            await asyncio.sleep(0.01)
            if live >= 2:
                break
        await asyncio.sleep(0.1)  # give any wrongly-admitted extra a chance to show
        assert peak == 2  # never more than the per-user cap, though global=4, tasks=5
        assert live == 2
    finally:
        release.set()
        await asyncio.gather(*tasks)

    # Knob at 0 (default) → per-user cap equals the global pool: no bite single-tenant.
    open_rt = JobRuntime(
        settings=get_settings().model_copy(update={"module_process_pool_size": 3}),
        bus=EventBus(),
    )
    assert open_rt._offload_limits() == (3, 3)


async def _wait(
    client: AsyncClient, log_id: str, target: str = "ready", timeout: float = 5.0
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/event-logs/{log_id}")
        last = resp.json()
        if last["status"] == target:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"Did not reach {target!r} in {timeout}s - last: {last}")


@pytest.mark.asyncio
async def test_jobs_list_filters(client: AsyncClient) -> None:
    with (FIXTURES / "sample.xes").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xes", f, "application/xml")},
        )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    listing = await client.get("/api/v1/jobs", params={"type": "event_log.import"})
    assert listing.status_code == 200
    rows = listing.json()
    assert any(r["id"] == job_id for r in rows)
    for r in rows:
        assert r["type"] == "event_log.import"

    await _wait(client, resp.json()["log_id"], target="ready")
    finished = await client.get("/api/v1/jobs", params={"status": "completed", "limit": 5})
    assert finished.status_code == 200
    assert any(r["id"] == job_id for r in finished.json())


@pytest.mark.asyncio
async def test_retry_only_failed(client: AsyncClient) -> None:
    with (FIXTURES / "sample.xes").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xes", f, "application/xml")},
        )
    job_id = resp.json()["job_id"]
    await _wait(client, resp.json()["log_id"], target="ready")

    rejected = await client.post(f"/api/v1/jobs/{job_id}/retry")
    assert rejected.status_code == 409


@pytest.mark.asyncio
async def test_cancel_unknown_job_404(client: AsyncClient) -> None:
    # Cancel enforces ownership first (get_owned_job), so an unknown/not-yours
    # job id is 404 - like get/retry. 409 is reserved for "exists but finished".
    resp = await client.post("/api/v1/jobs/00000000-0000-0000-0000-000000000000/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_idempotent(client: AsyncClient) -> None:
    a = await client.post("/api/v1/jobs/queue/pause")
    assert a.status_code == 204
    b = await client.post("/api/v1/jobs/queue/pause")
    assert b.status_code == 204  # idempotent
    c = await client.post("/api/v1/jobs/queue/resume")
    assert c.status_code == 204


@pytest.mark.asyncio
async def test_sse_events_receives_job_lifecycle(client: AsyncClient) -> None:
    """The platform SSE stream relays a job's queued/started/completed lifecycle.

    The stream is SSE, not WebSocket: the prod proxy chain drops WS upgrades, so
    a handshake reaches the API as a plain GET and 404s. httpx's ASGITransport
    buffers the whole response and so can't read an open-ended stream, so we
    drive the route's streaming generator directly while a real import publishes
    `job.*` events onto the bus.
    """
    from mate.api.auth.dependencies import CurrentUser
    from mate.api.routes.events_sse import stream_events

    from .conftest import TEST_USER_EMAIL, TEST_USER_ID

    user = CurrentUser(
        id=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        preferred_username="test",
        name="Test User",
        roles=("user",),
    )
    resp = await stream_events(user=user, topic=["job.*"])
    received: list[dict] = []

    async def _collect() -> None:
        async for chunk in resp.body_iterator:
            for line in chunk.splitlines():
                if not line.startswith("data:"):  # skip `: ping` keep-alives
                    continue
                msg = json.loads(line[5:].lstrip(" "))
                received.append(msg)
                if msg["topic"] == "job.completed":
                    return

    async def _kick() -> None:
        # Let the bus subscription register before publishing - no replay.
        await asyncio.sleep(0.3)
        with (FIXTURES / "sample.xes").open("rb") as f:
            await client.post(
                "/api/v1/event-logs",
                files={"file": ("sample.xes", f, "application/xml")},
            )

    try:
        await asyncio.wait_for(asyncio.gather(_collect(), _kick()), timeout=15)
    finally:
        await resp.body_iterator.aclose()

    topics = [m["topic"] for m in received]
    assert "job.queued" in topics
    assert "job.started" in topics
    assert "job.completed" in topics


@pytest.mark.asyncio
async def test_interrupted_precompute_resumes_on_restart() -> None:
    """A precompute job killed mid-run (or left queued) by a restart must re-run
    on the next boot, not strand with an empty result cache.

    Regression: importing a log locally with `make dev` (`uvicorn --reload`), the
    *slow* modules (cv4cdd ~70s, complexity-over-time) were still running when a
    reload restarted the API. The boot reconcile marked them `failed` and stranded
    the queued ones, so nothing ever wrote their output - the dock showed the jobs
    but their panels were empty, i.e. they "did nothing". Precompute jobs are
    idempotent and re-runnable from their persisted row, so an interrupted one is
    reset to `queued` and re-enqueued instead.
    """
    from mate.api.config import get_settings
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.events.bus import EventBus
    from mate.api.jobs.runtime import JobRuntime

    from .conftest import TEST_USER_ID

    rt = JobRuntime(settings=get_settings(), bus=EventBus())
    ran: list[str] = []

    async def precompute(handle: object) -> None:
        ran.append(handle.id)  # type: ignore[attr-defined]

    # Precompute handlers are registered under exactly this type shape by the
    # module loader (`module.<id>.event.<topic>`).
    rt.register("module.demo.event.log_imported", precompute)
    sm = get_sessionmaker()

    async def _status(job_id: str) -> str:
        async with sm() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            return job.status

    def _child(jid: str, status: str, *, parent: str | None) -> Job:
        return Job(
            id=jid,
            user_id=TEST_USER_ID,
            type="module.demo.event.log_imported",
            title="demo precompute",
            module_id="demo",
            parent_job_id=parent,
            payload_json={"log_id": "log-1", "_event_payload": {}},
            status=status,
        )

    # The state a crash-mid-import leaves behind: a completed import job with one
    # child left `running` (slow module killed mid-run) and one still `queued`
    # (never got a worker), plus an unrelated `running` job with no import parent
    # (must fail as before, not resume).
    seeded = ["imp-1", "pc-running", "pc-queued", "other-running"]
    async with sm() as session:
        session.add(
            Job(
                id="imp-1",
                user_id=TEST_USER_ID,
                type="event_log.import",
                title="import",
                payload_json={"log_id": "log-1"},
                status="completed",
            )
        )
        session.add(_child("pc-running", "running", parent="imp-1"))
        session.add(_child("pc-queued", "queued", parent="imp-1"))
        session.add(_child("other-running", "running", parent=None))
        await session.commit()

    try:
        await rt.start()  # runs _reconcile_orphan_running

        # Killed precompute child → reset to `queued` (resumable), not failed.
        assert await _status("pc-running") == "queued"
        # Unrelated running job (no import parent) → failed as before.
        assert await _status("other-running") == "failed"

        await rt.resume_interrupted_precompute()

        for jid in ("pc-running", "pc-queued"):
            for _ in range(60):  # ≤3s
                if await _status(jid) == "completed":
                    break
                await asyncio.sleep(0.05)
            assert await _status(jid) == "completed", jid
        assert set(ran) == {"pc-running", "pc-queued"}  # both actually re-ran
        assert await _status("other-running") == "failed"  # never resumed
    finally:
        await rt.stop()
        async with sm() as session:
            for jid in seeded:
                row = await session.get(Job, jid)
                if row is not None:
                    await session.delete(row)
            await session.commit()
