"""All-requests usage middleware + batched server-event writer.

The middleware never touches the DB on the request path - it enqueues drafts
that the background writer persists (consent-gated, objects materialised).
Exercised here without HTTP: the auth override in tests bypasses the real
``get_current_user`` that stamps ``scope.state.user_id``, so we drive
``_enqueue`` and the drain directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID


def test_excluded_paths() -> None:
    from mate.api.middleware.usage import _excluded

    assert _excluded("OPTIONS", "/api/v1/event-logs")
    assert _excluded("HEAD", "/api/v1/event-logs")
    assert _excluded("POST", "/api/v1/usage/sync")
    assert _excluded("GET", "/api/v1/events")
    assert _excluded("GET", "/api/v1/admin/insights/overview")
    assert _excluded("GET", "/api/v1/jobs/abc/stream")
    assert _excluded("GET", "/health")
    assert not _excluded("GET", "/api/v1/event-logs")
    assert not _excluded("DELETE", "/api/v1/event-logs/abc")
    assert not _excluded("GET", "/api/v1/jobs/abc")


def test_business_op_names_kept() -> None:
    from mate.api.middleware.usage import _match_op

    assert _match_op("POST", "/api/v1/ai/chat") == "ai_chat"
    assert _match_op("DELETE", "/api/v1/event-logs/x") == "process_deleted"
    assert _match_op("GET", "/api/v1/event-logs") is None


@pytest.mark.asyncio
async def test_enqueued_request_becomes_operation_event(client: AsyncClient) -> None:
    # `client` boots the app so the engine/settings point at the test DB; the
    # config GET seeds the consent row (repo default policy: force → enabled).
    await client.get("/api/v1/usage/config")

    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsEvent, AnalyticsEventObject
    from mate.api.middleware.usage import UsageTrackingMiddleware
    from mate.api.services.usage_recorder import flush_pending_for_tests

    mw = UsageTrackingMiddleware(app=None)
    scope = {
        "route": SimpleNamespace(path_format="/api/v1/event-logs/{log_id}"),
        "path_params": {"log_id": "log-123"},
    }
    mw._enqueue(scope, "GET", "/api/v1/event-logs/log-123", TEST_USER_ID, 200, 42)
    await flush_pending_for_tests()

    sm = get_sessionmaker()
    async with sm() as session:
        row = (
            await session.execute(
                select(AnalyticsEvent).where(
                    AnalyticsEvent.user_id == TEST_USER_ID,
                    AnalyticsEvent.event_name == "GET /api/v1/event-logs/{log_id}",
                )
            )
        ).scalar_one()
        assert row.source == "server"
        assert row.event_type == "operation"
        assert row.duration_ms == 42
        assert row.properties is not None
        assert row.properties["path_params"] == {"log_id": "log-123"}

        # The path param materialised as an OCEL resource object.
        e2o = (
            await session.execute(
                select(AnalyticsEventObject.object_id).where(
                    AnalyticsEventObject.event_id == row.id
                )
            )
        ).all()
        object_ids = {str(oid) for (oid,) in e2o}
        assert "log:log-123" in object_ids
        assert "app:mate-api" in object_ids


@pytest.mark.asyncio
async def test_consent_cache_invalidated_on_config_change(client: AsyncClient) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.routes.analytics import cached_config

    await client.get("/api/v1/usage/config")
    sm = get_sessionmaker()
    async with sm() as session:
        first = await cached_config(session, TEST_USER_ID)
    # A config save must drop the cached entry so the next read sees the new
    # seed immediately (no 60s staleness window after an explicit change).
    put = await client.put(
        "/api/v1/usage/config",
        json={"enabled": True, "anon_user_id_seed": "11111111-1111-4111-8111-111111111111"},
    )
    assert put.status_code == 200
    async with sm() as session:
        second = await cached_config(session, TEST_USER_ID)
    assert first.anon_user_id_seed != second.anon_user_id_seed
    assert second.anon_user_id_seed == "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
async def _reset_state() -> AsyncIterator[None]:
    from .test_analytics_objects import _purge_analytics_state

    await _purge_analytics_state()
    yield
    await _purge_analytics_state()
