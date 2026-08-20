"""Dashboard sharing + teams - access control across users.

Drives several identities through one app by swapping the ``get_current_user``
override mid-test, so we can assert owner / recipient / admin / stranger each see
exactly what they should.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_ID

OWNER_ID = TEST_USER_ID
RECIPIENT_ID = "00000000-0000-7000-8000-0000000000a2"
ADMIN_ID = "00000000-0000-7000-8000-0000000000a3"
STRANGER_ID = "00000000-0000-7000-8000-0000000000a4"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _user(uid: str, roles: tuple[str, ...] = ("user",)):
    from mate.api.auth.dependencies import CurrentUser

    return CurrentUser(
        id=uid,
        email=f"{uid}@mate.local",
        preferred_username=uid[:8],
        name=uid[:8],
        roles=roles,
    )


async def _seed_users() -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import User

    sm = get_sessionmaker()
    async with sm() as s:
        for uid in (RECIPIENT_ID, ADMIN_ID, STRANGER_ID):
            if await s.get(User, uid) is None:
                s.add(
                    User(
                        id=uid,
                        email=f"{uid}@mate.local",
                        preferred_username=uid[:8],
                        name=uid[:8],
                        created_at=_now(),
                        last_seen_at=_now(),
                    )
                )
        await s.commit()


@contextlib.asynccontextmanager
async def _multi_user_client() -> AsyncIterator[tuple[AsyncClient, dict]]:
    from mate.api.auth.dependencies import get_current_user
    from mate.api.main import create_app

    app = create_app()
    state = {"user": _user(OWNER_ID)}

    async def _current():
        return state["user"]

    app.dependency_overrides[get_current_user] = _current
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as c,
        app.router.lifespan_context(app),
    ):
        await _seed_users()
        yield c, state


def _act_as(state: dict, uid: str, roles: tuple[str, ...] = ("user",)) -> None:
    state["user"] = _user(uid, roles)


@pytest.mark.asyncio
async def test_team_share_grants_view_not_edit() -> None:
    async with _multi_user_client() as (c, state):
        _act_as(state, OWNER_ID)
        r = await c.post("/api/v1/dashboards", json={"name": "Shared board"})
        assert r.status_code == 201, r.text
        dash_id = r.json()["id"]

        # Admin builds a team containing owner + recipient.
        _act_as(state, ADMIN_ID, roles=("admin",))
        r = await c.post("/api/v1/admin/teams", json={"name": "Analysts"})
        assert r.status_code == 201, r.text
        team_id = r.json()["id"]
        for uid in (OWNER_ID, RECIPIENT_ID):
            r = await c.post(f"/api/v1/admin/teams/{team_id}/members", json={"user_id": uid})
            assert r.status_code == 201, r.text

        # Owner shares with the whole team.
        _act_as(state, OWNER_ID)
        r = await c.post(f"/api/v1/dashboards/{dash_id}/shares", json={"target_team_id": team_id})
        assert r.status_code == 201, r.text

        # Recipient: appears in their inbox, viewable read-only, not editable.
        _act_as(state, RECIPIENT_ID)
        r = await c.get("/api/v1/sharing/shared-with-me")
        assert r.status_code == 200
        assert dash_id in [d["id"] for d in r.json()]
        r = await c.get(f"/api/v1/dashboards/{dash_id}")
        assert r.status_code == 200, r.text
        assert r.json()["is_owner"] is False
        assert (
            await c.patch(f"/api/v1/dashboards/{dash_id}", json={"name": "x"})
        ).status_code == 404
        assert (await c.delete(f"/api/v1/dashboards/{dash_id}")).status_code == 404

        # The recipient can now see the team as a share target (co-membership).
        r = await c.get("/api/v1/sharing/targets")
        assert r.status_code == 200
        assert any(t["kind"] == "team" and t["id"] == team_id for t in r.json())

        # Stranger: nothing.
        _act_as(state, STRANGER_ID)
        assert (await c.get("/api/v1/sharing/shared-with-me")).json() == []
        assert (await c.get(f"/api/v1/dashboards/{dash_id}")).status_code == 404


@pytest.mark.asyncio
async def test_direct_share_dedup_and_revoke() -> None:
    async with _multi_user_client() as (c, state):
        _act_as(state, OWNER_ID)
        dash_id = (await c.post("/api/v1/dashboards", json={"name": "Direct"})).json()["id"]

        # A direct share is only allowed with a teammate - set up a shared team.
        _act_as(state, ADMIN_ID, roles=("admin",))
        team_id = (await c.post("/api/v1/admin/teams", json={"name": "T"})).json()["id"]
        for uid in (OWNER_ID, RECIPIENT_ID):
            await c.post(f"/api/v1/admin/teams/{team_id}/members", json={"user_id": uid})

        _act_as(state, OWNER_ID)
        # Sharing with a non-teammate is refused.
        r = await c.post(
            f"/api/v1/dashboards/{dash_id}/shares", json={"target_user_id": STRANGER_ID}
        )
        assert r.status_code == 403, r.text

        r = await c.post(
            f"/api/v1/dashboards/{dash_id}/shares", json={"target_user_id": RECIPIENT_ID}
        )
        assert r.status_code == 201, r.text
        share_id = r.json()["id"]
        # Duplicate target rejected.
        r = await c.post(
            f"/api/v1/dashboards/{dash_id}/shares", json={"target_user_id": RECIPIENT_ID}
        )
        assert r.status_code == 409
        # Sharing with yourself rejected.
        r = await c.post(f"/api/v1/dashboards/{dash_id}/shares", json={"target_user_id": OWNER_ID})
        assert r.status_code == 400

        _act_as(state, RECIPIENT_ID)
        assert (await c.get(f"/api/v1/dashboards/{dash_id}")).status_code == 200

        _act_as(state, OWNER_ID)
        assert (
            await c.delete(f"/api/v1/dashboards/{dash_id}/shares/{share_id}")
        ).status_code == 204

        _act_as(state, RECIPIENT_ID)
        assert (await c.get(f"/api/v1/dashboards/{dash_id}")).status_code == 404


@pytest.mark.asyncio
async def test_admin_routes_require_admin_role() -> None:
    async with _multi_user_client() as (c, state):
        _act_as(state, OWNER_ID)  # plain user
        assert (await c.get("/api/v1/admin/teams")).status_code == 403
        assert (await c.post("/api/v1/admin/teams", json={"name": "X"})).status_code == 403
        assert (await c.get("/api/v1/admin/dashboard-shares")).status_code == 403


@pytest.mark.asyncio
async def test_user_can_read_log_follows_share() -> None:
    """The loader's cross-account data gate: reading a log via a shared dashboard."""
    async with _multi_user_client() as (_c, _state):
        from mate.api.db.engine import get_sessionmaker
        from mate.api.db.models import Dashboard, DashboardShare, EventLog
        from mate.api.sharing import user_can_read_log
        from mate.api.uuid7 import uuid7_str

        log_id, dash_id = uuid7_str(), uuid7_str()
        sm = get_sessionmaker()
        async with sm() as s:
            # A real log row - the dashboard's event_log_id FK is enforced.
            # Flush it before the dashboard so the parent INSERT lands first.
            s.add(EventLog(id=log_id, user_id=OWNER_ID, name="L", created_at=_now()))
            await s.flush()
            s.add(
                Dashboard(
                    id=dash_id,
                    user_id=OWNER_ID,
                    name="bound",
                    event_log_id=log_id,
                    log_model="case_centric",
                    layout_json={},
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            await s.commit()

        async with sm() as s:
            assert await user_can_read_log(s, log_id, RECIPIENT_ID) is False
            s.add(
                DashboardShare(
                    id=uuid7_str(),
                    dashboard_id=dash_id,
                    target_user_id=RECIPIENT_ID,
                    created_by=OWNER_ID,
                    created_at=_now(),
                )
            )
            await s.commit()

        async with sm() as s:
            assert await user_can_read_log(s, log_id, RECIPIENT_ID) is True
            assert await user_can_read_log(s, log_id, STRANGER_ID) is False
            # The owner isn't a "share recipient" - the predicate is cross-account only.
            assert await user_can_read_log(s, log_id, OWNER_ID) is False
