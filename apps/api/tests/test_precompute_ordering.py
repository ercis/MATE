"""Precompute ordering: the readiness gate must follow the transitive
`provides`/`consumes` chain, not just the direct `log.imported` subscribers.

Two layers:
  * `_settled_set` as a pure unit (the cascade-skip matrix), no app needed.
  * the coordinator end-to-end with `precompute_mod` (a `log.imported`
    subscriber) and `chained_mod` (subscribes to the reserved
    `precompute_mod.completed`) installed - closure derivation, the gate waiting
    for the *downstream*, cascade-skip on an upstream failure, and the
    auto-emitted `<module_id>.completed` spawning the chained job.
"""

from __future__ import annotations

import asyncio
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
from .test_module_processing import (
    _insert_child_job,
    _insert_import_job,
    _make_log_row,
    _set_processing,
    _status,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Pure cascade-skip matrix - no app, no DB.
# --------------------------------------------------------------------------- #


def _settled(expected, roots, edges, terminal, succeeded):
    from mate.api.modules.processing import ModuleProcessingCoordinator

    return ModuleProcessingCoordinator._settled_set(
        set(expected), set(roots), edges, set(terminal), set(succeeded)
    )


def test_settled_root_waits_then_settles() -> None:
    # A root with no terminal job is never "settled" - the log must wait for it.
    assert _settled({"a"}, {"a"}, {}, set(), set()) == set()
    assert _settled({"a"}, {"a"}, {}, {"a"}, {"a"}) == {"a"}


def test_settled_downstream_waits_while_upstream_succeeds() -> None:
    # b ← a. a succeeded but b has no row yet → b will be triggered → keep waiting.
    s = _settled({"a", "b"}, {"a"}, {"b": {"a"}}, {"a"}, {"a"})
    assert s == {"a"}  # b not yet settled
    # b then completes → both settled.
    s2 = _settled({"a", "b"}, {"a"}, {"b": {"a"}}, {"a", "b"}, {"a", "b"})
    assert s2 == {"a", "b"}


def test_settled_cascade_skips_downstream_of_failed_upstream() -> None:
    # a failed (terminal, not succeeded) → its `<a>.completed` never fires → b is
    # unreachable and counts as settled (skipped) so the log can't strand.
    s = _settled({"a", "b"}, {"a"}, {"b": {"a"}}, {"a"}, set())
    assert s == {"a", "b"}


def test_settled_cascade_skips_whole_chain() -> None:
    # a → b → c; a fails → b and c both skip.
    s = _settled({"a", "b", "c"}, {"a"}, {"b": {"a"}, "c": {"b"}}, {"a"}, set())
    assert s == {"a", "b", "c"}


# --------------------------------------------------------------------------- #
# Coordinator end-to-end with a real chained fixture.
# --------------------------------------------------------------------------- #


@contextlib.asynccontextmanager
async def _chained_mods_app(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """App with `precompute_mod` + `chained_mod` loaded and owned by the test user.

    `chained_mod` subscribes to `precompute_mod.completed`, so the precompute
    closure of a `log.imported` import is {precompute_mod, chained_mod}.
    """
    for name in ("precompute_mod", "chained_mod"):
        shutil.copytree(FIXTURES / "modules" / name, tmp_path / "modules" / name)

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


@pytest.mark.asyncio
async def test_closure_includes_chained_downstream(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _chained_mods_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        async with sm() as session:
            expected = await coordinator.expected_modules("log.imported", TEST_USER_ID, session)
        # The downstream is pulled in transitively, not just the direct subscriber.
        assert expected == {"precompute_mod", "chained_mod"}

        async with sm() as session:
            nodes, plan = await coordinator.precompute_plan("log.imported", TEST_USER_ID, session)
        assert nodes == {"precompute_mod", "chained_mod"}
        by_id = {step["id"]: step["after"] for step in plan}
        assert by_id["precompute_mod"] == []  # a root
        assert by_id["chained_mod"] == ["precompute_mod"]  # waits on the root


@pytest.mark.asyncio
async def test_gate_waits_for_chained_downstream(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _chained_mods_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["chained_mod", "precompute_mod"])

        # Upstream done, downstream not even submitted yet → must keep waiting
        # (the downstream *will* be triggered because the upstream succeeded).
        up = await _insert_child_job(import_job_id, "precompute_mod", "completed")
        assert up
        async with sm() as session:
            assert await coordinator.check_and_finalize(log_id, session) is False
        assert await _status(log_id) == "processing"

        # Downstream completes → the whole chain is terminal → flip.
        await _insert_child_job(import_job_id, "chained_mod", "completed")
        async with sm() as session:
            assert await coordinator.check_and_finalize(log_id, session) is True
        assert await _status(log_id) == "ready"


@pytest.mark.asyncio
async def test_failed_upstream_cascade_skips_downstream(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.processing import get_coordinator

    async with _chained_mods_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        await _set_processing(log_id, import_job_id, ["chained_mod", "precompute_mod"])

        # Upstream FAILS → it never emits `precompute_mod.completed`, so the
        # downstream job is never created. The gate must still flip (skip), not
        # strand the log on a job that will never exist.
        await _insert_child_job(import_job_id, "precompute_mod", "failed")
        async with sm() as session:
            assert await coordinator.check_and_finalize(log_id, session) is True
        assert await _status(log_id) == "ready"


@pytest.mark.asyncio
async def test_on_terminal_job_emits_completed_and_spawns_chained(tmp_path: Path) -> None:
    """A successful upstream precompute → `<id>.completed` → the chained module's
    job is submitted and parented to the same import job (group + gate linkage)."""
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.modules.processing import get_coordinator

    async with _chained_mods_app(tmp_path):
        coordinator = get_coordinator()
        assert coordinator is not None
        sm = get_sessionmaker()

        log_id = await _make_log_row()
        import_job_id = await _insert_import_job(log_id)
        up = await _insert_child_job(import_job_id, "precompute_mod", "completed")

        # Drive the terminal-job reaction directly (no need for the real worker).
        await coordinator.on_terminal_job({"id": up})

        # The auto-emitted `precompute_mod.completed` flows to chained_mod's
        # job-backed @on_event runner, which submits a child *parented to this
        # import job* (the DB is shared across tests, so scope by parent id).
        async def _chained_spawned() -> bool:
            async with sm() as session:
                row = (
                    await session.execute(
                        select(Job.id).where(
                            Job.module_id == "chained_mod",
                            Job.parent_job_id == import_job_id,
                        )
                    )
                ).first()
            return row is not None

        deadline = asyncio.get_event_loop().time() + 5.0
        spawned = False
        while asyncio.get_event_loop().time() < deadline:
            spawned = await _chained_spawned()
            if spawned:
                break
            await asyncio.sleep(0.05)
        assert spawned, "chained_mod job was not spawned by precompute_mod.completed"
