"""User-facing sharing collection routes - `/api/v1/sharing/*`.

Per-dashboard share management (add/remove/list) lives on the dashboards router
(`/dashboards/{id}/shares`); this module holds the cross-dashboard views:

  - what's been shared *with me* (the recipient's inbox), and
  - who I *can* share with (my teams + co-members), to populate the share picker.

Sharing targets are deliberately scoped to people you share a team with - you
can't share with arbitrary strangers, only members of your own teams.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from sqlalchemy import or_, select

from mate.api.auth import CurrentUserDep
from mate.api.db.models import Dashboard, DashboardShare, Team, TeamMember, User
from mate.api.db.session import SessionDep
from mate.api.schemas.sharing import SharedDashboard, ShareTarget
from mate.api.sharing import user_label, user_team_ids

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sharing", tags=["sharing"])


@router.get("/shared-with-me", response_model=list[SharedDashboard])
async def shared_with_me(session: SessionDep, user: CurrentUserDep) -> list[SharedDashboard]:
    """Dashboards other users have shared with me (directly or via a team)."""
    team_ids = await user_team_ids(session, user.id)
    conds = [DashboardShare.target_user_id == user.id]
    if team_ids:
        conds.append(DashboardShare.target_team_id.in_(team_ids))
    stmt = (
        select(Dashboard, User)
        .join(DashboardShare, DashboardShare.dashboard_id == Dashboard.id)
        .join(User, User.id == Dashboard.user_id)
        .where(or_(*conds))
        .order_by(Dashboard.updated_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    out: list[SharedDashboard] = []
    seen: set[str] = set()
    for dash, owner in rows:
        # A board shared both directly and via a team appears once.
        if dash.id in seen:
            continue
        seen.add(dash.id)
        out.append(
            SharedDashboard(
                id=dash.id,
                name=dash.name,
                description=dash.description,
                event_log_id=dash.event_log_id,
                log_model=dash.log_model,
                card_count=len((dash.layout_json or {}).get("items", [])),
                owner_label=user_label(owner),
                updated_at=dash.updated_at,
            )
        )
    return out


@router.get("/targets", response_model=list[ShareTarget])
async def share_targets(session: SessionDep, user: CurrentUserDep) -> list[ShareTarget]:
    """Candidates the current user may share a dashboard with: their teams, and
    the co-members of those teams (excluding self)."""
    team_ids = await user_team_ids(session, user.id)
    if not team_ids:
        return []

    teams = (
        (
            await session.execute(
                select(Team)
                .where(Team.id.in_(team_ids), Team.deleted_at.is_(None))
                .order_by(Team.name.asc())
            )
        )
        .scalars()
        .all()
    )
    targets: list[ShareTarget] = [
        ShareTarget(kind="team", id=t.id, label=t.name, sublabel="Team") for t in teams
    ]

    members = (
        (
            await session.execute(
                select(User)
                .join(TeamMember, TeamMember.user_id == User.id)
                .where(TeamMember.team_id.in_(team_ids), TeamMember.user_id != user.id)
                .order_by(User.name.asc())
            )
        )
        .scalars()
        .all()
    )
    seen: set[str] = set()
    for u in members:
        if u.id in seen:
            continue
        seen.add(u.id)
        targets.append(ShareTarget(kind="user", id=u.id, label=user_label(u), sublabel=u.email))
    return targets
