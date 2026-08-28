"""Admin cross-user module dashboard + controls (``/admin/modules``).

Covers the admin surface added for module administration:
- the dashboard lists every module joined to its owners (+ best-effort uploader);
- non-admins are rejected;
- a bundled module is always default and can't be un-defaulted;
- force-install / force-uninstall of a module for an individual user;
- declaring an *uploaded* (non-bundled) module a default eager-seeds every user.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_USER_ID, _sample_mod_client
from tests.test_modules_per_user import (
    OTHER_USER_ID,
    _install_row_source,
    _reset_user_modules,
    _upload,
)


async def _ensure_user(user_id: str) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import User

    sm = get_sessionmaker()
    async with sm() as s:
        if await s.get(User, user_id) is None:
            s.add(User(id=user_id, email=f"{user_id}@mate.local", preferred_username="other"))
            await s.commit()


async def _admin_default_ids() -> set[str]:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.defaults import get_admin_default_ids

    sm = get_sessionmaker()
    async with sm() as s:
        return await get_admin_default_ids(s)


async def _excluded_default_ids() -> set[str]:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules.defaults import get_excluded_default_ids

    sm = get_sessionmaker()
    async with sm() as s:
        return await get_excluded_default_ids(s)


def _row(rows: list[dict], module_id: str) -> dict | None:
    return next((r for r in rows if r["id"] == module_id), None)


@pytest.fixture
async def admin_client(tmp_path):
    async with _sample_mod_client(tmp_path, seed=True, mod="sample_mod", admin=True) as c:
        yield c


@pytest.mark.asyncio
async def test_admin_modules_requires_admin(client_with_sample_mod: AsyncClient) -> None:
    resp = await client_with_sample_mod.get("/api/v1/admin/modules")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_lists_owners_and_uploader(admin_client: AsyncClient) -> None:
    resp = await admin_client.get("/api/v1/admin/modules")
    assert resp.status_code == 200
    row = _row(resp.json(), "sample_mod")
    assert row is not None
    assert row["is_bundled"] is True
    assert row["is_default"] is True
    assert row["default_locked"] is True
    assert row["owner_count"] >= 1
    assert TEST_USER_ID in [o["user_id"] for o in row["owners"]]
    # The seed records source="upload", so the test user is the best-effort uploader.
    assert row["uploaded_by"] is not None
    assert row["uploaded_by"]["user_id"] == TEST_USER_ID


@pytest.mark.asyncio
async def test_bundled_cannot_be_undefaulted(admin_client: AsyncClient) -> None:
    resp = await admin_client.put(
        "/api/v1/admin/modules/sample_mod/default", json={"is_default": False}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_force_install_validation(admin_client: AsyncClient) -> None:
    await _ensure_user(OTHER_USER_ID)
    # Unknown module.
    resp = await admin_client.post(
        "/api/v1/admin/modules/nope/installs", json={"user_id": OTHER_USER_ID}
    )
    assert resp.status_code == 404
    # Unknown user.
    resp = await admin_client.post(
        "/api/v1/admin/modules/sample_mod/installs", json={"user_id": "no-such-user"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_force_install_then_uninstall(admin_client: AsyncClient) -> None:
    await _ensure_user(OTHER_USER_ID)

    resp = await admin_client.post(
        "/api/v1/admin/modules/sample_mod/installs", json={"user_id": OTHER_USER_ID}
    )
    assert resp.status_code == 200, resp.text
    assert OTHER_USER_ID in [o["user_id"] for o in resp.json()["owners"]]
    assert await _install_row_source(OTHER_USER_ID, "sample_mod") == "admin"

    resp = await admin_client.delete(f"/api/v1/admin/modules/sample_mod/installs/{OTHER_USER_ID}")
    assert resp.status_code == 204
    assert await _install_row_source(OTHER_USER_ID, "sample_mod") is None

    # sample_mod is a bundled default → its shared code survives the last per-user
    # removal (protected from teardown).
    from mate.api.modules import get_module_loader

    assert "sample_mod" in get_module_loader().loaded


@pytest.mark.asyncio
async def test_declare_uploaded_module_default_seeds_all_users(admin_client: AsyncClient) -> None:
    body = await _upload(admin_client, "extra_mod")
    assert body["status"] == "completed", body

    await _ensure_user(OTHER_USER_ID)
    assert await _install_row_source(OTHER_USER_ID, "extra_mod") is None

    resp = await admin_client.put(
        "/api/v1/admin/modules/extra_mod/default", json={"is_default": True}
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["is_bundled"] is False
    assert row["is_default"] is True
    assert row["default_locked"] is False

    # Eager-seeded to every existing user, and recorded in the admin default set.
    assert await _install_row_source(OTHER_USER_ID, "extra_mod") == "default"
    assert "extra_mod" in await _admin_default_ids()

    # Un-declaring only leaves the mandate set - existing installs are untouched.
    resp = await admin_client.put(
        "/api/v1/admin/modules/extra_mod/default", json={"is_default": False}
    )
    assert resp.status_code == 200
    assert resp.json()["is_default"] is False
    assert await _install_row_source(OTHER_USER_ID, "extra_mod") == "default"
    assert "extra_mod" not in await _admin_default_ids()


@pytest.mark.asyncio
async def test_withhold_bundled_default_from_new_users(admin_client: AsyncClient) -> None:
    """A withheld bundled default is not auto-seeded to a not-yet-seeded user,
    while staying a platform default (still ``is_default``/``default_locked``)."""
    resp = await admin_client.put(
        "/api/v1/admin/modules/sample_mod/withhold", json={"withheld": True}
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["withheld_from_new_users"] is True
    assert row["is_default"] is True and row["default_locked"] is True
    assert "sample_mod" in await _excluded_default_ids()

    # A fresh user (no install rows, no seeded record) is NOT seeded the withheld
    # default when they list modules.
    await _reset_user_modules()
    resp = await admin_client.get("/api/v1/modules")
    assert resp.status_code == 200
    assert "sample_mod" not in [m["id"] for m in resp.json()]
    assert await _install_row_source(TEST_USER_ID, "sample_mod") is None

    # Un-withholding lets the next list seed it again.
    resp = await admin_client.put(
        "/api/v1/admin/modules/sample_mod/withhold", json={"withheld": False}
    )
    assert resp.status_code == 200
    assert resp.json()["withheld_from_new_users"] is False
    assert "sample_mod" not in await _excluded_default_ids()

    resp = await admin_client.get("/api/v1/modules")
    assert resp.status_code == 200
    assert "sample_mod" in [m["id"] for m in resp.json()]
    assert await _install_row_source(TEST_USER_ID, "sample_mod") == "default"


@pytest.mark.asyncio
async def test_withhold_keeps_existing_owner(admin_client: AsyncClient) -> None:
    """Withholding never removes the module from a user who already owns it."""
    # The current user already owns sample_mod (pre-seeded by the fixture).
    assert (await admin_client.get("/api/v1/modules")).status_code == 200
    owned_before = await _install_row_source(TEST_USER_ID, "sample_mod")
    assert owned_before is not None

    resp = await admin_client.put(
        "/api/v1/admin/modules/sample_mod/withhold", json={"withheld": True}
    )
    assert resp.status_code == 200

    # Existing owner keeps it (same ownership row) after withholding + re-listing.
    resp = await admin_client.get("/api/v1/modules")
    assert resp.status_code == 200
    assert "sample_mod" in [m["id"] for m in resp.json()]
    assert await _install_row_source(TEST_USER_ID, "sample_mod") == owned_before


@pytest.mark.asyncio
async def test_withhold_non_default_rejected(admin_client: AsyncClient) -> None:
    """Only default modules can be withheld; a plain uploaded module 400s."""
    body = await _upload(admin_client, "plain_mod")
    assert body["status"] == "completed", body
    resp = await admin_client.put(
        "/api/v1/admin/modules/plain_mod/withhold", json={"withheld": True}
    )
    assert resp.status_code == 400
