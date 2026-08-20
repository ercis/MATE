"""Per-user client-state blobs backing the ui / viz zustand stores."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_preferences_roundtrip_and_allowlist(client: AsyncClient) -> None:
    # Non-allowlisted keys 404 (and don't reveal the valid set).
    assert (await client.get("/api/v1/preferences/secrets")).status_code == 404
    assert (await client.put("/api/v1/preferences/secrets", json={"x": 1})).status_code == 404

    # An allowlisted key with nothing saved → empty object; the client falls
    # back to store defaults (so a new user starts clean, not with someone
    # else's prefs).
    assert (await client.get("/api/v1/preferences/ui")).json() == {}

    # Roundtrip persists per-user.
    body = {"sidebarCollapsed": True, "timezone": "Europe/Zurich"}
    put = await client.put("/api/v1/preferences/ui", json=body)
    assert put.status_code == 200
    assert (await client.get("/api/v1/preferences/ui")).json() == body

    # Keys are independent - saving ui doesn't touch viz.
    assert (await client.get("/api/v1/preferences/viz")).json() == {}
