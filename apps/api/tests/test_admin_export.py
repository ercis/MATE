"""Admin behaviour-export - filtered cross-user export gated by ``admin``.

Mirrors ``test_admin_insights.py``: the default ``client`` user holds only the
``user`` role (exercises the 403 path); ``admin_client`` re-overrides
``get_current_user`` with an admin (exercises the 200/filter paths). Events are
seeded directly into the DB across two users / types / dates so the filters can
be asserted independently of the ingest gate.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_EMAIL, TEST_USER_ID

# Second user - seeded so user_id filtering has something to discriminate on.
OTHER_USER_ID = "00000000-0000-7000-8000-0000000000a2"
OTHER_USER_EMAIL = "other@mate.local"


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


@pytest.fixture
async def seeded_events() -> AsyncIterator[str]:
    """Seed a known event set across two users, two types, two days.

    Yields a unique marker embedded in ``event_name`` / ``path`` so each test can
    isolate its own rows from any others in the shared session DB. Cleaned up
    afterwards (events + the second user).
    """
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsEvent, User

    marker = uuid.uuid4().hex[:8]
    sm = get_sessionmaker()
    sess_a = f"sess-a-{marker}"
    sess_b = f"sess-b-{marker}"

    def _ev(**kw: object) -> AnalyticsEvent:
        base: dict[str, object] = {
            "anon_user_id": f"anon-{marker}",
            "source": "client",
            "server_received_at": datetime(2026, 1, 10, tzinfo=UTC).replace(tzinfo=None),
        }
        base.update(kw)
        return AnalyticsEvent(**base)  # type: ignore[arg-type]

    async with sm() as session:
        if await session.get(User, OTHER_USER_ID) is None:
            session.add(
                User(
                    id=OTHER_USER_ID,
                    email=OTHER_USER_EMAIL,
                    preferred_username="other",
                    name="Other User",
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    last_seen_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            # Flush the user before the events - there's no ORM relationship
            # between them, so the unit of work can't infer the FK insert order.
            await session.flush()
        session.add_all(
            [
                # user TEST: a click on /processes (day 1) + a page on /models (day 2)
                _ev(
                    user_id=TEST_USER_ID,
                    session_id=sess_a,
                    event_type="click",
                    event_name=f"click.{marker}",
                    path=f"/processes/{marker}",
                    occurred_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC).replace(tzinfo=None),
                    properties={"kind": "click", "marker": marker},
                ),
                _ev(
                    user_id=TEST_USER_ID,
                    session_id=sess_a,
                    event_type="page",
                    event_name=f"page.{marker}",
                    path=f"/models/{marker}",
                    occurred_at=datetime(2026, 1, 12, 9, 0, tzinfo=UTC).replace(tzinfo=None),
                ),
                # user OTHER: a single click on /settings (day 1)
                _ev(
                    user_id=OTHER_USER_ID,
                    session_id=sess_b,
                    event_type="click",
                    event_name=f"click.{marker}",
                    path=f"/settings/{marker}",
                    occurred_at=datetime(2026, 1, 10, 10, 0, tzinfo=UTC).replace(tzinfo=None),
                ),
            ]
        )
        await session.commit()

    try:
        yield marker
    finally:
        async with sm() as session:
            await session.execute(
                delete(AnalyticsEvent).where(AnalyticsEvent.anon_user_id == f"anon-{marker}")
            )
            await session.execute(delete(User).where(User.id == OTHER_USER_ID))
            await session.commit()


# --------------------------------------------------------------------------
# Access control - every new route is admin-gated
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_routes_require_admin(client: AsyncClient) -> None:
    for path in (
        "/api/v1/admin/export/event-log.xes",
        "/api/v1/admin/export/events.ndjson",
        "/api/v1/admin/export/events.csv",
        "/api/v1/admin/export/preview",
        "/api/v1/admin/export/facets",
    ):
        resp = await client.get(path)
        assert resp.status_code == 403, path


# --------------------------------------------------------------------------
# NDJSON filtering
# --------------------------------------------------------------------------


def _ndjson_rows(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_ndjson_filter_by_user(admin_client: AsyncClient, seeded_events: str) -> None:
    marker = seeded_events
    resp = await admin_client.get(f"/api/v1/admin/export/events.ndjson?user_id={OTHER_USER_ID}")
    assert resp.status_code == 200
    rows = [r for r in _ndjson_rows(resp.text) if r.get("anon_user_id") == f"anon-{marker}"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == OTHER_USER_ID
    assert rows[0]["path"] == f"/settings/{marker}"


@pytest.mark.asyncio
async def test_ndjson_filter_by_event_type(admin_client: AsyncClient, seeded_events: str) -> None:
    marker = seeded_events
    resp = await admin_client.get(
        f"/api/v1/admin/export/events.ndjson?event_type=click&event_name=click.{marker}"
    )
    assert resp.status_code == 200
    rows = _ndjson_rows(resp.text)
    assert len(rows) == 2  # both users' clicks share the marker event name
    assert all(r["event_type"] == "click" for r in rows)


@pytest.mark.asyncio
async def test_ndjson_filter_by_date_range(admin_client: AsyncClient, seeded_events: str) -> None:
    marker = seeded_events
    # Half-open [2026-01-11, 2026-01-13) - only the day-2 /models page event.
    resp = await admin_client.get(
        "/api/v1/admin/export/events.ndjson?start=2026-01-11T00:00:00&end=2026-01-13T00:00:00"
    )
    assert resp.status_code == 200
    rows = [r for r in _ndjson_rows(resp.text) if r.get("anon_user_id") == f"anon-{marker}"]
    assert len(rows) == 1
    assert rows[0]["event_name"] == f"page.{marker}"


@pytest.mark.asyncio
async def test_ndjson_filter_by_path_prefix(admin_client: AsyncClient, seeded_events: str) -> None:
    marker = seeded_events
    resp = await admin_client.get("/api/v1/admin/export/events.ndjson?path_prefix=/processes")
    assert resp.status_code == 200
    rows = [r for r in _ndjson_rows(resp.text) if r.get("anon_user_id") == f"anon-{marker}"]
    assert len(rows) == 1
    assert rows[0]["path"] == f"/processes/{marker}"


# --------------------------------------------------------------------------
# CSV filtering
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_export_well_formed_and_filtered(
    admin_client: AsyncClient, seeded_events: str
) -> None:
    import csv
    import io

    marker = seeded_events
    resp = await admin_client.get(
        f"/api/v1/admin/export/events.csv?user_id={TEST_USER_ID}&event_type=click"
        f"&event_name=click.{marker}"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    assert reader.fieldnames is not None
    assert "properties" in reader.fieldnames
    rows = [r for r in reader if r.get("anon_user_id") == f"anon-{marker}"]
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == TEST_USER_ID
    assert row["path"] == f"/processes/{marker}"
    # properties flattened to a JSON string column.
    assert json.loads(row["properties"])["kind"] == "click"


# --------------------------------------------------------------------------
# XES filtering / well-formedness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xes_filtered_well_formed(admin_client: AsyncClient, seeded_events: str) -> None:
    from xml.dom.minidom import parseString

    marker = seeded_events
    resp = await admin_client.get(
        f"/api/v1/admin/export/event-log.xes?case=user&user_id={TEST_USER_ID}"
    )
    assert resp.status_code == 200
    body = resp.text
    # Parses as XML (well-formed) and only the filtered user's events appear.
    doc = parseString(body)
    assert doc.documentElement.tagName == "log"
    assert f"/processes/{marker}" in body
    assert f"/models/{marker}" in body
    assert f"/settings/{marker}" not in body  # the other user is filtered out


# --------------------------------------------------------------------------
# Preview + facets
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_counts(admin_client: AsyncClient, seeded_events: str) -> None:
    marker = seeded_events
    # Constrain to this fixture's clicks by name so other rows can't pollute.
    resp = await admin_client.get(f"/api/v1/admin/export/preview?event_name=click.{marker}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_events"] == 2
    assert body["matched_sessions"] == 2
    assert body["distinct_users"] == 2
    assert body["date_min"] is not None
    assert body["date_max"] is not None
    assert any(t["label"] == "click" for t in body["event_types"])


@pytest.mark.asyncio
async def test_preview_user_filter(admin_client: AsyncClient, seeded_events: str) -> None:
    resp = await admin_client.get(
        f"/api/v1/admin/export/preview?user_id={TEST_USER_ID}&path_prefix=/processes"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_events"] == 1
    assert body["distinct_users"] == 1


@pytest.mark.asyncio
async def test_facets_shape(admin_client: AsyncClient, seeded_events: str) -> None:
    resp = await admin_client.get("/api/v1/admin/export/facets")
    assert resp.status_code == 200
    body = resp.json()
    # Both seeded users have events, so both surface in the user facet.
    user_ids = {u["id"] for u in body["users"]}
    assert TEST_USER_ID in user_ids
    assert OTHER_USER_ID in user_ids
    assert "click" in body["event_types"]
    assert "page" in body["event_types"]
    # event_names / paths are [{label,count}] frequency lists.
    assert all({"label", "count"} <= set(n) for n in body["event_names"])
    assert all({"label", "count"} <= set(p) for p in body["paths"])
