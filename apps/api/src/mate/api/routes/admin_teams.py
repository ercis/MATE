"""Admin-only team management + dashboard-share oversight - `/api/v1/admin/*`.

Gated by ``AdminUserDep`` (the ``admin`` realm role). Operators create teams,
assign members, and can audit/revoke any dashboard share across all users.
Teams are the coarse share target; members are the people a user may share with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from mate.api.auth import AdminUserDep
from mate.api.db.models import Dashboard, DashboardShare, Team, TeamMember, User
from mate.api.db.session import SessionDep
from mate.api.schemas.sharing import (
    AdminShareOut,
    MemberAdd,
    TeamCreate,
    TeamMemberOut,
    TeamOut,
    TeamUpdate,
    UserBrief,
)
from mate.api.sharing import user_label
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _team_or_404(session: SessionDep, team_id: str) -> Team:
    team = await session.get(Team, team_id)
    if team is None or team.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Team not found.")
    return team


# --- Users (for member pickers) ---------------------------------------------


@router.get("/users", response_model=list[UserBrief])
async def list_users(session: SessionDep, _admin: AdminUserDep) -> list[UserBrief]:
    rows = (
        (await session.execute(select(User).order_by(User.name.asc(), User.email.asc())))
        .scalars()
        .all()
    )
    return [
        UserBrief(id=u.id, email=u.email, preferred_username=u.preferred_username, name=u.name)
        for u in rows
    ]


# --- Teams ------------------------------------------------------------------


@router.get("/teams", response_model=list[TeamOut])
async def list_teams(session: SessionDep, _admin: AdminUserDep) -> list[TeamOut]:
    teams = (
        (
            await session.execute(
                select(Team).where(Team.deleted_at.is_(None)).order_by(Team.name.asc())
            )
        )
        .scalars()
        .all()
    )
    count_rows = (
        await session.execute(select(TeamMember.team_id, func.count()).group_by(TeamMember.team_id))
    ).all()
    counts: dict[str, int] = {tid: int(c) for tid, c in count_rows}
    return [
        TeamOut(id=t.id, name=t.name, member_count=counts.get(t.id, 0), created_at=t.created_at)
        for t in teams
    ]


@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(payload: TeamCreate, session: SessionDep, _admin: AdminUserDep) -> TeamOut:
    team = Team(id=uuid7_str(), name=payload.name, created_at=_utcnow())
    session.add(team)
    await session.commit()
    log.info("team.created", team_id=team.id, name=team.name)
    return TeamOut(id=team.id, name=team.name, member_count=0, created_at=team.created_at)


@router.patch("/teams/{team_id}", response_model=TeamOut)
async def update_team(
    team_id: str, payload: TeamUpdate, session: SessionDep, _admin: AdminUserDep
) -> TeamOut:
    team = await _team_or_404(session, team_id)
    team.name = payload.name
    await session.commit()
    count = (
        await session.execute(select(func.count()).where(TeamMember.team_id == team_id))
    ).scalar_one()
    return TeamOut(id=team.id, name=team.name, member_count=int(count), created_at=team.created_at)


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(team_id: str, session: SessionDep, _admin: AdminUserDep) -> None:
    team = await _team_or_404(session, team_id)
    # Hard delete: FK cascades drop memberships and any shares targeting this
    # team, so access is revoked atomically.
    await session.delete(team)
    await session.commit()
    log.info("team.deleted", team_id=team_id)


# --- Members ----------------------------------------------------------------


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
async def list_members(
    team_id: str, session: SessionDep, _admin: AdminUserDep
) -> list[TeamMemberOut]:
    await _team_or_404(session, team_id)
    rows = (
        await session.execute(
            select(TeamMember, User)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.team_id == team_id)
            .order_by(User.name.asc())
        )
    ).all()
    return [
        TeamMemberOut(
            user_id=m.user_id,
            role=m.role,
            email=u.email,
            preferred_username=u.preferred_username,
            name=u.name,
            created_at=m.created_at,
        )
        for m, u in rows
    ]


@router.post(
    "/teams/{team_id}/members",
    response_model=TeamMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    team_id: str, payload: MemberAdd, session: SessionDep, _admin: AdminUserDep
) -> TeamMemberOut:
    await _team_or_404(session, team_id)
    target = await session.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if await session.get(TeamMember, (team_id, payload.user_id)) is not None:
        raise HTTPException(status_code=409, detail="Already a member.")
    member = TeamMember(
        team_id=team_id, user_id=payload.user_id, role=payload.role, created_at=_utcnow()
    )
    session.add(member)
    await session.commit()
    log.info("team.member_added", team_id=team_id, user_id=payload.user_id)
    return TeamMemberOut(
        user_id=member.user_id,
        role=member.role,
        email=target.email,
        preferred_username=target.preferred_username,
        name=target.name,
        created_at=member.created_at,
    )


@router.delete("/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: str, user_id: str, session: SessionDep, _admin: AdminUserDep
) -> None:
    member = await session.get(TeamMember, (team_id, user_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    await session.delete(member)
    await session.commit()
    log.info("team.member_removed", team_id=team_id, user_id=user_id)


# --- Share oversight --------------------------------------------------------


@router.get("/dashboard-shares", response_model=list[AdminShareOut])
async def list_all_shares(session: SessionDep, _admin: AdminUserDep) -> list[AdminShareOut]:
    shares = (
        (await session.execute(select(DashboardShare).order_by(DashboardShare.created_at.desc())))
        .scalars()
        .all()
    )
    out: list[AdminShareOut] = []
    for s in shares:
        dash = await session.get(Dashboard, s.dashboard_id)
        owner = await session.get(User, dash.user_id) if dash is not None else None
        target_kind: Literal["user", "team"]
        if s.target_team_id is not None:
            team = await session.get(Team, s.target_team_id)
            target_kind = "team"
            target_label = team.name if team is not None else "Deleted team"
        else:
            target_kind = "user"
            target_label = user_label(await session.get(User, s.target_user_id))
        out.append(
            AdminShareOut(
                id=s.id,
                dashboard_id=s.dashboard_id,
                dashboard_name=dash.name if dash is not None else "Deleted dashboard",
                owner_label=user_label(owner),
                target_kind=target_kind,
                target_label=target_label,
                created_at=s.created_at,
            )
        )
    return out


@router.delete("/dashboard-shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share(share_id: str, session: SessionDep, _admin: AdminUserDep) -> None:
    share = await session.get(DashboardShare, share_id)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found.")
    await session.delete(share)
    await session.commit()
    log.info("admin.share_revoked", share_id=share_id)
