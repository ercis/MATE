"""Dashboards toolset: cross-module card boards - CRUD, sharing, portability.

Mirrors :mod:`mate.api.routes.dashboards` (+ the ``/sharing`` and
``/modules/cards`` views) one-to-one: ownership gates, bound-log validation,
share-target validation and the export/import snapshot are imported from the
route layer so the two surfaces can never drift. Dashboard sharing is the
platform's only sanctioned cross-account path and it is read-only - recipients
can ``get_dashboard`` a shared board, while every mutating tool is owner-only
(404 on foreign, exactly like the routes).
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Dashboard, DashboardShare
from mate.api.mcp.core import MCPContext, authz, cap, confirm_preview, guarded
from mate.api.mcp.errors import CODE_INVALID, from_http_exception, tool_error
from mate.api.mcp.pagination import clamp_limit, decode_cursor, page_envelope
from mate.api.mcp.registry import mcp_tool
from mate.api.mcp.scopes import SCOPE_DASHBOARDS_READ, SCOPE_DASHBOARDS_WRITE
from mate.api.routes.dashboards import (
    apply_dashboard_update,
    create_share_for_owner,
    dashboard_detail,
    dashboard_items,
    dashboard_settings,
    get_owned_dashboard,
    layout_blob,
    resolve_share_for_owner,
    share_out,
    validate_dashboard_log,
)
from mate.api.routes.modules import list_cards as list_module_cards
from mate.api.routes.sharing import share_targets as sharing_targets
from mate.api.routes.sharing import shared_with_me as sharing_shared_with_me
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.dashboards import (
    DashboardCreate,
    DashboardExport,
    DashboardImport,
    DashboardUpdate,
)
from mate.api.schemas.event_logs import LogModel
from mate.api.schemas.sharing import ShareCreate
from mate.api.sharing import get_accessible_dashboard
from mate.api.uuid7 import uuid7_str


def _validated[M: BaseModel](model: type[M], data: Any, what: str) -> M:
    """Route-schema validation, translated to a tool error."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise tool_error(CODE_INVALID, f"Invalid {what}: {exc}") from exc


async def _ensure_owned(session: AsyncSession, dashboard_id: str, user_id: str) -> Dashboard:
    """Owner-only gate, translated to a tool error (404 for missing AND foreign)."""
    try:
        return await get_owned_dashboard(session, dashboard_id, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


async def _ensure_accessible(session: AsyncSession, dashboard_id: str, user_id: str) -> Dashboard:
    """Owner-or-recipient view gate (the routes' shared-dashboard access path)."""
    try:
        return await get_accessible_dashboard(session, dashboard_id, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


async def _ensure_valid_log(
    session: AsyncSession, log_id: str | None, user_id: str, log_model: str | None
) -> None:
    """Bound-log gate: owned (404 on foreign) + matching data model (400)."""
    try:
        await validate_dashboard_log(session, log_id, user_id, log_model)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


def _summary_dict(r: Dashboard) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "event_log_id": r.event_log_id,
        "log_model": r.log_model,
        "card_count": len((r.layout_json or {}).get("items", [])),
        "updated_at": utc_isoformat(r.updated_at),
    }


def _detail_dict(row: Dashboard, *, is_owner: bool) -> dict[str, Any]:
    """The exact shape the REST detail route serves (items + settings blob)."""
    return dashboard_detail(row, is_owner=is_owner).model_dump(mode="json")


async def _share_count(session: AsyncSession, dashboard_id: str) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(DashboardShare)
            .where(DashboardShare.dashboard_id == dashboard_id)
        )
    ).scalar_one()


# ── read tools ───────────────────────────────────────────────────────────────


@mcp_tool(toolset="dashboards", idempotent=True)
async def list_dashboards(
    ctx: MCPContext, cursor: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    """List the dashboards you own, in the UI's board order.

    Each item carries id, name, description, event_log_id (the bound process,
    if any), log_model and card_count. Use an id with ``get_dashboard`` for
    the full board. ``cursor``/``limit`` paginate (pass back ``next_cursor``);
    boards shared *with* you are listed by ``list_shared_with_me`` instead.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(Dashboard)
                    .where(Dashboard.user_id == p.user.id)
                )
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(Dashboard)
                        .where(Dashboard.user_id == p.user.id)
                        .order_by(Dashboard.position.asc(), Dashboard.created_at.desc())
                        .offset(offset)
                        .limit(size)
                    )
                )
                .scalars()
                .all()
            )
            items = [_summary_dict(r) for r in rows]
        return page_envelope(items, offset=offset, limit=size, total=total)

    return await guarded(p, "list_dashboards", {}, _impl())


@mcp_tool(toolset="dashboards", idempotent=True)
async def get_dashboard(ctx: MCPContext, dashboard_id: str) -> dict[str, Any]:
    """Full detail of one dashboard: card placements (``items``) + canvas
    ``settings`` (filters, presets, chrome).

    Works for boards you own AND boards shared with you (directly or via a
    team); ``is_owner: false`` marks a shared, read-only board - mutating
    tools will 404 on it.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_accessible(session, dashboard_id, p.user.id)
            return cap(_detail_dict(row, is_owner=row.user_id == p.user.id))

    return await guarded(p, "get_dashboard", {"dashboard_id": dashboard_id}, _impl())


@mcp_tool(toolset="dashboards", idempotent=True)
async def list_shared_with_me(ctx: MCPContext) -> list[dict[str, Any]]:
    """Dashboards other users shared with you (directly or via one of your
    teams), each with an ``owner_label``. Read them with ``get_dashboard``."""
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            boards = await sharing_shared_with_me(session, p.user)
        return [b.model_dump(mode="json") for b in boards]

    return await guarded(p, "list_shared_with_me", {}, _impl())


@mcp_tool(toolset="dashboards", idempotent=True)
async def get_dashboard_card_catalog(ctx: MCPContext) -> dict[str, Any]:
    """The cards you can place on a dashboard (from the modules you own).

    Each entry gives the ``module_id`` + ``widget_id`` pair to reference in a
    board item, its title/description, default and minimum sizes, the per-card
    ``config_schema`` (options for the item's ``config``) and ``log_models``
    (which board data model the card supports). Compose ``items`` for
    create_dashboard / update_dashboard from these.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            cards = await list_module_cards(session, p.user)
        return cap({"cards": [c.model_dump(mode="json") for c in cards], "count": len(cards)})

    return await guarded(p, "get_dashboard_card_catalog", {}, _impl())


@mcp_tool(toolset="dashboards", idempotent=True)
async def list_dashboard_shares(ctx: MCPContext, dashboard_id: str) -> list[dict[str, Any]]:
    """Active shares on a dashboard you own (who can read it). Owner-only.

    Each share has an ``id`` (for revoke_dashboard_share), ``kind``
    (user|team), ``target_id`` and a display ``label``.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ensure_owned(session, dashboard_id, p.user.id)
            rows = (
                (
                    await session.execute(
                        select(DashboardShare)
                        .where(DashboardShare.dashboard_id == dashboard_id)
                        .order_by(DashboardShare.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            return [(await share_out(session, r)).model_dump(mode="json") for r in rows]

    return await guarded(p, "list_dashboard_shares", {"dashboard_id": dashboard_id}, _impl())


@mcp_tool(toolset="dashboards", idempotent=True)
async def get_share_targets(ctx: MCPContext) -> list[dict[str, Any]]:
    """Who you may share a dashboard with: your teams and their co-members.

    Empty when you belong to no team - sharing is team-scoped, so join a team
    (admin-managed) before sharing. Use an entry's ``kind`` + ``id`` as
    ``target_user_id`` / ``target_team_id`` in share_dashboard.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            targets = await sharing_targets(session, p.user)
        return [t.model_dump(mode="json") for t in targets]

    return await guarded(p, "get_share_targets", {}, _impl())


@mcp_tool(toolset="dashboards", idempotent=True)
async def export_dashboard(ctx: MCPContext, dashboard_id: str) -> dict[str, Any]:
    """Portable, id-free snapshot of a dashboard you own (owner-only, like the
    download endpoint).

    The snapshot intentionally drops the bound ``event_log_id`` - logs are
    per-user; re-create the board (optionally rebinding a log) by passing the
    snapshot to import_dashboard.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned(session, dashboard_id, p.user.id)
            snapshot = DashboardExport(
                name=row.name,
                description=row.description,
                log_model=cast(LogModel, row.log_model),
                items=dashboard_items(row),
                settings=dashboard_settings(row),
            )
        return cap(snapshot.model_dump(mode="json"))

    return await guarded(p, "export_dashboard", {"dashboard_id": dashboard_id}, _impl())


# ── write tools ──────────────────────────────────────────────────────────────


@mcp_tool(toolset="dashboards", write=True)
async def create_dashboard(
    ctx: MCPContext,
    name: str,
    log_model: str = "case_centric",
    event_log_id: str | None = None,
    description: str | None = None,
    items: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a dashboard: a saved grid of module cards bound to one event log.

    ``log_model`` (case_centric|object_centric) is fixed at creation; a bound
    ``event_log_id`` must be one of your logs with the same model (omit it for
    an unbound board). ``items`` is the list of card placements:
    ``{"i": "<unique placement id>", "module_id": "...", "widget_id": "...",
    "title"?: str, "x": 0-11, "y": 0-400, "w": 1-12, "h": 1-48, "config": {}}``
    - the board is a fixed 12-column grid. Discover valid module_id/widget_id
    pairs, sizes and each card's ``config`` schema via
    get_dashboard_card_catalog. ``settings`` holds the board-level canvas
    preferences and filters:
    ``{"chrome": {"border": bool}, "presets": [{"id", "name", "filters":
    [...]}], "active_preset_id"?, "column_filters"?: [...], "time_filters"?:
    [...]}``. Both default to empty.

    Legacy boards: passing the old ``settings.granularity``
    (free|fine|medium|low) makes the server read ``items`` as that many
    columns and rescale them onto the 12-column grid once. It is never
    returned, so a coerced board is never rescaled twice.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_WRITE, write=True)
    data: dict[str, Any] = {"name": name, "log_model": log_model}
    if description is not None:
        data["description"] = description
    if event_log_id is not None:
        data["event_log_id"] = event_log_id
    if items is not None:
        data["items"] = items
    if settings is not None:
        data["settings"] = settings
    payload = _validated(DashboardCreate, data, "dashboard")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ensure_valid_log(session, payload.event_log_id, p.user.id, payload.log_model)
            row = Dashboard(
                id=uuid7_str(),
                user_id=p.user.id,
                name=payload.name,
                description=payload.description,
                event_log_id=payload.event_log_id,
                log_model=payload.log_model,
                layout_json=layout_blob(payload.items, payload.settings),
            )
            session.add(row)
            await session.commit()
            return _detail_dict(row, is_owner=True)

    return await guarded(p, "create_dashboard", {"name": name}, _impl(), mutation=True)


@mcp_tool(toolset="dashboards", write=True)
async def update_dashboard(
    ctx: MCPContext,
    dashboard_id: str,
    name: str | None = None,
    description: str | None = None,
    event_log_id: str | None = None,
    items: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a dashboard you own; omitted fields are left unchanged.

    ``items``/``settings`` use the same shapes as create_dashboard and each
    REPLACES its blob wholesale (read the board first, modify, write back) -
    the untouched sibling is preserved. Rebinding ``event_log_id`` revalidates
    ownership + log_model. The board's log_model itself is fixed at creation.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_WRITE, write=True)
    provided = {
        key: value
        for key, value in {
            "name": name,
            "description": description,
            "event_log_id": event_log_id,
            "items": items,
            "settings": settings,
        }.items()
        if value is not None
    }
    payload = _validated(DashboardUpdate, provided, "dashboard update")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned(session, dashboard_id, p.user.id)
            try:
                await apply_dashboard_update(session, row, payload, p.user.id)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
            await session.commit()
            return _detail_dict(row, is_owner=True)

    return await guarded(
        p, "update_dashboard", {"dashboard_id": dashboard_id}, _impl(), mutation=True
    )


@mcp_tool(toolset="dashboards", write=True)
async def import_dashboard(ctx: MCPContext, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Re-create a dashboard from an export_dashboard snapshot (a new board id
    is minted; nothing is overwritten).

    ``snapshot`` is the exported dict (``name``/``description``/``log_model``/
    ``items``/``settings``); an optional ``event_log_id`` may be added to bind
    one of YOUR logs on import (ownership + log_model validated).
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_WRITE, write=True)
    payload = _validated(DashboardImport, snapshot, "dashboard snapshot")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ensure_valid_log(session, payload.event_log_id, p.user.id, payload.log_model)
            row = Dashboard(
                id=uuid7_str(),
                user_id=p.user.id,
                name=(payload.name or "Imported dashboard").strip() or "Imported dashboard",
                description=payload.description,
                event_log_id=payload.event_log_id,
                log_model=payload.log_model,
                layout_json=layout_blob(payload.items, payload.settings),
            )
            session.add(row)
            await session.commit()
            return _detail_dict(row, is_owner=True)

    return await guarded(p, "import_dashboard", {}, _impl(), mutation=True)


@mcp_tool(toolset="dashboards", write=True)
async def share_dashboard(
    ctx: MCPContext,
    dashboard_id: str,
    target_user_id: str | None = None,
    target_team_id: str | None = None,
) -> dict[str, Any]:
    """Grant READ access to a dashboard you own. Exactly one target: a teammate
    (``target_user_id``) or a whole team you belong to (``target_team_id``).

    Recipients can view the board (and its cards' data) but never edit it.
    Candidates come from get_share_targets; sharing outside your teams is
    refused and re-sharing to the same target is a [conflict].
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_WRITE, write=True)
    payload = _validated(
        ShareCreate,
        {"target_user_id": target_user_id, "target_team_id": target_team_id},
        "share target",
    )

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                share = await create_share_for_owner(session, dashboard_id, payload, p.user.id)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
            await session.commit()
            return (await share_out(session, share)).model_dump(mode="json")

    return await guarded(
        p, "share_dashboard", {"dashboard_id": dashboard_id}, _impl(), mutation=True
    )


@mcp_tool(toolset="dashboards", write=True)
async def revoke_dashboard_share(
    ctx: MCPContext, dashboard_id: str, share_id: str
) -> dict[str, Any]:
    """Remove one share from a dashboard you own (the target loses access).

    ``share_id`` comes from list_dashboard_shares / share_dashboard.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                share = await resolve_share_for_owner(session, dashboard_id, share_id, p.user.id)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
            await session.delete(share)
            await session.commit()
        return {"dashboard_id": dashboard_id, "share_id": share_id, "revoked": True}

    return await guarded(
        p,
        "revoke_dashboard_share",
        {"dashboard_id": dashboard_id, "share_id": share_id},
        _impl(),
        mutation=True,
    )


@mcp_tool(toolset="dashboards", write=True, destructive=True)
async def delete_dashboard(
    ctx: MCPContext, dashboard_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Delete a dashboard you own. Dry-runs without ``confirm``.

    The preview reports the board's name, card count and how many active
    shares would be revoked - deletion cascades every share, so recipients
    lose access immediately. The bound event log itself is never touched.
    """
    p = await authz(ctx, SCOPE_DASHBOARDS_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned(session, dashboard_id, p.user.id)
            share_count = await _share_count(session, dashboard_id)
            if not confirm:
                return confirm_preview(
                    "delete_dashboard",
                    {
                        "dashboard_id": row.id,
                        "name": row.name,
                        "card_count": len((row.layout_json or {}).get("items", [])),
                        "share_count": share_count,
                        "note": "Deleting revokes every share; the bound event log is kept.",
                    },
                )
            await session.delete(row)
            await session.commit()
        return {"deleted": True, "dashboard_id": dashboard_id, "shares_revoked": share_count}

    return await guarded(
        p,
        "delete_dashboard",
        {"dashboard_id": dashboard_id, "confirm": confirm},
        _impl(),
        mutation=True,
    )
