"""CRUD + export/import for /api/v1/dashboards.

A dashboard is a saved grid of cards (each a module's `(widget_id)`) bound to a
single event log; every card renders against that log. The board state (placed
cards + react-grid-layout geometry) is stored as one JSON blob, so a save is a
single atomic write and export is a passthrough.

Every row is keyed by the Keycloak `sub` (``user.id``); a dashboard and its
bound log are both validated against the current user on every access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.models import Dashboard, DashboardShare, Team, User
from mate.api.db.session import SessionDep
from mate.api.schemas.dashboards import (
    CanvasSettings,
    DashboardCreate,
    DashboardDetail,
    DashboardExport,
    DashboardImport,
    DashboardItem,
    DashboardSummary,
    DashboardUpdate,
)
from mate.api.schemas.sharing import DashboardShareOut, ShareCreate
from mate.api.sharing import (
    can_share_with_team,
    can_share_with_user,
    get_accessible_dashboard,
    user_label,
)
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _items_of(row: Dashboard) -> list[DashboardItem]:
    raw = (row.layout_json or {}).get("items", [])
    items: list[DashboardItem] = []
    for entry in raw:
        try:
            items.append(DashboardItem.model_validate(entry))
        except Exception:
            # Skip a malformed/legacy item rather than 500 the whole board.
            log.warning("dashboard.item_skipped", dashboard_id=row.id, entry=entry)
    return items


def _settings_of(row: Dashboard) -> CanvasSettings:
    raw = (row.layout_json or {}).get("settings")
    if not raw:
        return CanvasSettings()
    try:
        return CanvasSettings.model_validate(raw)
    except Exception:
        # Fall back to defaults rather than 500 on a legacy/garbled blob.
        log.warning("dashboard.settings_invalid", dashboard_id=row.id, raw=raw)
        return CanvasSettings()


def _layout_blob(items: list[DashboardItem], settings: CanvasSettings) -> dict[str, Any]:
    return {"items": [it.model_dump() for it in items], "settings": settings.model_dump()}


def _detail(row: Dashboard, *, is_owner: bool = True) -> DashboardDetail:
    return DashboardDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        event_log_id=row.event_log_id,
        log_model=row.log_model,  # type: ignore[arg-type]
        items=_items_of(row),
        settings=_settings_of(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_owner=is_owner,
    )


async def _get_owned(session: SessionDep, dashboard_id: str, user_id: str) -> Dashboard:
    row = await session.get(Dashboard, dashboard_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    return row


async def _validate_log(
    session: SessionDep, log_id: str | None, user_id: str, log_model: str | None = None
) -> None:
    """A bound log must belong to the user and (when ``log_model`` is given)
    match the board's data model. ``None`` (unbound) is always allowed."""
    if log_id is None:
        return
    row = await get_owned_event_log(session, log_id, user_id)
    if log_model is not None and row.log_model != log_model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Log is {row.log_model}; this dashboard is {log_model}.",
        )


@router.get("", response_model=list[DashboardSummary])
async def list_dashboards(session: SessionDep, user: CurrentUserDep) -> list[DashboardSummary]:
    stmt = (
        select(Dashboard)
        .where(Dashboard.user_id == user.id)
        .order_by(Dashboard.position.asc(), Dashboard.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        DashboardSummary(
            id=r.id,
            name=r.name,
            description=r.description,
            event_log_id=r.event_log_id,
            log_model=r.log_model,  # type: ignore[arg-type]
            card_count=len((r.layout_json or {}).get("items", [])),
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("", response_model=DashboardDetail, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    payload: DashboardCreate, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    await _validate_log(session, payload.event_log_id, user.id, payload.log_model)
    row = Dashboard(
        id=uuid7_str(),
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        event_log_id=payload.event_log_id,
        log_model=payload.log_model,
        layout_json=_layout_blob(payload.items, payload.settings),
        created_at=_utcnow(),
    )
    session.add(row)
    await session.commit()
    log.info("dashboard.created", dashboard_id=row.id)
    return _detail(row)


@router.get("/{dashboard_id}", response_model=DashboardDetail)
async def get_dashboard(
    dashboard_id: str, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    # Owner or share recipient may view; recipients get a read-only board.
    row = await get_accessible_dashboard(session, dashboard_id, user.id)
    return _detail(row, is_owner=row.user_id == user.id)


@router.patch("/{dashboard_id}", response_model=DashboardDetail)
async def update_dashboard(
    dashboard_id: str,
    payload: DashboardUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> DashboardDetail:
    row = await _get_owned(session, dashboard_id, user.id)
    fields = payload.model_fields_set

    if "name" in fields and payload.name is not None:
        row.name = payload.name
    if "description" in fields:
        row.description = payload.description
    if "event_log_id" in fields:
        await _validate_log(session, payload.event_log_id, user.id, row.log_model)
        row.event_log_id = payload.event_log_id
    # Items and settings share one blob, so rewrite it whenever either is
    # touched, keeping the untouched sibling as-is.
    if ("items" in fields and payload.items is not None) or (
        "settings" in fields and payload.settings is not None
    ):
        items = payload.items if payload.items is not None else _items_of(row)
        settings = payload.settings if payload.settings is not None else _settings_of(row)
        row.layout_json = _layout_blob(items, settings)

    await session.commit()
    return _detail(row)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(dashboard_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    row = await _get_owned(session, dashboard_id, user.id)
    await session.delete(row)
    await session.commit()
    log.info("dashboard.deleted", dashboard_id=dashboard_id)


@router.get("/{dashboard_id}/export", response_model=DashboardExport)
async def export_dashboard(
    dashboard_id: str, session: SessionDep, user: CurrentUserDep
) -> DashboardExport:
    """Portable, id-free snapshot for download. Re-importable via POST /import.

    Intentionally drops the bound `event_log_id` - logs are per-user and the
    importer rebinds on the target machine.
    """
    row = await _get_owned(session, dashboard_id, user.id)
    return DashboardExport(
        name=row.name,
        description=row.description,
        log_model=row.log_model,  # type: ignore[arg-type]
        items=_items_of(row),
        settings=_settings_of(row),
    )


@router.post("/import", response_model=DashboardDetail, status_code=status.HTTP_201_CREATED)
async def import_dashboard(
    payload: DashboardImport, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    await _validate_log(session, payload.event_log_id, user.id, payload.log_model)
    row = Dashboard(
        id=uuid7_str(),
        user_id=user.id,
        name=(payload.name or "Imported dashboard").strip() or "Imported dashboard",
        description=payload.description,
        event_log_id=payload.event_log_id,
        log_model=payload.log_model,
        layout_json=_layout_blob(payload.items, payload.settings),
        created_at=_utcnow(),
    )
    session.add(row)
    await session.commit()
    log.info("dashboard.imported", dashboard_id=row.id, cards=len(payload.items))
    return _detail(row)


# --- Sharing (owner-managed) -------------------------------------------------


async def _share_out(session: SessionDep, share: DashboardShare) -> DashboardShareOut:
    if share.target_team_id is not None:
        team = await session.get(Team, share.target_team_id)
        label = team.name if team is not None else "Deleted team"
        return DashboardShareOut(
            id=share.id,
            dashboard_id=share.dashboard_id,
            kind="team",
            target_id=share.target_team_id,
            label=label,
            created_at=share.created_at,
        )
    target_user = await session.get(User, share.target_user_id)
    return DashboardShareOut(
        id=share.id,
        dashboard_id=share.dashboard_id,
        kind="user",
        target_id=share.target_user_id or "",
        label=user_label(target_user),
        created_at=share.created_at,
    )


@router.get("/{dashboard_id}/shares", response_model=list[DashboardShareOut])
async def list_shares(
    dashboard_id: str, session: SessionDep, user: CurrentUserDep
) -> list[DashboardShareOut]:
    await _get_owned(session, dashboard_id, user.id)
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
    return [await _share_out(session, r) for r in rows]


@router.post(
    "/{dashboard_id}/shares",
    response_model=DashboardShareOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_share(
    dashboard_id: str,
    payload: ShareCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> DashboardShareOut:
    await _get_owned(session, dashboard_id, user.id)

    # Validate the target exists and is reachable: you can only share with a
    # teammate or a team you belong to (the same scope /sharing/targets offers).
    if payload.target_user_id is not None:
        if payload.target_user_id == user.id:
            raise HTTPException(status_code=400, detail="You already own this dashboard.")
        if await session.get(User, payload.target_user_id) is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if not await can_share_with_user(session, payload.target_user_id, user.id):
            raise HTTPException(
                status_code=403, detail="You can only share with members of your teams."
            )
        dup = (
            select(DashboardShare.id)
            .where(
                DashboardShare.dashboard_id == dashboard_id,
                DashboardShare.target_user_id == payload.target_user_id,
            )
            .limit(1)
        )
    else:
        if await session.get(Team, payload.target_team_id) is None:
            raise HTTPException(status_code=404, detail="Team not found.")
        if not await can_share_with_team(session, payload.target_team_id or "", user.id):
            raise HTTPException(
                status_code=403, detail="You can only share with teams you belong to."
            )
        dup = (
            select(DashboardShare.id)
            .where(
                DashboardShare.dashboard_id == dashboard_id,
                DashboardShare.target_team_id == payload.target_team_id,
            )
            .limit(1)
        )

    if (await session.execute(dup)).first() is not None:
        raise HTTPException(status_code=409, detail="Already shared with this target.")

    share = DashboardShare(
        id=uuid7_str(),
        dashboard_id=dashboard_id,
        target_user_id=payload.target_user_id,
        target_team_id=payload.target_team_id,
        created_by=user.id,
        created_at=_utcnow(),
    )
    session.add(share)
    await session.commit()
    log.info("dashboard.shared", dashboard_id=dashboard_id, share_id=share.id)
    return await _share_out(session, share)


@router.delete("/{dashboard_id}/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_share(
    dashboard_id: str, share_id: str, session: SessionDep, user: CurrentUserDep
) -> None:
    await _get_owned(session, dashboard_id, user.id)
    share = await session.get(DashboardShare, share_id)
    if share is None or share.dashboard_id != dashboard_id:
        raise HTTPException(status_code=404, detail="Share not found.")
    await session.delete(share)
    await session.commit()
    log.info("dashboard.unshared", dashboard_id=dashboard_id, share_id=share_id)
