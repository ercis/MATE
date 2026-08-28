"""Admin user management - `/api/v1/admin/users/*`.

Gated by ``AdminUserDep`` (the ``admin`` realm role). Two endpoints beyond the
read-only list in ``admin_teams.py``:

* ``GET  /admin/users/{id}``  - drill-down: every resource the user owns.
* ``DELETE /admin/users/{id}`` - full purge (DB cascade + on-disk + S3 +
  Keycloak). See ``services/user_deletion.delete_user_and_all_data`` for the
  ordering and the partial-failure contract.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from mate.api.auth import AdminUserDep
from mate.api.auth.keycloak_admin import KeycloakAdmin, get_keycloak_admin
from mate.api.config import get_settings
from mate.api.db.models import (
    AnalyticsEvent,
    AnalyticsSession,
    ApiToken,
    Dashboard,
    DashboardShare,
    EventLog,
    Folder,
    Job,
    Team,
    TeamMember,
    User,
    WatchedFolder,
)
from mate.api.db.session import SessionDep
from mate.api.jobs.runtime import JobRuntime, get_job_runtime
from mate.api.modules import get_module_loader
from mate.api.modules.defaults import get_admin_default_ids
from mate.api.modules.installs import owner_count, user_module_ids
from mate.api.services.user_deletion import delete_user_and_all_data

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _runtime_dep() -> JobRuntime:
    return get_job_runtime()


_RuntimeDep = Annotated[JobRuntime, Depends(_runtime_dep)]


def _keycloak_dep() -> KeycloakAdmin:
    return get_keycloak_admin()


_KeycloakDep = Annotated[KeycloakAdmin, Depends(_keycloak_dep)]


def _dir_size_bytes(path: Path, *, max_entries: int = 100_000) -> int:
    """Bounded recursive byte total under ``path`` (mirrors admin_insights/system)."""
    total = 0
    visited = 0
    for entry in path.rglob("*"):
        visited += 1
        if visited > max_entries:
            break
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def _label(u: User) -> str:
    return u.name or u.preferred_username or u.email or u.id


# --- response schemas -------------------------------------------------------


class UserEventLogBrief(BaseModel):
    id: str
    name: str
    status: str
    log_model: str
    events_count: int | None
    cases_count: int | None
    created_at: datetime
    deleted_at: datetime | None


class UserWatchedFolderBrief(BaseModel):
    id: str
    name: str
    status: str
    mode: str


class UserDashboardBrief(BaseModel):
    id: str
    name: str
    event_log_id: str | None


class UserTeamBrief(BaseModel):
    team_id: str
    name: str
    role: str


class UserApiTokenBrief(BaseModel):
    id: str
    name: str
    token_prefix: str
    revoked: bool
    last_used_at: datetime | None


class UserModuleBrief(BaseModel):
    module_id: str
    # True when this user is the sole owner and the module isn't a protected
    # default - i.e. a delete would tear the shared artifact down.
    last_owner: bool


class UserJobCounts(BaseModel):
    by_status: dict[str, int]
    active: int


class AdminUserDetail(BaseModel):
    id: str
    email: str | None
    preferred_username: str | None
    name: str | None
    label: str
    created_at: datetime
    last_seen_at: datetime
    event_logs: list[UserEventLogBrief]
    folders_count: int
    watched_folders: list[UserWatchedFolderBrief]
    dashboards: list[UserDashboardBrief]
    shares_created: int
    shares_received: int
    jobs: UserJobCounts
    modules: list[UserModuleBrief]
    teams: list[UserTeamBrief]
    api_tokens: list[UserApiTokenBrief]
    analytics_sessions: int
    analytics_events: int
    # Only populated when the request passes ``?include_disk=1`` (the heavy walk).
    storage_bytes: int | None = None


class DeleteUserResponse(BaseModel):
    deleted: bool
    jobs_cancelled: int
    modules_torn_down: int
    keycloak_deleted: bool
    keycloak_skipped_reason: str | None
    warnings: list[str]


# --- endpoints --------------------------------------------------------------


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: str,
    session: SessionDep,
    _admin: AdminUserDep,
    include_disk: int = 0,
) -> AdminUserDetail:
    """Every resource *user_id* owns. ``?include_disk=1`` adds the on-disk byte total."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    # Event logs - include soft-deleted so the admin sees the full footprint.
    logs = (
        (
            await session.execute(
                select(EventLog)
                .where(EventLog.user_id == user_id)
                .order_by(EventLog.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    folders_count = (
        await session.execute(
            select(func.count())
            .select_from(Folder)
            .where(Folder.user_id == user_id, Folder.deleted_at.is_(None))
        )
    ).scalar_one()
    watched = (
        (
            await session.execute(
                select(WatchedFolder).where(
                    WatchedFolder.user_id == user_id, WatchedFolder.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    dashboards = (
        (
            await session.execute(
                select(Dashboard)
                .where(Dashboard.user_id == user_id)
                .order_by(Dashboard.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    shares_created = (
        await session.execute(
            select(func.count())
            .select_from(DashboardShare)
            .where(DashboardShare.created_by == user_id)
        )
    ).scalar_one()
    shares_received = (
        await session.execute(
            select(func.count())
            .select_from(DashboardShare)
            .where(DashboardShare.target_user_id == user_id)
        )
    ).scalar_one()

    job_rows = (
        await session.execute(
            select(Job.status, func.count()).where(Job.user_id == user_id).group_by(Job.status)
        )
    ).all()
    by_status = {str(s): int(c) for s, c in job_rows}
    active = by_status.get("queued", 0) + by_status.get("running", 0)

    # Modules + last-owner flag. Loader access is best-effort: if it isn't ready
    # we still list the ids, just without the teardown hint.
    owned_modules = sorted(await user_module_ids(session, user_id))
    modules: list[UserModuleBrief] = []
    try:
        loader = get_module_loader()
        protected = loader.default_module_ids | await get_admin_default_ids(session)
        for mid in owned_modules:
            oc = await owner_count(session, mid)
            modules.append(
                UserModuleBrief(module_id=mid, last_owner=(mid not in protected and oc == 1))
            )
    except Exception:
        modules = [UserModuleBrief(module_id=mid, last_owner=False) for mid in owned_modules]

    team_rows = (
        await session.execute(
            select(TeamMember.team_id, TeamMember.role, Team.name)
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == user_id, Team.deleted_at.is_(None))
        )
    ).all()
    teams = [UserTeamBrief(team_id=tid, name=name, role=role) for tid, role, name in team_rows]

    tokens = (
        (
            await session.execute(
                select(ApiToken)
                .where(ApiToken.user_id == user_id)
                .order_by(ApiToken.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    analytics_sessions = (
        await session.execute(
            select(func.count())
            .select_from(AnalyticsSession)
            .where(AnalyticsSession.user_id == user_id)
        )
    ).scalar_one()
    analytics_events = (
        await session.execute(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.user_id == user_id)
        )
    ).scalar_one()

    storage_bytes: int | None = None
    if include_disk:
        base = get_settings().users_dir / user_id
        storage_bytes = await asyncio.to_thread(
            lambda: _dir_size_bytes(base) if base.exists() else 0
        )

    return AdminUserDetail(
        id=user.id,
        email=user.email,
        preferred_username=user.preferred_username,
        name=user.name,
        label=_label(user),
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        event_logs=[
            UserEventLogBrief(
                id=lg.id,
                name=lg.name,
                status=lg.status,
                log_model=lg.log_model,
                events_count=lg.events_count,
                cases_count=lg.cases_count,
                created_at=lg.created_at,
                deleted_at=lg.deleted_at,
            )
            for lg in logs
        ],
        folders_count=int(folders_count),
        watched_folders=[
            UserWatchedFolderBrief(id=w.id, name=w.name, status=w.status, mode=w.mode)
            for w in watched
        ],
        dashboards=[
            UserDashboardBrief(id=d.id, name=d.name, event_log_id=d.event_log_id)
            for d in dashboards
        ],
        shares_created=int(shares_created),
        shares_received=int(shares_received),
        jobs=UserJobCounts(by_status=by_status, active=active),
        modules=modules,
        teams=teams,
        api_tokens=[
            UserApiTokenBrief(
                id=t.id,
                name=t.name,
                token_prefix=t.token_prefix,
                revoked=t.revoked,
                last_used_at=t.last_used_at,
            )
            for t in tokens
        ],
        analytics_sessions=int(analytics_sessions),
        analytics_events=int(analytics_events),
        storage_bytes=storage_bytes,
    )


@router.delete("/users/{user_id}", response_model=DeleteUserResponse)
async def delete_user(
    user_id: str,
    session: SessionDep,
    admin: AdminUserDep,
    runtime: _RuntimeDep,
    keycloak: _KeycloakDep,
) -> DeleteUserResponse:
    """Purge *user_id* and ALL their data (DB cascade + on-disk + S3 + Keycloak).

    Self-delete is refused. Because only an admin reaches this route and cannot
    delete themselves, the realm never loses its last admin here; last-admin
    protection is otherwise not server-enforceable (roles live in Keycloak, not
    this DB). See ``services/user_deletion`` for the full contract.
    """
    loader = get_module_loader()
    report = await delete_user_and_all_data(
        session,
        runtime,
        loader,
        keycloak,
        target_user_id=user_id,
        caller_id=admin.id,
    )
    return DeleteUserResponse(
        deleted=report.deleted,
        jobs_cancelled=report.jobs_cancelled,
        modules_torn_down=report.modules_torn_down,
        keycloak_deleted=report.keycloak_deleted,
        keycloak_skipped_reason=report.keycloak_skipped_reason,
        warnings=report.warnings,
    )
