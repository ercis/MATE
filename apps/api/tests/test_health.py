from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_modules_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/modules")
    assert resp.status_code == 200
    assert resp.json() == []
