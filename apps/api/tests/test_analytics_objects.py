"""OCEL object materialisation for the UI log (Abb & Rehse reference model).

Ingesting a client batch must upsert the object registry (ui_element,
ui_group, application, system, user, task), write qualified E2O rows per
event, and record the static ``part_of`` hierarchy - with stable object ids
across sessions and dynamic path segments. Wipe removes all of it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID

UUID_A = "123e4567-e89b-12d3-a456-426614174000"
UUID_B = "9f0e8d7c-6b5a-4321-8765-4321fedcba98"


def _click_batch(
    anon: str, *, session_id: str, path: str, event_name: str = "click"
) -> dict[str, Any]:
    return {
        "session": {
            "id": session_id,
            "anon_user_id": anon,
            "started_at": datetime.now(UTC).isoformat(),
            "ua_class": "chromium-mac",
        },
        "events": [
            {
                "event_type": "click",
                "event_name": event_name,
                "occurred_at": datetime.now(UTC).isoformat(),
                "path": path,
                "properties": {
                    "kind": "click",
                    "activity": 'click button "Import"',
                    "selector": 'main>form#import-form>button[data-testid="import"]',
                    "target": {
                        "tag": "button",
                        "id": None,
                        "testid": "import",
                        "role": "button",
                        "label": "Import",
                        "text": "Import",
                        "type": None,
                    },
                    "state": {"disabled": False},
                    "ui_groups": [
                        {"kind": "form", "id": "import-form", "label": "Import"},
                        {"kind": "main", "id": None, "label": None},
                    ],
                },
            }
        ],
    }


async def _seed(client: AsyncClient) -> str:
    cfg = (await client.get("/api/v1/usage/config")).json()
    return str(cfg["anon_user_id_seed"])


async def _objects_by_type() -> dict[str, list[str]]:
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsObject

    sm = get_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(
                select(AnalyticsObject.object_type, AnalyticsObject.object_id).where(
                    AnalyticsObject.user_id == TEST_USER_ID
                )
            )
        ).all()
    out: dict[str, list[str]] = {}
    for otype, oid in rows:
        out.setdefault(str(otype), []).append(str(oid))
    return out


async def _relations() -> set[tuple[str, str, str]]:
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsObjectRelation

    sm = get_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(
                select(
                    AnalyticsObjectRelation.src_object_id,
                    AnalyticsObjectRelation.tgt_object_id,
                    AnalyticsObjectRelation.qualifier,
                ).where(AnalyticsObjectRelation.user_id == TEST_USER_ID)
            )
        ).all()
    return {(str(s), str(t), str(q)) for s, t, q in rows}


async def _e2o_qualifiers() -> set[str]:
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsEventObject

    sm = get_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(
                select(AnalyticsEventObject.qualifier).where(
                    AnalyticsEventObject.user_id == TEST_USER_ID
                )
            )
        ).all()
    return {str(q) for (q,) in rows}


@pytest.mark.asyncio
async def test_ingest_materialises_object_layer(client: AsyncClient) -> None:
    anon = await _seed(client)
    resp = await client.post(
        "/api/v1/usage/sync",
        json=_click_batch(anon, session_id="obj-s1", path=f"/processes/{UUID_A}"),
    )
    assert resp.status_code == 202

    objs = await _objects_by_type()
    assert len(objs.get("ui_element", [])) == 1
    assert len(objs.get("ui_group", [])) == 2
    assert objs.get("application") == ["app:mate-web"]
    assert len(objs.get("system", [])) == 1
    assert objs.get("user") == [f"user:{anon}"]
    assert objs.get("task") == ["task:processes"]

    # E2O: one qualified edge per hierarchy role.
    assert {
        "target",
        "context",
        "application",
        "system",
        "performed_by",
        "task",
    } <= await _e2o_qualifiers()

    # O2O part_of chain: elem -> inner group -> outer group -> app -> system.
    rels = await _relations()
    elem = objs["ui_element"][0]
    groups = set(objs["ui_group"])
    system = objs["system"][0]
    elem_parents = {t for s, t, q in rels if s == elem and q == "part_of"}
    assert elem_parents and elem_parents <= groups
    assert ("app:mate-web", system, "part_of") in rels
    group_parents = {t for s, t, q in rels if s in groups and q == "part_of"}
    assert "app:mate-web" in group_parents


@pytest.mark.asyncio
async def test_object_ids_stable_across_sessions_and_dynamic_paths(
    client: AsyncClient,
) -> None:
    anon = await _seed(client)
    # Same element on two detail pages of different resources, two sessions -
    # the uuid segment is normalised out, so one ui_element row results.
    for sess, path in (("obj-s2", f"/processes/{UUID_A}"), ("obj-s3", f"/processes/{UUID_B}")):
        resp = await client.post(
            "/api/v1/usage/sync", json=_click_batch(anon, session_id=sess, path=path)
        )
        assert resp.status_code == 202

    objs = await _objects_by_type()
    assert len(objs.get("ui_element", [])) == 1
    assert len(objs.get("task", [])) == 1


@pytest.mark.asyncio
async def test_wipe_removes_object_layer(client: AsyncClient) -> None:
    anon = await _seed(client)
    resp = await client.post(
        "/api/v1/usage/sync",
        json=_click_batch(anon, session_id="obj-s4", path="/dashboards"),
    )
    assert resp.status_code == 202
    assert await _objects_by_type()

    wipe = await client.delete("/api/v1/usage/sync")
    assert wipe.status_code == 200
    assert wipe.json()["deleted_events"] >= 1

    assert await _objects_by_type() == {}
    assert await _relations() == set()
    assert await _e2o_qualifiers() == set()


async def _purge_analytics_state() -> None:
    """Remove the test user's analytics rows + config and drop the consent cache.

    Runs before AND after each test: other suites emit server events (jobs,
    MCP audit, operations) for the same shared test user, and the assertions
    here are exact-count, so a dirty starting state would flake.
    """
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import (
        AnalyticsEvent,
        AnalyticsObject,
        AnalyticsObjectRelation,
        AnalyticsSession,
        UserSetting,
    )
    from mate.api.routes.analytics import ANALYTICS_CONFIG_KEY, _consent_cache

    _consent_cache.clear()
    sm = get_sessionmaker()
    async with sm() as session:
        for model in (AnalyticsEvent, AnalyticsObject, AnalyticsObjectRelation, AnalyticsSession):
            await session.execute(delete(model).where(model.user_id == TEST_USER_ID))
        await session.execute(
            delete(UserSetting).where(
                UserSetting.user_id == TEST_USER_ID,
                UserSetting.key == ANALYTICS_CONFIG_KEY,
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _reset_state() -> AsyncIterator[None]:
    await _purge_analytics_state()
    yield
    await _purge_analytics_state()
