"""/api/v1/preferences/{key} - per-user client-state blobs.

Backs the device-independent UI state that used to live in browser
localStorage (the ``useUi`` and ``useVizSettings`` zustand stores). Keying by
Keycloak user keeps one account's sidebar / visualisation prefs from bleeding
into another's on a shared browser, and lets them follow the user across
devices.

Only an allowlisted set of keys is accepted, so this can't be turned into
arbitrary unbounded per-user storage. Stored under namespaced
``user_settings`` rows (``pref.<key>``) to stay clear of the bespoke settings
keys (``analytics.config``, ``ai.config``, ``onboarding``, …).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from mate.api.auth import CurrentUserDep
from mate.api.db.models import UserSetting
from mate.api.db.session import SessionDep

router = APIRouter(prefix="/preferences", tags=["preferences"])

_ALLOWED_KEYS = frozenset({"ui", "viz"})
_KEY_PREFIX = "pref."


def _setting_key(key: str) -> str:
    if key not in _ALLOWED_KEYS:
        # 404 (not 400) so the valid-key set isn't enumerable.
        raise HTTPException(status_code=404, detail="Unknown preference key.")
    return _KEY_PREFIX + key


@router.get("/{key}")
async def get_preference(key: str, session: SessionDep, user: CurrentUserDep) -> dict[str, Any]:
    row = await session.get(UserSetting, (user.id, _setting_key(key)))
    if row is None or not isinstance(row.value_json, dict):
        # No saved blob → empty; the client falls back to store defaults.
        return {}
    return row.value_json


@router.put("/{key}")
async def put_preference(
    key: str,
    payload: dict[str, Any],
    session: SessionDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    setting_key = _setting_key(key)
    row = await session.get(UserSetting, (user.id, setting_key))
    if row is None:
        session.add(UserSetting(user_id=user.id, key=setting_key, value_json=payload))
    else:
        row.value_json = payload
    await session.commit()
    return payload
