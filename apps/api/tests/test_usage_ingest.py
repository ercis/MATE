"""/api/v1/usage ingest gate - the server-side privacy safety net.

``POST /usage/sync`` only persists events when the caller's tracking config is
effectively enabled. Two policies are exercised:

* ``force`` (the repo default) - tracking is on for every user regardless of the
  stored ``enabled`` flag, so a batch is always accepted.
* ``off`` - tracking is opt-in; a user who hasn't opted in is rejected with 204,
  and a batch whose ``anon_user_id`` doesn't match the configured seed is dropped
  even after opting in (stale-client replay guard).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID


def _batch(anon_user_id: str, *, event_name: str) -> dict[str, object]:
    return {
        "session": {
            "id": f"ingest-test-{event_name}",
            "anon_user_id": anon_user_id,
            "started_at": datetime.now(UTC).isoformat(),
        },
        "events": [
            {
                "event_type": "click",
                "event_name": event_name,
                "occurred_at": datetime.now(UTC).isoformat(),
                "path": "/x",
                "properties": {"kind": "click"},
            }
        ],
    }


async def _count(event_name: str) -> int:
    from sqlalchemy import func, select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsEvent

    sm = get_sessionmaker()
    async with sm() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AnalyticsEvent)
                .where(AnalyticsEvent.event_name == event_name)
            )
            or 0
        )


@pytest.fixture
def tracking_off() -> Iterator[None]:
    """Flip ``USER_TRACKING_ONBOARDING`` to ``off`` for one test, then restore.

    ``_effective`` reads ``get_settings()`` per request, so clearing the lru_cache
    after re-setting the env var takes effect for subsequent requests on the same
    app. Restored in teardown so the default-``force`` tests are unaffected.
    """
    import os

    from mate.api import config as cfg

    prev = os.environ.get("USER_TRACKING_ONBOARDING")
    os.environ["USER_TRACKING_ONBOARDING"] = "off"
    cfg.get_settings.cache_clear()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("USER_TRACKING_ONBOARDING", None)
        else:
            os.environ["USER_TRACKING_ONBOARDING"] = prev
        cfg.get_settings.cache_clear()


async def _seed(client: AsyncClient) -> str:
    """Read the user's config so the anon seed exists; return it."""
    cfg = (await client.get("/api/v1/usage/config")).json()
    return str(cfg["anon_user_id_seed"])


@pytest.mark.asyncio
async def test_ingest_accepted_under_force(client: AsyncClient) -> None:
    # Default policy is `force` - tracking is on for everyone, batch persists.
    seed = await _seed(client)
    resp = await client.post("/api/v1/usage/sync", json=_batch(seed, event_name="forced.click"))
    assert resp.status_code == 202
    assert await _count("forced.click") == 1


@pytest.mark.asyncio
async def test_ingest_rejected_when_disabled(client: AsyncClient, tracking_off: None) -> None:
    # Under `off`, a user who never opted in is rejected (204) and nothing lands.
    seed = await _seed(client)
    resp = await client.post("/api/v1/usage/sync", json=_batch(seed, event_name="disabled.click"))
    assert resp.status_code == 204
    assert await _count("disabled.click") == 0


@pytest.mark.asyncio
async def test_ingest_anon_seed_mismatch_dropped(client: AsyncClient, tracking_off: None) -> None:
    # Opt in so the gate is open, then POST with a stale anon id - the replay
    # guard silently drops it (204) without persisting.
    await _seed(client)
    put = await client.put(
        "/api/v1/usage/config",
        json={"enabled": True, "anon_user_id_seed": "00000000-0000-0000-0000-00000000seed"},
    )
    assert put.status_code == 200
    assert put.json()["enabled"] is True

    resp = await client.post(
        "/api/v1/usage/sync",
        json=_batch("a-different-anon-id", event_name="mismatch.click"),
    )
    assert resp.status_code == 204
    assert await _count("mismatch.click") == 0

    # The matching seed is accepted - proves the gate itself was open.
    ok = await client.post(
        "/api/v1/usage/sync",
        json=_batch("00000000-0000-0000-0000-00000000seed", event_name="match.click"),
    )
    assert ok.status_code == 202
    assert await _count("match.click") == 1


@pytest.fixture(autouse=True)
async def _reset_config() -> AsyncIterator[None]:
    """Remove this user's analytics config row after each test.

    The config row is per-user and shared across the session DB; resetting it
    keeps the opt-in/seed state from one test leaking into the next.
    """
    yield
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import UserSetting
    from mate.api.routes.analytics import ANALYTICS_CONFIG_KEY

    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(
            delete(UserSetting).where(
                UserSetting.user_id == TEST_USER_ID,
                UserSetting.key == ANALYTICS_CONFIG_KEY,
            )
        )
        await session.commit()
