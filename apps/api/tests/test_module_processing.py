"""Task 8 - a freshly imported log stays disabled (`status="processing"`) until
every subscribing module has finished precomputing against it.

Exercises the `ModuleProcessingCoordinator` directly (expected-set derivation,
the processing→ready flip off child-job terminality, boot reconcile) plus the
end-to-end ingest behaviour with a subscribing module installed.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import (
    TEST_USER_ID,
    _override_current_user_for_tests,
    _seed_module_installs_for_test_user,
)

FIXTURES = Path(__file__).parent / "fixtures"
IMPORT_JOB_TYPE = "event_log.import"


@contextlib.asynccontextmanager
async def _precompute_mod_app(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """App with `precompute_mod` as a default module, test user pre-seeded.

    Mirrors ``conftest._sample_mod_client`` but for the fixture that subscribes
    to ``log.imported`` with a ``@job`` - so an import lands a log in
    ``processing`` and produces a child precompute job under the import job.
    """
    src = FIXTURES / "modules" / "precompute_mod"
    dst = tmp_path / "modules" / "precompute_mod"
    shutil.copytree(src, dst)

    prev_modules = os.environ.get("MODULES_DIR")
    os.environ["MODULES_DIR"] = str(tmp_path / "modules")

    from mate.api import config as cfg

    cfg.get_settings.cache_clear()
    shutil.rmtree(cfg.get_settings().uploaded_modules_dir, ignore_errors=True)

    try:
        from mate.api.main import create_app

        app = create_app()
        _override_current_user_for_tests(app)
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://testserver") as c,
            app.router.lifespan_context(app),
        ):
            await _seed_module_installs_for_test_user()
            yield c
    finally:
        if prev_modules is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev_modules
        cfg.get_settings.cache_clear()


async def _make_log_row(status: str = "importing") -> str:
    """Insert a bare ``process_logs`` row for the test user; return its id."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog
    from mate.api.uuid7 import uuid7_str

    log_id = uuid7_str()
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            EventLog(
                id=log_id,
                user_id=TEST_USER_ID,
                name="processing-test",
                source_format="csv",
                log_model="case_centric",
                status=status,
            )
        )
        await session.commit()
    return log_id


async def _insert_import_job(log_id: str) -> str:
    """Insert a completed `event_log.import` job carrying `log_id` in its payload."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.uuid7 import uuid7_str

    job_id = uuid7_str()
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            Job(
                id=job_id,
                user_id=TEST_USER_ID,
                type=IMPORT_JOB_TYPE,
                title="import",
                payload_json={"log_id": log_id},
                status="completed",
            )
        )
        await session.commit()
    return job_id


async def _insert_child_job(parent_job_id: str, module_id: str, status: str) -> str:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.uuid7 import uuid7_str

    job_id = uuid7_str()
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            Job(
                id=job_id,
                user_id=TEST_USER_ID,
                type=f"module.{module_id}.event.log_imported",
                title="precompute",
                module_id=module_id,
                payload_json={},
                status=status,
                parent_job_id=parent_job_id,
            )
        )
        await session.commit()
    return job_id


async def _set_job_status(job_id: str, status: str) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job

    sm = get_sessionmaker()
    async with sm() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        job.status = status
        await session.commit()


async def _set_processing(log_id: str, import_job_id: str, expected: list[str]) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(EventLog, log_id)
        assert row is not None
        row.status = "processing"
        row.processing_import_job_id = import_job_id
        row.expected_modules = expected
        await session.commit()


async def _status(log_id: str) -> str:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    sm = get_sessionmaker()
    async with sm() as session:
        row = await session.get(EventLog, log_id)
        assert row is not None
        return row.status


@pytest.mark.asyncio
async def test_expected_modules_is_subscribers_intersect_installs(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.installs import remove_install
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        # Installed → expected includes the subscriber.
        async with sm() as session:
            expected = await coordinator.expected_modules("log.imported", TEST_USER_ID, session)
        assert expected == {"precompute_mod"}

        # OCEL topic isn't subscribed by this module → empty.
        async with sm() as session:
            expected_ocel = await coordinator.expected_modules(
                "ocel.imported", TEST_USER_ID, session
            )
        assert expected_ocel == set()

        # Uninstall → the subscriber drops out of the expected set even though
        # the module is still loaded in the process.
        async with sm() as session:
            await remove_install(session, TEST_USER_ID, "precompute_mod")
            await session.commit()
        async with sm() as session:
            expected_after = await coordinator.expected_modules(
                "log.imported", TEST_USER_ID, session
            )
        assert expected_after == set()


@pytest.mark.asyncio
async def test_check_and_finalize_waits_then_flips(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["precompute_mod"])

        # Child job still queued → stays processing.
        child_id = await _insert_child_job(import_job_id, "precompute_mod", "queued")
        async with sm() as session:
            flipped = await coordinator.check_and_finalize(log_id, session)
        assert flipped is False
        assert await _status(log_id) == "processing"

        # Child job completes → flips to ready, columns cleared.
        await _set_job_status(child_id, "completed")
        async with sm() as session:
            flipped = await coordinator.check_and_finalize(log_id, session)
        assert flipped is True
        assert await _status(log_id) == "ready"

        async with sm() as session:
            from mate.api.db.models import EventLog

            row = await session.get(EventLog, log_id)
            assert row is not None
            assert row.processing_import_job_id is None
            assert row.expected_modules is None

        # Idempotent: a second call on an already-ready log is a no-op.
        async with sm() as session:
            assert await coordinator.check_and_finalize(log_id, session) is False


@pytest.mark.asyncio
async def test_failed_child_counts_as_terminal(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["precompute_mod"])
        # A failed module job must not strand the log - counts as terminal.
        await _insert_child_job(import_job_id, "precompute_mod", "failed")

        async with sm() as session:
            flipped = await coordinator.check_and_finalize(log_id, session)
        assert flipped is True
        assert await _status(log_id) == "ready"


@pytest.mark.asyncio
async def test_cancelled_child_counts_as_terminal(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["precompute_mod"])
        await _insert_child_job(import_job_id, "precompute_mod", "cancelled")

        async with sm() as session:
            flipped = await coordinator.check_and_finalize(log_id, session)
        assert flipped is True
        assert await _status(log_id) == "ready"


@pytest.mark.asyncio
async def test_partial_coverage_stays_processing(tmp_path: Path) -> None:
    """Two expected modules, only one terminal → still processing."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        # Freeze an expected set with a *second* id that never reports terminal.
        await _set_processing(log_id, import_job_id, ["precompute_mod", "other_mod"])
        await _insert_child_job(import_job_id, "precompute_mod", "completed")

        async with sm() as session:
            flipped = await coordinator.check_and_finalize(log_id, session)
        assert flipped is False
        assert await _status(log_id) == "processing"

        # Once the second one also completes → flips.
        await _insert_child_job(import_job_id, "other_mod", "completed")
        async with sm() as session:
            flipped = await coordinator.check_and_finalize(log_id, session)
        assert flipped is True
        assert await _status(log_id) == "ready"


@pytest.mark.asyncio
async def test_reconcile_boot_flips_completed_processing_log(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["precompute_mod"])
        # The child finished while the API was "down" - reconcile picks it up.
        await _insert_child_job(import_job_id, "precompute_mod", "completed")

        async with sm() as session:
            await coordinator.reconcile_boot(session)
        assert await _status(log_id) == "ready"


@pytest.mark.asyncio
async def test_deleted_processing_log_is_noop(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog
    from mate.api.modules.processing import get_coordinator

    async with _precompute_mod_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["precompute_mod"])
        await _insert_child_job(import_job_id, "precompute_mod", "completed")
        # Soft-delete the row → finalize must not touch it.
        async with sm() as session:
            row = await session.get(EventLog, log_id)
            assert row is not None
            row.deleted_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()

        async with sm() as session:
            assert await coordinator.check_and_finalize(log_id, session) is False
        async with sm() as session:
            row = await session.get(EventLog, log_id)
            assert row is not None
            assert row.status == "processing"  # untouched


@pytest.mark.asyncio
async def test_import_with_subscriber_lands_processing(tmp_path: Path) -> None:
    """End-to-end: with a module that subscribes to `log.imported` via a `@job`
    installed, an import is `processing` right after the parse and only resolves
    to `ready` once the precompute child job finishes."""
    import asyncio

    async with _precompute_mod_app(tmp_path) as c:
        fixture = FIXTURES / "sample.csv"
        with fixture.open("rb") as f:
            resp = await c.post(
                "/api/v1/event-logs",
                files={"file": ("sample.csv", f, "text/csv")},
                data={"name": "Processing E2E"},
            )
        assert resp.status_code == 202, resp.text
        log_id = resp.json()["log_id"]

        # Poll until we leave `importing`. The next state must be `processing`
        # (the subscriber froze it), not `ready`.
        seen_processing = False
        deadline = asyncio.get_event_loop().time() + 8.0
        while asyncio.get_event_loop().time() < deadline:
            detail = (await c.get(f"/api/v1/event-logs/{log_id}")).json()
            if detail["status"] == "processing":
                seen_processing = True
                break
            if detail["status"] in ("ready", "failed"):
                break
            await asyncio.sleep(0.01)
        assert seen_processing, (
            f"expected `processing` with a subscriber installed, saw {detail['status']!r}"
        )

        # Data routes reject a non-ready log (409) - the log is genuinely
        # disabled while processing.
        gated = await c.get(f"/api/v1/event-logs/{log_id}/events")
        assert gated.status_code == 409

        # Eventually the precompute child finishes and the log flips to ready.
        deadline = asyncio.get_event_loop().time() + 15.0
        while asyncio.get_event_loop().time() < deadline:
            detail = (await c.get(f"/api/v1/event-logs/{log_id}")).json()
            if detail["status"] == "ready":
                break
            if detail["status"] == "failed":
                raise AssertionError(f"Import failed: {detail.get('error')}")
            await asyncio.sleep(0.05)
        assert detail["status"] == "ready"


@pytest.mark.asyncio
async def test_import_without_subscriber_is_ready_immediately(
    client: AsyncClient,
) -> None:
    """No module installed that subscribes to `log.imported` → the import skips
    `processing` and is `ready` as soon as the parse completes (the default test
    client loads no modules)."""
    fixture = FIXTURES / "sample.csv"
    with fixture.open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "No-subscriber"},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]

    import asyncio

    deadline = asyncio.get_event_loop().time() + 5.0
    last = {}
    while asyncio.get_event_loop().time() < deadline:
        last = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
        if last["status"] == "ready":
            break
        if last["status"] in ("processing", "failed"):
            raise AssertionError(f"Unexpected status {last['status']}: {last.get('error')}")
        await asyncio.sleep(0.02)
    assert last["status"] == "ready"
