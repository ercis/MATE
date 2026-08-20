"""Admin control framework - policy resolution, masking, and 403 gating.

Covers the generic ``ControlPolicy`` layer (``mate.api.policy``) plus its three
wirings: ``ai_config.load_ai_config`` (admin key injection + GET masking),
``routes/ai`` (403 on locked PUT, blank-key merge), and ``routes/modules``
(admin-controlled module config + 403). The default test user holds only the
``user`` role (so it exercises 403); ``admin_client`` re-overrides the current
user with an admin.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_EMAIL, TEST_USER_ID


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    """Like the shared ``client`` fixture but the current user is an admin."""
    from mate.api.auth.dependencies import CurrentUser, get_current_user
    from mate.api.main import create_app

    app = create_app()
    admin = CurrentUser(
        id=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        preferred_username="test",
        name="Test User",
        roles=("user", "admin"),
    )

    async def _admin_user() -> CurrentUser:
        return admin

    app.dependency_overrides[get_current_user] = _admin_user
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as c,
        app.router.lifespan_context(app),
    ):
        yield c


async def _clear_policy(scope: str, key: str) -> None:
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import ControlPolicy

    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(
            delete(ControlPolicy).where(ControlPolicy.scope == scope, ControlPolicy.key == key)
        )
        await session.commit()


# --------------------------------------------------------------------------
# Resolver
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_user_vs_admin() -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.policy import SCOPE_SETTING, resolve, set_policy

    sm = get_sessionmaker()
    key = f"test.resolve.{uuid.uuid4().hex[:8]}"
    try:
        # No row → per-user.
        async with sm() as session:
            value, controlled = await resolve(session, SCOPE_SETTING, key, TEST_USER_ID)
            assert value is None and controlled is False

        # Admin-controlled → shared value + flag.
        async with sm() as session:
            await set_policy(
                session,
                SCOPE_SETTING,
                key,
                control_mode="admin",
                admin_value={"x": 1},
                updated_by=TEST_USER_ID,
            )
            await session.commit()
        async with sm() as session:
            value, controlled = await resolve(session, SCOPE_SETTING, key, TEST_USER_ID)
            assert controlled is True and value == {"x": 1}

        # Back to user → clears the admin value.
        async with sm() as session:
            await set_policy(
                session,
                SCOPE_SETTING,
                key,
                control_mode="user",
                admin_value={"x": 1},
                updated_by=TEST_USER_ID,
            )
            await session.commit()
        async with sm() as session:
            from mate.api.db.models import ControlPolicy

            row = await session.get(ControlPolicy, (SCOPE_SETTING, key))
            assert row is not None
            assert row.control_mode == "user"
            assert row.admin_value_json is None
            value, controlled = await resolve(session, SCOPE_SETTING, key, TEST_USER_ID)
            assert controlled is False
    finally:
        await _clear_policy(SCOPE_SETTING, key)


# --------------------------------------------------------------------------
# AI config: admin key injection + GET masking + PUT 403
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_admin_key_injected_for_other_user() -> None:
    """load_ai_config returns the admin key for a *different* user when locked."""
    from mate.api.ai_config import AI_CONFIG_KEY, load_ai_config
    from mate.api.db.engine import get_sessionmaker
    from mate.api.policy import SCOPE_SETTING, set_policy

    sm = get_sessionmaker()
    other_user = "11111111-1111-7000-8000-0000000000aa"
    try:
        async with sm() as session:
            await set_policy(
                session,
                SCOPE_SETTING,
                AI_CONFIG_KEY,
                control_mode="admin",
                admin_value={
                    "anthropic": {"api_key": "sk-admin-shared", "base_url": None},
                    "selected_provider": "anthropic",
                    "selected_model": "claude-x",
                },
                updated_by=TEST_USER_ID,
            )
            await session.commit()

        async with sm() as session:
            cfg = await load_ai_config(session, other_user)
            assert cfg.anthropic.api_key == "sk-admin-shared"
            assert cfg.selected_provider == "anthropic"
    finally:
        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


@pytest.mark.asyncio
async def test_ai_config_get_masks_key(admin_client: AsyncClient) -> None:
    """GET /ai/config never returns api_key; reports controlled + key_set flags."""
    from mate.api.ai_config import AI_CONFIG_KEY

    try:
        # Lock with a stored key via the admin controls route.
        resp = await admin_client.put(
            f"/api/v1/admin/controls/items/setting/{AI_CONFIG_KEY}",
            json={
                "control_mode": "admin",
                "admin_value": {
                    "anthropic": {"api_key": "sk-secret-1", "base_url": None},
                    "selected_provider": "anthropic",
                    "selected_model": "claude-x",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The admin echo never carries the raw key.
        assert "sk-secret-1" not in resp.text
        assert body["secret_set"] is True

        # GET /ai/config: masked, controlled, key_set true, no api_key anywhere.
        got = await admin_client.get("/api/v1/ai/config")
        assert got.status_code == 200
        gj = got.json()
        assert gj["controlled_by_admin"] is True
        assert gj["anthropic_key_set"] is True
        assert "api_key" not in gj
        assert "anthropic" not in gj  # masked shape, not the nested provider obj
        assert "sk-secret-1" not in got.text
    finally:
        from mate.api.policy import SCOPE_SETTING

        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


@pytest.mark.asyncio
async def test_ai_config_put_403_when_controlled(client: AsyncClient) -> None:
    from mate.api.ai_config import AI_CONFIG_KEY
    from mate.api.db.engine import get_sessionmaker
    from mate.api.policy import SCOPE_SETTING, set_policy

    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await set_policy(
                session,
                SCOPE_SETTING,
                AI_CONFIG_KEY,
                control_mode="admin",
                admin_value={"anthropic": {"api_key": "sk-admin", "base_url": None}},
                updated_by=TEST_USER_ID,
            )
            await session.commit()

        resp = await client.put(
            "/api/v1/ai/config",
            json={
                "system_prompt": "",
                "anthropic": {"api_key": "sk-user", "base_url": None},
                "openai": {"api_key": None, "base_url": None},
                "unigpt": {"api_key": None, "base_url": None},
                "custom": {"api_key": None, "base_url": None},
                "selected_provider": "anthropic",
                "selected_model": "x",
                "allow_process_data": False,
            },
        )
        assert resp.status_code == 403
    finally:
        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


@pytest.mark.asyncio
async def test_ai_admin_blank_key_keeps_stored(admin_client: AsyncClient) -> None:
    """A second admin PUT with a blank key keeps the stored admin key (merge)."""
    from mate.api.ai_config import AI_CONFIG_KEY, load_ai_config
    from mate.api.db.engine import get_sessionmaker
    from mate.api.policy import SCOPE_SETTING

    try:
        # First save sets the key.
        await admin_client.put(
            f"/api/v1/admin/controls/items/setting/{AI_CONFIG_KEY}",
            json={
                "control_mode": "admin",
                "admin_value": {"anthropic": {"api_key": "sk-keep-me", "base_url": None}},
            },
        )
        # Second save leaves the key blank but changes the model.
        await admin_client.put(
            f"/api/v1/admin/controls/items/setting/{AI_CONFIG_KEY}",
            json={
                "control_mode": "admin",
                "admin_value": {
                    "anthropic": {"api_key": None, "base_url": None},
                    "selected_model": "claude-y",
                },
            },
        )
        sm = get_sessionmaker()
        async with sm() as session:
            cfg = await load_ai_config(session, "22222222-2222-7000-8000-0000000000bb")
            assert cfg.anthropic.api_key == "sk-keep-me"
            assert cfg.selected_model == "claude-y"
    finally:
        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


@pytest.mark.asyncio
async def test_admin_ai_config_editor_roundtrip(admin_client: AsyncClient) -> None:
    """The shared-config editor endpoints (/admin/controls/ai/config) save a
    masked, merged value, lock ai.config, and inject the key for every user."""
    from mate.api.ai_config import AI_CONFIG_KEY, load_ai_config
    from mate.api.db.engine import get_sessionmaker
    from mate.api.policy import SCOPE_SETTING

    try:
        # Save a key + model via the editor PUT.
        resp = await admin_client.put(
            "/api/v1/admin/controls/ai/config",
            json={
                "anthropic": {"api_key": "sk-shared-editor", "base_url": None},
                "selected_provider": "anthropic",
                "selected_model": "claude-x",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "sk-shared-editor" not in resp.text  # never echoed back
        assert body["anthropic_key_set"] is True
        assert body["controlled_by_admin"] is True
        assert body["selected_model"] == "claude-x"

        # GET is masked and reports the stored key.
        got = await admin_client.get("/api/v1/admin/controls/ai/config")
        assert got.status_code == 200
        assert got.json()["anthropic_key_set"] is True
        assert "sk-shared-editor" not in got.text

        # The saved value is now the shared key for any other user (locked).
        sm = get_sessionmaker()
        async with sm() as session:
            cfg = await load_ai_config(session, "33333333-3333-7000-8000-0000000000cc")
            assert cfg.anthropic.api_key == "sk-shared-editor"

        # A second PUT with a blank key keeps the stored one (merge).
        resp2 = await admin_client.put(
            "/api/v1/admin/controls/ai/config",
            json={
                "anthropic": {"api_key": None, "base_url": None},
                "selected_provider": "anthropic",
                "selected_model": "claude-y",
            },
        )
        assert resp2.status_code == 200, resp2.text
        async with sm() as session:
            cfg = await load_ai_config(session, "33333333-3333-7000-8000-0000000000cc")
            assert cfg.anthropic.api_key == "sk-shared-editor"
            assert cfg.selected_model == "claude-y"
    finally:
        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


@pytest.mark.asyncio
async def test_admin_ai_models_requires_key(admin_client: AsyncClient) -> None:
    """Fetching models with no shared key stored 400s before any outbound call."""
    from mate.api.ai_config import AI_CONFIG_KEY
    from mate.api.policy import SCOPE_SETTING

    try:
        resp = await admin_client.post("/api/v1/admin/controls/ai/models/anthropic")
        assert resp.status_code == 400, resp.text
    finally:
        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


@pytest.mark.asyncio
async def test_admin_ai_endpoints_require_admin(client: AsyncClient) -> None:
    """Non-admins are forbidden from the shared AI config + model endpoints."""
    assert (await client.get("/api/v1/admin/controls/ai/config")).status_code == 403
    put = await client.put(
        "/api/v1/admin/controls/ai/config",
        json={"selected_provider": "anthropic"},
    )
    assert put.status_code == 403
    assert (await client.post("/api/v1/admin/controls/ai/models/anthropic")).status_code == 403


@pytest.mark.asyncio
async def test_lock_with_no_value_keeps_stored(admin_client: AsyncClient) -> None:
    """Flipping the lock switch (no admin_value) must succeed, not 422, and must
    preserve any previously stored value - ai.config can't echo its key back, so
    the client never resends it on a relock."""
    from mate.api.ai_config import AI_CONFIG_KEY
    from mate.api.policy import SCOPE_SETTING

    try:
        # Lock from clean: no admin_value at all. Used to 422 "must be an object".
        resp = await admin_client.put(
            f"/api/v1/admin/controls/items/setting/{AI_CONFIG_KEY}",
            json={"control_mode": "admin"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["control_mode"] == "admin"
        assert resp.json()["secret_set"] is False

        # Set a key via the editor, then flip the lock again with no value.
        await admin_client.put(
            f"/api/v1/admin/controls/items/setting/{AI_CONFIG_KEY}",
            json={
                "control_mode": "admin",
                "admin_value": {"anthropic": {"api_key": "sk-stored", "base_url": None}},
            },
        )
        relock = await admin_client.put(
            f"/api/v1/admin/controls/items/setting/{AI_CONFIG_KEY}",
            json={"control_mode": "admin"},
        )
        assert relock.status_code == 200, relock.text
        # The stored key survived the value-less relock.
        assert relock.json()["secret_set"] is True
        assert "sk-stored" not in relock.text
    finally:
        await _clear_policy(SCOPE_SETTING, AI_CONFIG_KEY)


# --------------------------------------------------------------------------
# Admin-route gating
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_controls_require_admin(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/admin/controls/items?scope=setting")).status_code == 403
    resp = await client.put(
        "/api/v1/admin/controls/items/setting/ai.config",
        json={"control_mode": "user"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_controls_catalog(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/api/v1/admin/controls/items?scope=setting")
    assert resp.status_code == 200
    keys = {it["key"] for it in resp.json()["items"]}
    assert {"ai.config", "analytics.config", "worker_concurrency"} <= keys


# --------------------------------------------------------------------------
# Module config: admin-controlled + 403
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_module_config_403_when_controlled(client_with_sample_mod: AsyncClient) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.policy import SCOPE_MODULE, set_policy

    module_id = "sample_mod"
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await set_policy(
                session,
                SCOPE_MODULE,
                module_id,
                control_mode="admin",
                admin_value={"threshold": 0.9},
                updated_by=TEST_USER_ID,
            )
            await session.commit()

        # GET returns the shared config flagged read-only.
        got = await client_with_sample_mod.get(f"/api/v1/modules/{module_id}/config")
        assert got.status_code == 200
        gj = got.json()
        assert gj["controlled_by_admin"] is True
        assert gj["config"] == {"threshold": 0.9}

        # PUT is forbidden while controlled.
        put = await client_with_sample_mod.put(
            f"/api/v1/modules/{module_id}/config",
            json={"config": {"threshold": 0.1}, "enabled": True},
        )
        assert put.status_code == 403
    finally:
        await _clear_policy(SCOPE_MODULE, module_id)
