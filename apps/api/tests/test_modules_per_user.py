"""Per-user module ownership: default seeding, restore, lock, upload guards.

Covers the multi-user module layer (§ per-user modules):

- a fresh user is auto-seeded the repo default set on first list;
- `restore-defaults` re-adds an intentionally-removed default;
- uninstalling a default removes only the user's row, never the repo code;
- uploads can't clobber a default id or another user's id.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from tests.conftest import TEST_USER_ID

_DEFAULTS_SEEDED_KEY = "modules_defaults_seeded"
OTHER_USER_ID = "00000000-0000-7000-8000-0000000000ff"


def _module_zip(module_id: str) -> bytes:
    """A minimal, valid uploadable module archive declaring *module_id*."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{module_id}/manifest.yaml",
            f"id: {module_id}\nname: {module_id}\nversion: 0.0.1\ncategory: foundation\n"
            "requirements:\n  event_log:\n    required_columns: [case_id, activity, timestamp]\n"
            "    min_events: 1\n    min_cases: 1\n"
            "provides: []\nconsumes: []\n"
            "dependencies:\n  python:\n    inherit: []\n    isolation: in_process\n",
        )
        zf.writestr(
            f"{module_id}/module.py",
            (
                "from mate.sdk import Module, ModuleContext, route\n\n"
                f"class TheModule(Module):\n"
                f'    id = "{module_id}"\n\n'
                '    @route.get("/ping")\n'
                "    async def ping(self, ctx: ModuleContext) -> dict[str, str]:\n"
                '        return {"id": ctx.module_id}\n'
            ),
        )
    return buf.getvalue()


async def _reset_user_modules(user_id: str = TEST_USER_ID) -> None:
    """Clear a user's install rows + the seeded flag for a deterministic slate."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import ModuleInstall, UserSetting

    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(delete(ModuleInstall).where(ModuleInstall.user_id == user_id))
        await s.execute(
            delete(UserSetting).where(
                UserSetting.user_id == user_id, UserSetting.key == _DEFAULTS_SEEDED_KEY
            )
        )
        await s.commit()


async def _install_row_source(user_id: str, module_id: str) -> str | None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import ModuleInstall

    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(ModuleInstall, (user_id, module_id))
        return None if row is None else row.source


async def _set_seeded_record(value, user_id: str = TEST_USER_ID) -> None:
    """Force the per-user "defaults already offered" record to *value* (a list,
    a legacy bare ``True``, or ``None`` to clear)."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import UserSetting

    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(UserSetting, (user_id, _DEFAULTS_SEEDED_KEY))
        if value is None:
            if row is not None:
                await s.delete(row)
        elif row is None:
            s.add(UserSetting(user_id=user_id, key=_DEFAULTS_SEEDED_KEY, value_json=value))
        else:
            row.value_json = value
        await s.commit()


async def _seeded_record(user_id: str = TEST_USER_ID):
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import UserSetting

    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(UserSetting, (user_id, _DEFAULTS_SEEDED_KEY))
        return None if row is None else row.value_json


async def _await_job(client: AsyncClient, job_id: str) -> dict:
    for _ in range(50):
        d = await client.get(f"/api/v1/jobs/{job_id}")
        if d.status_code == 200 and d.json()["status"] in {"completed", "failed"}:
            return d.json()
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} did not settle")


@pytest.mark.asyncio
async def test_list_modules_auto_seeds_defaults(
    client_with_sample_mod_fresh: AsyncClient,
) -> None:
    """A user with no install rows gets the repo defaults on first list."""
    await _reset_user_modules()

    resp = await client_with_sample_mod_fresh.get("/api/v1/modules")
    assert resp.status_code == 200
    assert "sample_mod" in [m["id"] for m in resp.json()]

    # The seeded row is tagged source="default" (not "upload").
    assert await _install_row_source(TEST_USER_ID, "sample_mod") == "default"


@pytest.mark.asyncio
async def test_list_grants_newly_bundled_default_to_seeded_user(
    client_with_sample_mod_fresh: AsyncClient,
) -> None:
    """A default that appears *after* a user was seeded is granted on next list.

    Simulate an already-seeded user whose recorded set predates `sample_mod`
    (e.g. it was bundled later): listing should grant it and extend the record.
    """
    await _reset_user_modules()
    # User previously seeded with some other default; `sample_mod` is "new".
    await _set_seeded_record(["some_old_default"])

    resp = await client_with_sample_mod_fresh.get("/api/v1/modules")
    assert resp.status_code == 200
    assert "sample_mod" in [m["id"] for m in resp.json()]
    assert await _install_row_source(TEST_USER_ID, "sample_mod") == "default"
    assert "sample_mod" in (await _seeded_record())


@pytest.mark.asyncio
async def test_list_legacy_bool_flag_migrates_and_reconciles(
    client_with_sample_mod_fresh: AsyncClient,
) -> None:
    """A legacy one-shot ``True`` flag reconciles the full set once, then becomes
    an id list so removals stick afterwards."""
    await _reset_user_modules()
    await _set_seeded_record(True)  # legacy flag, no ids recorded

    resp = await client_with_sample_mod_fresh.get("/api/v1/modules")
    assert resp.status_code == 200
    assert await _install_row_source(TEST_USER_ID, "sample_mod") == "default"
    record = await _seeded_record()
    assert isinstance(record, list) and "sample_mod" in record


@pytest.mark.asyncio
async def test_list_does_not_resurrect_removed_default(
    client_with_sample_mod: AsyncClient,
) -> None:
    """Once a default is in the recorded set, uninstalling it and re-listing must
    not bring it back (only explicit restore-defaults does)."""
    # First list records `sample_mod` as offered.
    assert (await client_with_sample_mod.get("/api/v1/modules")).status_code == 200
    assert "sample_mod" in (await _seeded_record())

    deleted = await client_with_sample_mod.delete("/api/v1/modules/sample_mod")
    assert deleted.status_code == 204

    resp = await client_with_sample_mod.get("/api/v1/modules")
    assert resp.status_code == 200
    assert "sample_mod" not in [m["id"] for m in resp.json()]
    assert await _install_row_source(TEST_USER_ID, "sample_mod") is None


@pytest.mark.asyncio
async def test_restore_defaults_re_adds_removed_default(
    client_with_sample_mod: AsyncClient,
) -> None:
    """Uninstalling a default (unlocked) then restoring re-grants ownership."""
    # Remove the default for this user.
    deleted = await client_with_sample_mod.delete("/api/v1/modules/sample_mod")
    assert deleted.status_code == 204
    assert await _install_row_source(TEST_USER_ID, "sample_mod") is None

    restored = await client_with_sample_mod.post("/api/v1/modules/restore-defaults")
    assert restored.status_code == 200
    assert "sample_mod" in restored.json()["restored"]
    assert await _install_row_source(TEST_USER_ID, "sample_mod") == "default"


@pytest.mark.asyncio
async def test_uninstall_default_keeps_repo_files(
    client_with_sample_mod: AsyncClient,
) -> None:
    """Uninstalling a default drops only the user's row - repo code survives."""
    from mate.api.config import get_settings

    module_py = get_settings().modules_dir / "sample_mod" / "module.py"
    assert module_py.exists()

    deleted = await client_with_sample_mod.delete("/api/v1/modules/sample_mod")
    assert deleted.status_code == 204

    # Ownership row gone, but the shared on-disk default code is untouched.
    assert await _install_row_source(TEST_USER_ID, "sample_mod") is None
    assert module_py.exists()


@pytest.mark.asyncio
async def test_upload_rejects_default_id(
    client_with_sample_mod: AsyncClient,
) -> None:
    """An upload whose id collides with a default must fail loudly and never
    overwrite the default's repo code."""
    from mate.api.config import get_settings

    module_py = get_settings().modules_dir / "sample_mod" / "module.py"
    before = module_py.read_text()

    resp = await client_with_sample_mod.post(
        "/api/v1/modules/install",
        files={"file": ("sample_mod.zip", _module_zip("sample_mod"), "application/zip")},
    )
    assert resp.status_code == 202
    body = await _await_job(client_with_sample_mod, resp.json()["job_id"])
    assert body["status"] == "failed", body
    msg = ((body.get("message") or "") + (body.get("error") or "")).lower()
    assert "default" in msg

    # The repo default's code was not clobbered.
    assert module_py.read_text() == before


@pytest.mark.asyncio
async def test_upload_rejects_other_users_id(
    client_with_sample_mod: AsyncClient,
) -> None:
    """An upload whose id is already owned by another user must fail."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import ModuleInstall, User

    sm = get_sessionmaker()
    async with sm() as s:
        if await s.get(User, OTHER_USER_ID) is None:
            s.add(User(id=OTHER_USER_ID, email="other@mate.local"))
            await s.flush()
        if await s.get(ModuleInstall, (OTHER_USER_ID, "foreign_mod")) is None:
            s.add(ModuleInstall(user_id=OTHER_USER_ID, module_id="foreign_mod", source="upload"))
        await s.commit()

    resp = await client_with_sample_mod.post(
        "/api/v1/modules/install",
        files={"file": ("foreign_mod.zip", _module_zip("foreign_mod"), "application/zip")},
    )
    assert resp.status_code == 202
    body = await _await_job(client_with_sample_mod, resp.json()["job_id"])
    assert body["status"] == "failed", body
    msg = ((body.get("message") or "") + (body.get("error") or "")).lower()
    assert "another user" in msg or "in use" in msg
