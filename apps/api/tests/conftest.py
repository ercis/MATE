"""Shared test fixtures.

The API is run against an isolated DATA_DIR per test session so SQLite, the
job runtime, and any Parquet output land in a tmp dir.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


TEST_USER_ID = "00000000-0000-7000-8000-000000000001"
TEST_USER_EMAIL = "test@mate.local"


@pytest.fixture(scope="session")
def session_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("ff-data")
    (root / "users" / TEST_USER_ID / "event_logs").mkdir(parents=True)
    (root / "users" / TEST_USER_ID / "module_results").mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _configure_env(session_data_dir: Path) -> Iterator[None]:
    os.environ["DATA_DIR"] = str(session_data_dir)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{session_data_dir}/metadata.db"
    os.environ["WORKER_CONCURRENCY"] = "1"
    # Default modules dir: an empty subdir so unrelated tests don't see the fixture mod.
    empty_modules = session_data_dir / "modules-empty"
    empty_modules.mkdir(exist_ok=True)
    os.environ["MODULES_DIR"] = str(empty_modules)
    # Force the lru_cache'd settings to be re-read.
    from mate.api import config as cfg

    cfg.get_settings.cache_clear()

    # Build the schema by running migrations head.
    from sqlalchemy import create_engine

    from mate.api.db.models import Base, User

    sync_url = os.environ["DATABASE_URL"].replace("+aiosqlite", "")
    engine = create_engine(sync_url, future=True)
    Base.metadata.create_all(engine)
    # Seed the test user so route handlers don't need to JIT-create one (the
    # JIT path goes through JWT validation, which we stub out below).
    from datetime import UTC, datetime

    from sqlalchemy.orm import Session

    with Session(engine) as s:
        if s.get(User, TEST_USER_ID) is None:
            s.add(
                User(
                    id=TEST_USER_ID,
                    email=TEST_USER_EMAIL,
                    preferred_username="test",
                    name="Test User",
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    last_seen_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            s.commit()
    engine.dispose()

    yield


@pytest.fixture(scope="session")
def _sync_engine(_configure_env: None) -> Iterator[Any]:
    """A plain sync engine on the session DB, for fixtures that run outside a loop."""
    from sqlalchemy import create_engine

    engine = create_engine(os.environ["DATABASE_URL"].replace("+aiosqlite", ""), future=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_admin_module_defaults(_sync_engine: Any) -> None:
    """Drop the admin module-default overrides before every test.

    ``modules.default_ids`` / ``modules.default_excluded_ids`` are *system*-wide
    ``SystemSetting`` rows living in the session-scoped SQLite DB, so an admin
    test that withholds a bundled default (``test_admin_modules.py``) reshapes
    the effective default set for every test that runs after it - which is how
    ``test_modules_per_user.py`` came to pass alone but fail in a full run.
    Clearing them up front keeps default resolution independent of file order.
    """
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from mate.api.db.models import SystemSetting
    from mate.api.modules.defaults import ADMIN_DEFAULTS_KEY, EXCLUDED_DEFAULTS_KEY

    with Session(_sync_engine) as s:
        s.execute(
            delete(SystemSetting).where(
                SystemSetting.key.in_([ADMIN_DEFAULTS_KEY, EXCLUDED_DEFAULTS_KEY])
            )
        )
        s.commit()


def _override_current_user_for_tests(app) -> None:
    """Bypass JWT validation by overriding ``get_current_user`` in the app.

    All routes that depend on ``CurrentUserDep`` route through
    ``mate.api.auth.dependencies.get_current_user``. FastAPI's
    ``dependency_overrides`` swaps it out at the app level - far cleaner than
    forging a token + JWKS for every test.
    """
    from mate.api.auth.dependencies import (
        CurrentUser,
        get_current_user,
    )

    fake_user = CurrentUser(
        id=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        preferred_username="test",
        name="Test User",
        roles=("user",),
    )

    async def _fake_current_user():
        return fake_user

    app.dependency_overrides[get_current_user] = _fake_current_user


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from mate.api.main import create_app

    app = create_app()
    _override_current_user_for_tests(app)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as c,
        app.router.lifespan_context(app),
    ):
        yield c


async def _seed_module_installs_for_test_user() -> None:
    """Grant the test user ownership of every loaded module.

    Module visibility is per-user (``module_installs``); the loader loads the
    fixture module into the process but doesn't record ownership. Real installs
    do that in the install job - here we seed it so the sample module shows up
    in the test user's listing.
    """
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules import get_module_loader
    from mate.api.modules.installs import record_install

    loader = get_module_loader()
    sm = get_sessionmaker()
    async with sm() as session:
        for manifest in loader.manifests():
            await record_install(session, TEST_USER_ID, manifest.id, "upload")
        await session.commit()


@contextlib.asynccontextmanager
async def _sample_mod_client(
    tmp_path: Path, *, seed: bool, mod: str = "sample_mod", admin: bool = False
) -> AsyncIterator[AsyncClient]:
    """Spin up the app with a fixture module (`mod`) as a default module.

    Copies the fixture into a tmp dir and points MODULES_DIR at it for the
    duration so `mod` is treated as a repo default (it lives under the defaults
    root). ``seed`` pre-grants the test user ownership of every loaded module
    (skip it to exercise the lazy default-seeding path).
    """
    src = Path(__file__).parent / "fixtures" / "modules" / mod
    dst = tmp_path / "modules" / mod
    shutil.copytree(src, dst)

    prev_modules = os.environ.get("MODULES_DIR")
    os.environ["MODULES_DIR"] = str(tmp_path / "modules")

    from mate.api import config as cfg

    cfg.get_settings.cache_clear()
    # Uploads land under the shared session data_dir; clear the root so a
    # successful upload in one test isn't rediscovered/loaded by the next.
    shutil.rmtree(cfg.get_settings().uploaded_modules_dir, ignore_errors=True)

    try:
        from mate.api.main import create_app

        app = create_app()
        _override_current_user_for_tests(app)
        if admin:
            from mate.api.auth.dependencies import CurrentUser, get_current_user

            admin_user = CurrentUser(
                id=TEST_USER_ID,
                email=TEST_USER_EMAIL,
                preferred_username="test",
                name="Test User",
                roles=("user", "admin"),
            )

            async def _admin_user() -> CurrentUser:
                return admin_user

            app.dependency_overrides[get_current_user] = _admin_user
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://testserver") as c,
            app.router.lifespan_context(app),
        ):
            if seed:
                await _seed_module_installs_for_test_user()
            yield c
    finally:
        if prev_modules is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev_modules
        cfg.get_settings.cache_clear()


@pytest.fixture
async def client_with_sample_mod(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with _sample_mod_client(tmp_path, seed=True) as c:
        yield c


@pytest.fixture
async def client_with_sample_mod_fresh(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """Test user NOT pre-seeded - exercises lazy default seeding."""
    async with _sample_mod_client(tmp_path, seed=False) as c:
        yield c


@pytest.fixture
async def admin_client_with_sample_cards(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """Admin-role app with the `sample_cards` fixture (config + ai + model cards)
    loaded - for the per-card admin-control tests. The admin user is also the
    module owner, so it can both lock cards (admin API) and view them (/config)."""
    async with _sample_mod_client(tmp_path, seed=True, mod="sample_cards", admin=True) as c:
        yield c
