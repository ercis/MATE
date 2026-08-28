"""CRUD + export/import for /api/v1/dashboards.

A dashboard is a saved grid of cards (each a module's `(widget_id)`) bound to a
single event log; every card renders against that log. The board state (placed
cards + react-grid-layout geometry) is stored as one JSON blob, so a save is a
single atomic write and export is a passthrough.

Every row is keyed by the Keycloak `sub` (``user.id``); a dashboard and its
bound log are both validated against the current user on every access.

The ownership/validation/share helpers here are plain ``(session, ...)``
functions rather than FastAPI dependencies so the MCP dashboards toolset can
reuse them verbatim - keep them framework-free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.dashboard_templates import get_template, list_templates
from mate.api.db.models import Dashboard, DashboardShare, Team, User
from mate.api.db.session import SessionDep
from mate.api.schemas.dashboards import (
    GRID_COLS,
    LEGACY_GRANULARITY_COLS,
    MAX_ROW,
    CanvasSettings,
    DashboardCreate,
    DashboardDetail,
    DashboardExport,
    DashboardImport,
    DashboardItem,
    DashboardSummary,
    DashboardTemplate,
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


def _clamp_geometry(entry: Any) -> dict[str, Any] | None:
    """Pull a card's x/y/w/h back inside the grid bounds.

    Only geometry is touched: a card that fails validation for any other
    reason (a widget card with no ``widget_id``, say) still can't be rescued
    and is dropped by the caller.
    """
    if not isinstance(entry, dict):
        return None
    out: dict[str, Any] = dict(entry)  # pyright: ignore[reportUnknownArgumentType]

    def num(key: str, default: int) -> int:
        v = out.get(key, default)
        # bool is an int subclass - exclude it, it's never meaningful geometry.
        return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else default

    w = max(1, min(GRID_COLS, num("w", 6)))
    h = max(1, min(48, num("h", 8)))
    # Clamp x against the *clamped* width so a narrowed card can't hang off the
    # right edge (x + w must stay within the grid).
    x = max(0, min(GRID_COLS - w, num("x", 0)))
    y = max(0, min(MAX_ROW, num("y", 0)))
    out.update(x=x, y=y, w=w, h=h)
    return out


def dashboard_items(row: Dashboard) -> list[DashboardItem]:
    raw = (row.layout_json or {}).get("items", [])
    items: list[DashboardItem] = []
    for entry in raw:
        try:
            items.append(DashboardItem.model_validate(entry))
            continue
        except Exception:
            pass
        # Clamp-then-keep. Tightening the grid bounds must never silently
        # delete a user's card: an out-of-range placement is a geometry
        # problem, so fix the geometry and keep the card rather than dropping
        # it (which is what this used to do, and what would have wiped every
        # card past column 11 the moment the 12-col bounds landed).
        clamped = _clamp_geometry(entry)
        if clamped is not None:
            try:
                items.append(DashboardItem.model_validate(clamped))
                log.info("dashboard.item_clamped", dashboard_id=row.id, entry=entry)
                continue
            except Exception:
                pass
        # Genuinely malformed - skip it rather than 500 the whole board.
        log.warning("dashboard.item_skipped", dashboard_id=row.id, entry=entry)
    return items


def coerce_grid(items: list[DashboardItem], settings: CanvasSettings) -> list[DashboardItem]:
    """Rescale pre-v2 geometry onto the fixed 12-column grid.

    A legacy board is detected by ``settings.legacy_granularity`` - the old
    ``granularity`` key, which every pre-v2 blob carries and which
    ``CanvasSettings`` never re-emits. So a coerced board is written back
    without the marker and this is a no-op forever after: idempotent by
    construction rather than by a version check the caller has to remember.

    The marker always travels with the items it describes: only a pre-v2
    writer emits ``granularity``, and such a writer necessarily also emits
    pre-v2 geometry. So "marker present" is a sound signal on any payload,
    stored blob or export file alike.
    """
    gran = settings.legacy_granularity
    if gran is None or not items:
        return items
    from_cols = LEGACY_GRANULARITY_COLS.get(gran, 24)
    if from_cols == GRID_COLS:
        return items  # the old "low" level was already 12 columns
    factor = GRID_COLS / from_cols
    scaled: list[DashboardItem] = []
    for it in items:
        w = max(1, min(GRID_COLS, round(it.w * factor)))
        x = max(0, min(GRID_COLS - w, round(it.x * factor)))
        # y/h are row-based and the row height is unchanged (the new grid keeps
        # the old "medium" rowHeight), so vertical geometry survives as-is.
        scaled.append(it.model_copy(update={"x": x, "w": w}))
    return _push_down(scaled)


def _push_down(items: list[DashboardItem]) -> list[DashboardItem]:
    """Resolve overlaps by pushing cards straight down, never sideways.

    Shrinking the column count collapses distinct columns onto the same cell
    (60 -> 12 divides x by five), so a faithful rescale routinely produces
    overlapping cards. This is the server-side twin of the canvas's
    ``reflowFree``: walk in reading order and drop each card below anything it
    lands on, so relative order is preserved and nothing is silently stacked.
    """
    placed: list[DashboardItem] = []
    for it in sorted(items, key=lambda c: (c.y, c.x)):
        y = it.y
        moved = True
        guard = 0
        while moved and guard < 1000:
            guard += 1
            moved = False
            for other in placed:
                if (
                    it.x < other.x + other.w
                    and it.x + it.w > other.x
                    and y < other.y + other.h
                    and y + it.h > other.y
                ):
                    y = other.y + other.h
                    moved = True
        placed.append(it.model_copy(update={"y": min(y, MAX_ROW)}))
    return placed


def dashboard_settings(row: Dashboard) -> CanvasSettings:
    raw = (row.layout_json or {}).get("settings")
    if not raw:
        return CanvasSettings()
    try:
        return CanvasSettings.model_validate(raw)
    except Exception:
        # Fall back to defaults rather than 500 on a legacy/garbled blob.
        log.warning("dashboard.settings_invalid", dashboard_id=row.id, raw=raw)
        return CanvasSettings()


def layout_blob(items: list[DashboardItem], settings: CanvasSettings) -> dict[str, Any]:
    """Serialize a board's items + settings into the stored layout blob.

    This is where the 12-column invariant is enforced, for two reasons.

    First, it is the single choke point every write goes through (the CRUD
    routes, import, and the MCP toolset, which builds its rows directly rather
    than calling the routes). ``model_dump`` drops the legacy ``granularity``
    marker, so coercing anywhere else would let a caller write unrescaled
    items with the marker already stripped - permanently losing the only key
    that says how to interpret them. Coerce-and-drop must be atomic.

    Second, ``DashboardItem`` deliberately accepts wider-than-grid geometry so
    legacy export files still import (see the note on its bounds). That makes
    normalisation at write the only place the invariant can actually hold:
    ``coerce_grid`` rescales a marked legacy board, and ``_fit_to_grid``
    catches anything else that is out of range.
    """
    return {
        "items": [it.model_dump() for it in normalise_grid(items, settings)],
        "settings": settings.model_dump(),
    }


def normalise_grid(items: list[DashboardItem], settings: CanvasSettings) -> list[DashboardItem]:
    """Put a board's cards on the 12-column grid: rescale, then clamp.

    Applied on both sides - every write (via ``layout_blob``) and every read
    (``dashboard_detail``, ``export_dashboard``) - because the schema accepts
    wider-than-grid geometry and rows written before the migration may still
    be out of range. Idempotent, so running it on both sides is harmless.
    """
    return _fit_to_grid(coerce_grid(items, settings))


def _fit_to_grid(items: list[DashboardItem]) -> list[DashboardItem]:
    """Clamp every card into the 12-column grid. No-op for in-range geometry."""
    out: list[DashboardItem] = []
    for it in items:
        w = max(1, min(GRID_COLS, it.w))
        x = max(0, min(GRID_COLS - w, it.x))
        out.append(it if (x, w) == (it.x, it.w) else it.model_copy(update={"x": x, "w": w}))
    return out


def dashboard_detail(row: Dashboard, *, is_owner: bool = True) -> DashboardDetail:
    settings = dashboard_settings(row)
    return DashboardDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        event_log_id=row.event_log_id,
        log_model=row.log_model,  # type: ignore[arg-type]
        # Read-path normalisation is a safety net, not the migration: Alembic
        # 0014 rewrites stored rows. It covers a row that predates the
        # migration (restored backup, a board written by an older API).
        # Nothing is written back here - the client's next autosave persists
        # the normalised geometry, and since the response drops the legacy
        # marker the board converges to v2 on its own.
        items=normalise_grid(dashboard_items(row), settings),
        settings=settings,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_owner=is_owner,
    )


async def get_owned_dashboard(session: AsyncSession, dashboard_id: str, user_id: str) -> Dashboard:
    """Owner-only gate for mutations: 404 for missing AND foreign boards."""
    row = await session.get(Dashboard, dashboard_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    return row


async def validate_dashboard_log(
    session: AsyncSession, log_id: str | None, user_id: str, log_model: str | None = None
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


async def apply_dashboard_update(
    session: AsyncSession, row: Dashboard, payload: DashboardUpdate, user_id: str
) -> None:
    """Apply a partial update to an owned board (no commit).

    Only fields present in ``payload.model_fields_set`` are touched, so a field
    can be explicitly cleared (``None``) without clobbering the rest.
    """
    fields = payload.model_fields_set

    if "name" in fields and payload.name is not None:
        row.name = payload.name
    if "description" in fields:
        row.description = payload.description
    if "event_log_id" in fields:
        await validate_dashboard_log(session, payload.event_log_id, user_id, row.log_model)
        row.event_log_id = payload.event_log_id
    # Items and settings share one blob, so rewrite it whenever either is
    # touched, keeping the untouched sibling as-is.
    if ("items" in fields and payload.items is not None) or (
        "settings" in fields and payload.settings is not None
    ):
        items = payload.items if payload.items is not None else dashboard_items(row)
        settings = payload.settings if payload.settings is not None else dashboard_settings(row)
        row.layout_json = layout_blob(items, settings)


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
    # A ``template_id`` seeds the board from a curated starter template - its
    # items, settings and data model win over the (empty) request body. Absent
    # it, this is the classic blank-board create.
    template = get_template(payload.template_id) if payload.template_id else None
    if payload.template_id and template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    log_model = template.log_model if template else payload.log_model
    items = template.items if template else payload.items
    settings = template.settings if template else payload.settings
    await validate_dashboard_log(session, payload.event_log_id, user.id, log_model)
    row = Dashboard(
        id=uuid7_str(),
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        event_log_id=payload.event_log_id,
        log_model=log_model,
        layout_json=layout_blob(items, settings),
        created_at=_utcnow(),
    )
    session.add(row)
    await session.commit()
    log.info("dashboard.created", dashboard_id=row.id, template=payload.template_id)
    return dashboard_detail(row)


@router.get("/templates", response_model=list[DashboardTemplate])
async def list_dashboard_templates(user: CurrentUserDep) -> list[DashboardTemplate]:
    """Curated starter boards for the "start from template" picker.

    Static + global (not per-user); the actual cards are seeded by
    ``POST /dashboards`` with ``template_id``. Declared before ``/{dashboard_id}``
    so the literal path wins over the id parameter.
    """
    return [
        DashboardTemplate(
            id=t.id,
            name=t.name,
            description=t.description,
            log_model=t.log_model,
            card_count=len(t.items),
        )
        for t in list_templates()
    ]


@router.get("/{dashboard_id}", response_model=DashboardDetail)
async def get_dashboard(
    dashboard_id: str, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    # Owner or share recipient may view; recipients get a read-only board.
    row = await get_accessible_dashboard(session, dashboard_id, user.id)
    return dashboard_detail(row, is_owner=row.user_id == user.id)


@router.patch("/{dashboard_id}", response_model=DashboardDetail)
async def update_dashboard(
    dashboard_id: str,
    payload: DashboardUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> DashboardDetail:
    row = await get_owned_dashboard(session, dashboard_id, user.id)
    await apply_dashboard_update(session, row, payload, user.id)
    await session.commit()
    return dashboard_detail(row)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(dashboard_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    row = await get_owned_dashboard(session, dashboard_id, user.id)
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
    row = await get_owned_dashboard(session, dashboard_id, user.id)
    settings = dashboard_settings(row)
    return DashboardExport(
        name=row.name,
        description=row.description,
        log_model=row.log_model,  # type: ignore[arg-type]
        # Always emit v2 geometry, so a file exported from an unmigrated row
        # can't reintroduce legacy geometry when it is imported later.
        items=normalise_grid(dashboard_items(row), settings),
        settings=settings,
    )


@router.post("/import", response_model=DashboardDetail, status_code=status.HTTP_201_CREATED)
async def import_dashboard(
    payload: DashboardImport, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    await validate_dashboard_log(session, payload.event_log_id, user.id, payload.log_model)
    # Exported board files live on users' disks indefinitely, so an import is
    # the one path where pre-v2 geometry keeps arriving long after the DB
    # migration ran. `layout_blob` coerces it.
    row = Dashboard(
        id=uuid7_str(),
        user_id=user.id,
        name=(payload.name or "Imported dashboard").strip() or "Imported dashboard",
        description=payload.description,
        event_log_id=payload.event_log_id,
        log_model=payload.log_model,
        layout_json=layout_blob(payload.items, payload.settings),
        created_at=_utcnow(),
    )
    session.add(row)
    await session.commit()
    log.info("dashboard.imported", dashboard_id=row.id, cards=len(payload.items))
    return dashboard_detail(row)


# --- Sharing (owner-managed) -------------------------------------------------


async def share_out(session: AsyncSession, share: DashboardShare) -> DashboardShareOut:
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


async def create_share_for_owner(
    session: AsyncSession, dashboard_id: str, payload: ShareCreate, user_id: str
) -> DashboardShare:
    """Validate + stage a new share for a board *user_id* owns (no commit).

    Owner-only (404 on foreign), target must exist and be reachable - you can
    only share with a teammate or a team you belong to (the same scope
    ``/sharing/targets`` offers) - and a duplicate target is a 409.
    """
    await get_owned_dashboard(session, dashboard_id, user_id)

    if payload.target_user_id is not None:
        if payload.target_user_id == user_id:
            raise HTTPException(status_code=400, detail="You already own this dashboard.")
        if await session.get(User, payload.target_user_id) is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if not await can_share_with_user(session, payload.target_user_id, user_id):
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
        if not await can_share_with_team(session, payload.target_team_id or "", user_id):
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
        created_by=user_id,
        created_at=_utcnow(),
    )
    session.add(share)
    return share


async def resolve_share_for_owner(
    session: AsyncSession, dashboard_id: str, share_id: str, user_id: str
) -> DashboardShare:
    """Look up a share on a board *user_id* owns; 404 on foreign board/share."""
    await get_owned_dashboard(session, dashboard_id, user_id)
    share = await session.get(DashboardShare, share_id)
    if share is None or share.dashboard_id != dashboard_id:
        raise HTTPException(status_code=404, detail="Share not found.")
    return share


@router.get("/{dashboard_id}/shares", response_model=list[DashboardShareOut])
async def list_shares(
    dashboard_id: str, session: SessionDep, user: CurrentUserDep
) -> list[DashboardShareOut]:
    await get_owned_dashboard(session, dashboard_id, user.id)
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
    return [await share_out(session, r) for r in rows]


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
    share = await create_share_for_owner(session, dashboard_id, payload, user.id)
    await session.commit()
    log.info("dashboard.shared", dashboard_id=dashboard_id, share_id=share.id)
    return await share_out(session, share)


@router.delete("/{dashboard_id}/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_share(
    dashboard_id: str, share_id: str, session: SessionDep, user: CurrentUserDep
) -> None:
    share = await resolve_share_for_owner(session, dashboard_id, share_id, user.id)
    await session.delete(share)
    await session.commit()
    log.info("dashboard.unshared", dashboard_id=dashboard_id, share_id=share_id)
