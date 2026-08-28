"""Admin user management + full-delete purge - `/api/v1/admin/users/*`.

Covers the drill-down detail endpoint and the destructive delete: DB cascade
completeness, on-disk purge, the self-delete guard, admin gating, the mandatory
`_seen_user_ids` eviction, the Keycloak hop (mocked), and the after-commit
partial-failure contract. The Keycloak admin client is unconfigured in tests, so
the real network is never touched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from .conftest import TEST_USER_EMAIL, TEST_USER_ID


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    """Shared `client`, but the current user is an admin (mirrors test_policy)."""
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


async def _seed_victim() -> str:
    """Seed a fresh second user with a row in every user-scoped table + on-disk
    data. Returns the new user id. Unique per call so tests don't collide in the
    session-shared SQLite DB."""
    from mate.api.config import get_settings
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import (
        AnalyticsSession,
        ApiToken,
        Dashboard,
        DashboardShare,
        EventLog,
        Folder,
        Job,
        Team,
        TeamMember,
        User,
        UserSetting,
        WatchedFolder,
    )
    from mate.api.uuid7 import uuid7_str

    vid = uuid7_str()
    log_id = uuid7_str()
    dash_id = uuid7_str()
    team_id = uuid7_str()

    sm = get_sessionmaker()
    async with sm() as s:
        s.add(
            User(
                id=vid,
                email="victim@mate.local",
                preferred_username="victim",
                name="Victim",
                created_at=_now(),
                last_seen_at=_now(),
            )
        )
        # Flush the parent row before the children so the FKs resolve regardless
        # of the unit-of-work insert ordering.
        await s.flush()
        s.add(Folder(id=uuid7_str(), user_id=vid, name="F"))
        s.add(EventLog(id=log_id, user_id=vid, name="L"))
        s.add(WatchedFolder(id=uuid7_str(), user_id=vid, name="W"))
        # Terminal status so the job runtime never touches this synthetic row.
        s.add(Job(id=uuid7_str(), user_id=vid, type="test", title="J", status="completed"))
        s.add(Dashboard(id=dash_id, user_id=vid, name="D"))
        # Share created BY the victim, targeting the admin - proves the cascade
        # revokes shares other users depend on.
        s.add(
            DashboardShare(
                id=uuid7_str(),
                dashboard_id=dash_id,
                created_by=vid,
                target_user_id=TEST_USER_ID,
            )
        )
        s.add(
            ApiToken(
                id=uuid7_str(),
                user_id=vid,
                name="T",
                token_hash=uuid7_str(),
                token_prefix="mate_pat_x",
            )
        )
        s.add(UserSetting(user_id=vid, key="k", value_json={"v": 1}))
        s.add(
            AnalyticsSession(
                id=uuid7_str(),
                user_id=vid,
                anon_user_id="anon",
                started_at=_now(),
                last_seen_at=_now(),
            )
        )
        s.add(Team(id=team_id, name="Tm"))
        s.add(TeamMember(team_id=team_id, user_id=vid, role="member"))
        await s.commit()

    d = get_settings().users_dir / vid / "event_logs" / log_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.parquet").write_bytes(b"x")
    return vid


async def _victim_row_counts(vid: str) -> dict[str, int]:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import (
        AnalyticsSession,
        ApiToken,
        Dashboard,
        DashboardShare,
        EventLog,
        Folder,
        Job,
        TeamMember,
        User,
        UserSetting,
        WatchedFolder,
    )

    sm = get_sessionmaker()
    async with sm() as s:

        async def count(model, col) -> int:
            return int(
                (
                    await s.execute(select(func.count()).select_from(model).where(col == vid))
                ).scalar_one()
            )

        return {
            "user": 1 if await s.get(User, vid) is not None else 0,
            "folders": await count(Folder, Folder.user_id),
            "logs": await count(EventLog, EventLog.user_id),
            "watched": await count(WatchedFolder, WatchedFolder.user_id),
            "jobs": await count(Job, Job.user_id),
            "dashboards": await count(Dashboard, Dashboard.user_id),
            "shares_created": await count(DashboardShare, DashboardShare.created_by),
            "tokens": await count(ApiToken, ApiToken.user_id),
            "settings": await count(UserSetting, UserSetting.user_id),
            "analytics": await count(AnalyticsSession, AnalyticsSession.user_id),
            "team_members": await count(TeamMember, TeamMember.user_id),
        }


@pytest.mark.asyncio
async def test_delete_purges_db_cascade_and_disk(admin_client: AsyncClient) -> None:
    from mate.api.config import get_settings

    vid = await _seed_victim()
    before = await _victim_row_counts(vid)
    assert before["user"] == 1 and before["logs"] == 1 and before["shares_created"] == 1
    user_dir = get_settings().users_dir / vid
    assert user_dir.exists()

    resp = await admin_client.delete(f"/api/v1/admin/users/{vid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    after = await _victim_row_counts(vid)
    assert after == {k: 0 for k in after}, after
    assert not user_dir.exists()


@pytest.mark.asyncio
async def test_self_delete_refused(admin_client: AsyncClient) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import User

    resp = await admin_client.delete(f"/api/v1/admin/users/{TEST_USER_ID}")
    assert resp.status_code == 400
    sm = get_sessionmaker()
    async with sm() as s:
        assert await s.get(User, TEST_USER_ID) is not None


@pytest.mark.asyncio
async def test_delete_requires_admin(client: AsyncClient) -> None:
    # The admin gate is a dependency - a non-admin is refused before any work,
    # so no seed is needed.
    resp = await client.delete(f"/api/v1/admin/users/{TEST_USER_ID}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_missing_user_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.delete("/api/v1/admin/users/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_evicts_seen_user_cache(admin_client: AsyncClient) -> None:
    from mate.api.auth import dependencies as deps

    vid = await _seed_victim()
    deps._seen_user_ids.add(vid)
    resp = await admin_client.delete(f"/api/v1/admin/users/{vid}")
    assert resp.status_code == 200
    assert vid not in deps._seen_user_ids


@pytest.mark.asyncio
async def test_keycloak_delete_invoked(admin_client: AsyncClient, monkeypatch) -> None:
    from mate.api.auth import keycloak_admin as ka

    calls: list[str] = []

    async def _fake_delete(self, user_id: str) -> ka.KeycloakDeleteResult:
        calls.append(user_id)
        return ka.KeycloakDeleteResult(deleted=True)

    monkeypatch.setattr(ka.KeycloakAdmin, "delete_user", _fake_delete)

    vid = await _seed_victim()
    resp = await admin_client.delete(f"/api/v1/admin/users/{vid}")
    assert resp.status_code == 200
    assert calls == [vid]
    assert resp.json()["keycloak_deleted"] is True


@pytest.mark.asyncio
async def test_keycloak_failure_is_warning_after_commit(
    admin_client: AsyncClient, monkeypatch
) -> None:
    from mate.api.auth import keycloak_admin as ka
    from mate.api.config import get_settings

    async def _boom(self, user_id: str) -> ka.KeycloakDeleteResult:
        raise ka.KeycloakAdminError("boom")

    monkeypatch.setattr(ka.KeycloakAdmin, "delete_user", _boom)

    vid = await _seed_victim()
    user_dir = get_settings().users_dir / vid
    resp = await admin_client.delete(f"/api/v1/admin/users/{vid}")
    # A Keycloak failure AFTER the DB commit must not 500 - it degrades to a warning.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["keycloak_deleted"] is False
    assert any("Keycloak" in w for w in body["warnings"])
    # DB + disk are still purged.
    assert (await _victim_row_counts(vid))["user"] == 0
    assert not user_dir.exists()


@pytest.mark.asyncio
async def test_no_keycloak_configured_reports_skip(admin_client: AsyncClient) -> None:
    from mate.api.auth.keycloak_admin import reset_for_tests

    reset_for_tests()  # ensure the default (unconfigured) client
    vid = await _seed_victim()
    resp = await admin_client.delete(f"/api/v1/admin/users/{vid}")
    assert resp.status_code == 200
    assert resp.json()["keycloak_skipped_reason"] == "not configured"


@pytest.mark.asyncio
async def test_user_detail_lists_owned_resources(admin_client: AsyncClient) -> None:
    vid = await _seed_victim()
    resp = await admin_client.get(f"/api/v1/admin/users/{vid}?include_disk=1")
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["id"] == vid
    assert len(d["event_logs"]) == 1
    assert len(d["dashboards"]) == 1
    assert len(d["watched_folders"]) == 1
    assert len(d["api_tokens"]) == 1
    assert len(d["teams"]) == 1
    assert d["shares_created"] == 1
    assert d["jobs"]["by_status"].get("completed", 0) == 1
    assert d["storage_bytes"] is not None and d["storage_bytes"] > 0

    # Clean up the seeded victim (session-shared DB).
    await admin_client.delete(f"/api/v1/admin/users/{vid}")


@pytest.mark.asyncio
async def test_user_detail_missing_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/api/v1/admin/users/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_protected_default_module_survives_last_owner(tmp_path) -> None:
    """The victim is the SOLE owner of a protected default module. Deleting them
    drops their install row but the shared module stays loaded (a protected
    default is never torn down, even by its last owner). Uses seed=False so the
    admin/test-user's own module state is untouched (no cross-test pollution)."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules import get_module_loader
    from mate.api.modules.installs import record_install, user_module_ids

    from .conftest import _sample_mod_client

    async with _sample_mod_client(tmp_path, seed=False, mod="sample_cards", admin=True) as c:
        vid = await _seed_victim()
        sm = get_sessionmaker()
        async with sm() as s:
            await record_install(s, vid, "sample_cards", "upload")
            await s.commit()
        async with sm() as s:
            assert "sample_cards" in await user_module_ids(s, vid)

        resp = await c.delete(f"/api/v1/admin/users/{vid}")
        assert resp.status_code == 200, resp.text

        loader = get_module_loader()
        assert "sample_cards" in loader.loaded  # protected default survives
        async with sm() as s:
            assert "sample_cards" not in await user_module_ids(s, vid)  # victim install gone
