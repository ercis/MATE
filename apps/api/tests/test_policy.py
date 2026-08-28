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
# Per-card module control (mate.api.modules.cards)
# --------------------------------------------------------------------------

_MID = "sample_cards"


async def _lock_card(client: AsyncClient, card_id: str, value: object) -> None:
    resp = await client.put(
        f"/api/v1/admin/controls/items/card/{_MID}:{card_id}",
        json={"control_mode": "admin", "admin_value": value},
    )
    assert resp.status_code == 200, resp.text


async def _unlock_card(client: AsyncClient, card_id: str) -> None:
    resp = await client.put(
        f"/api/v1/admin/controls/items/card/{_MID}:{card_id}",
        json={"control_mode": "user"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_card_lock_overlays_only_its_slice(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    """Locking the config card overlays only the config slice - the user's AI
    and model selections survive (the whole-module-lock regression)."""
    c = admin_client_with_sample_cards
    user_cfg = {
        "threshold": 0.5,
        "mode": "fast",
        "ai": {"llm": {"model": "user-llm"}},
        "model": "user-model",
    }
    put = await c.put(f"/api/v1/modules/{_MID}/config", json={"config": user_cfg, "enabled": True})
    assert put.status_code == 200

    await _lock_card(c, "config", {"threshold": 0.9, "mode": "slow"})

    body = (await c.get(f"/api/v1/modules/{_MID}/config")).json()
    assert body["controlled_cards"] == {"config": True, "ai": False, "model": False}
    assert body["controlled_by_admin"] is False  # not every card is locked
    # config props come from the admin; ai + model stay the user's.
    assert body["config"]["threshold"] == 0.9
    assert body["config"]["mode"] == "slow"
    assert body["config"]["ai"] == {"llm": {"model": "user-llm"}}
    assert body["config"]["model"] == "user-model"
    # The read-only sentinel is a runtime-only marker, never in the /config body.
    assert "__model_admin_locked__" not in body["config"]

    # The effective config the module actually receives matches.
    echo = (await c.get(f"/api/v1/modules/{_MID}/echo-config")).json()
    assert echo["config"]["threshold"] == 0.9
    assert echo["config"]["ai"] == {"llm": {"model": "user-llm"}}


@pytest.mark.asyncio
async def test_model_card_lock_sets_sentinel(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    c = admin_client_with_sample_cards
    await c.put(
        f"/api/v1/modules/{_MID}/config",
        json={"config": {"threshold": 0.5, "model": "user-model"}, "enabled": True},
    )
    await _lock_card(c, "model", {"model": "pinned"})

    body = (await c.get(f"/api/v1/modules/{_MID}/config")).json()
    assert body["controlled_cards"]["model"] is True
    assert body["config"]["model"] == "pinned"
    assert "__model_admin_locked__" not in body["config"]

    # The module context gets the sentinel + pin; its /models route reads it.
    echo = (await c.get(f"/api/v1/modules/{_MID}/echo-config")).json()
    assert echo["config"]["__model_admin_locked__"] is True
    assert echo["config"]["model"] == "pinned"
    models = (await c.get(f"/api/v1/modules/{_MID}/models")).json()
    assert models == {"locked": True, "selected": "pinned"}


@pytest.mark.asyncio
async def test_ai_card_lock_sets_sentinel(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    """Locking the ai card injects __ai_admin_locked__ into the module's runtime
    config (never into /config) so action routes gated on it - e.g. CDE's
    Pinecone rebuild - can refuse."""
    c = admin_client_with_sample_cards
    await c.put(
        f"/api/v1/modules/{_MID}/config",
        json={"config": {"threshold": 0.5, "ai": {"llm": {"model": "user-llm"}}}, "enabled": True},
    )
    await _lock_card(c, "ai", {"ai": {"llm": {"model": "pinned-llm"}}})

    body = (await c.get(f"/api/v1/modules/{_MID}/config")).json()
    assert body["controlled_cards"]["ai"] is True
    assert body["config"]["ai"] == {"llm": {"model": "pinned-llm"}}
    # Runtime-only marker, never leaked to the settings body.
    assert "__ai_admin_locked__" not in body["config"]

    # The module context receives the sentinel + the pinned ai slice.
    echo = (await c.get(f"/api/v1/modules/{_MID}/echo-config")).json()
    assert echo["config"]["__ai_admin_locked__"] is True
    assert echo["config"]["ai"] == {"llm": {"model": "pinned-llm"}}

    # Locks persist in the shared fixture DB; unlock so later tests see the ai
    # card unlocked again (mirrors the config-card tests' cleanup).
    await _unlock_card(c, "ai")


@pytest.mark.asyncio
async def test_put_ignores_locked_card_keeps_user_slice(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    """A PUT no longer 403s while a card is locked; the locked card's slice is
    kept from the user's stored value, other cards persist."""
    c = admin_client_with_sample_cards
    await c.put(
        f"/api/v1/modules/{_MID}/config",
        json={
            "config": {"threshold": 0.5, "mode": "fast", "ai": {"llm": {"model": "user-llm"}}},
            "enabled": True,
        },
    )
    await _lock_card(c, "config", {"threshold": 0.9, "mode": "slow"})

    # User tries to change the locked config props AND the unlocked ai card.
    put = await c.put(
        f"/api/v1/modules/{_MID}/config",
        json={
            "config": {"threshold": 0.1, "mode": "hacked", "ai": {"llm": {"model": "new-llm"}}},
            "enabled": True,
        },
    )
    assert put.status_code == 200  # no 403

    # Unlock and confirm the stored user slice for config was preserved (0.5),
    # not the attempted 0.1, while the ai change persisted.
    await _unlock_card(c, "config")
    stored = (await c.get(f"/api/v1/modules/{_MID}/config")).json()["config"]
    assert stored["threshold"] == 0.5
    assert stored["mode"] == "fast"
    assert stored["ai"] == {"llm": {"model": "new-llm"}}


@pytest.mark.asyncio
async def test_enabled_stays_user_controlled_under_lock(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    c = admin_client_with_sample_cards
    await _lock_card(c, "config", {"threshold": 0.9})
    await _lock_card(c, "ai", {"ai": {"llm": {"model": "admin-llm"}}})
    await _lock_card(c, "model", {"model": "pinned"})

    body = (await c.get(f"/api/v1/modules/{_MID}/config")).json()
    assert body["controlled_by_admin"] is True  # every card locked

    # The user can still disable the module even with every card locked.
    put = await c.put(f"/api/v1/modules/{_MID}/config", json={"config": {}, "enabled": False})
    assert put.status_code == 200
    assert put.json()["enabled"] is False
    assert (await c.get(f"/api/v1/modules/{_MID}/config")).json()["enabled"] is False


@pytest.mark.asyncio
async def test_card_catalog_and_setting_drops_cv4cdd_model(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    c = admin_client_with_sample_cards
    # cv4cdd.model is gone from the server-settings catalog.
    settings = (await c.get("/api/v1/admin/controls/items?scope=setting")).json()["items"]
    skeys = {it["key"] for it in settings}
    assert "cv4cdd.model" not in skeys
    assert "ai.config" in skeys

    # The card catalog lists one item per card the loaded module exposes.
    cards = (await c.get("/api/v1/admin/controls/items?scope=card")).json()["items"]
    by_key = {it["key"]: it for it in cards}
    assert {"sample_cards:config", "sample_cards:ai", "sample_cards:model"} <= set(by_key)
    cfg_item = by_key["sample_cards:config"]
    assert cfg_item["module_id"] == "sample_cards" and cfg_item["card_id"] == "config"
    assert cfg_item["config_schema"] is not None
    assert by_key["sample_cards:model"]["model_store"] is not None
    assert by_key["sample_cards:ai"]["ai_models"] is not None


@pytest.mark.asyncio
async def test_model_card_lock_requires_model_name(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    """An empty model-card pin is rejected (would break autodetect for all)."""
    c = admin_client_with_sample_cards
    resp = await c.put(
        "/api/v1/admin/controls/items/card/sample_cards:model",
        json={"control_mode": "admin", "admin_value": {"model": ""}},
    )
    assert resp.status_code == 422
