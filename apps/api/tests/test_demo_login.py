"""Demo login bypass (Settings.demo_mode / Settings.demo_admin).

When demo_mode is on, the demo sentinel token resolves to a fixed demo user
with no JWKS validation; demo_admin controls whether that user carries the
admin role. When demo_mode is off, the same token is rejected like any other
unverifiable bearer.

These tests set DEMO_MODE/DEMO_ADMIN explicitly (os.environ overrides the repo
.env) so they're deterministic regardless of the developer's local .env.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import HTTPException

from mate.api import config as cfg
from mate.api.auth.dependencies import (
    DEMO_ACCESS_TOKEN,
    get_current_user_from_token,
    reset_user_cache_for_tests,
)
from mate.api.db.engine import get_sessionmaker


def _apply(demo_mode: bool, demo_admin: bool) -> None:
    os.environ["DEMO_MODE"] = "true" if demo_mode else "false"
    os.environ["DEMO_ADMIN"] = "true" if demo_admin else "false"
    cfg.get_settings.cache_clear()


def _restore() -> None:
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("DEMO_ADMIN", None)
    cfg.get_settings.cache_clear()


@pytest.fixture
def demo_env() -> Iterator[None]:
    reset_user_cache_for_tests()
    try:
        yield
    finally:
        _restore()
        reset_user_cache_for_tests()


async def test_demo_token_resolves_non_admin_user(demo_env: None) -> None:
    _apply(demo_mode=True, demo_admin=False)
    sm = get_sessionmaker()
    async with sm() as session:
        user = await get_current_user_from_token(DEMO_ACCESS_TOKEN, session)
        await session.commit()

    assert user.id == "demo-user"
    assert user.email == "demo@mate.local"
    assert "admin" not in user.roles


async def test_demo_admin_grants_admin_role(demo_env: None) -> None:
    _apply(demo_mode=True, demo_admin=True)
    sm = get_sessionmaker()
    async with sm() as session:
        user = await get_current_user_from_token(DEMO_ACCESS_TOKEN, session)
        await session.commit()

    assert user.id == "demo-user"
    assert user.roles == ("admin",)


async def test_demo_user_row_is_created(demo_env: None) -> None:
    from mate.api.db.models import User

    _apply(demo_mode=True, demo_admin=False)
    sm = get_sessionmaker()
    async with sm() as session:
        await get_current_user_from_token(DEMO_ACCESS_TOKEN, session)
        await session.commit()

    async with sm() as session:
        row = await session.get(User, "demo-user")
    assert row is not None
    assert row.email == "demo@mate.local"


async def test_demo_token_rejected_when_disabled(demo_env: None) -> None:
    _apply(demo_mode=False, demo_admin=False)
    sm = get_sessionmaker()
    async with sm() as session:
        with pytest.raises(HTTPException) as exc:
            await get_current_user_from_token(DEMO_ACCESS_TOKEN, session)
    assert exc.value.status_code == 401
