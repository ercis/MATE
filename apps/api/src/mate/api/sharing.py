"""Dashboard sharing - the one sanctioned cross-account access path.

The platform is otherwise strictly per-user: every row keys on ``user_id`` and a
log's Parquet lives under ``data/users/{owner_id}/``. A ``DashboardShare`` is the
*only* way a resource crosses an account boundary, and it grants **read** access
only - recipients never mutate the dashboard or the underlying log.

Keeping every "may this user read X" predicate in this module means the
isolation-widening surface is small and auditable. Two consumers:

  - route handlers, via :func:`get_accessible_dashboard` (owner-or-shared view);
  - the module context builder, via :func:`user_can_read_log` (so a shared
    dashboard's cards can read the owner's log data - see ``loader._make_context``).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import Dashboard, DashboardShare, TeamMember, User


def user_label(u: User | None) -> str:
    """Best display name for a user: name → username → email → id."""
    if u is None:
        return "Unknown user"
    return u.name or u.preferred_username or u.email or u.id


async def user_team_ids(session: AsyncSession, user_id: str) -> set[str]:
    """The ids of every team *user_id* is a member of (possibly empty)."""
    rows = await session.execute(select(TeamMember.team_id).where(TeamMember.user_id == user_id))
    return set(rows.scalars().all())


def _share_target_clause(user_id: str, team_ids: set[str]):
    """SQL predicate: a share targets *user_id* directly or one of *team_ids*."""
    conds = [DashboardShare.target_user_id == user_id]
    if team_ids:
        conds.append(DashboardShare.target_team_id.in_(team_ids))
    return or_(*conds)


async def dashboard_shared_with(
    session: AsyncSession, dashboard_id: str, user_id: str, team_ids: set[str]
) -> bool:
    """Is *dashboard_id* shared with *user_id* (directly or via *team_ids*)?"""
    stmt = (
        select(DashboardShare.id)
        .where(
            DashboardShare.dashboard_id == dashboard_id,
            _share_target_clause(user_id, team_ids),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def get_accessible_dashboard(
    session: AsyncSession, dashboard_id: str, user_id: str
) -> Dashboard:
    """Return a dashboard the user may *view* - owner or share recipient.

    404 (not 403) on no-access, mirroring the per-user ownership helpers so a
    non-recipient can't distinguish "missing" from "exists but not shared".
    Mutations keep using the owner-only helper in ``routes/dashboards.py``.
    """
    row = await session.get(Dashboard, dashboard_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    if row.user_id == user_id:
        return row
    team_ids = await user_team_ids(session, user_id)
    if await dashboard_shared_with(session, dashboard_id, user_id, team_ids):
        return row
    raise HTTPException(status_code=404, detail="Dashboard not found.")


async def can_share_with_team(session: AsyncSession, team_id: str, user_id: str) -> bool:
    """A user may share with a team only if they belong to it - so a board
    can't be pushed to a team the sharer isn't part of."""
    return await session.get(TeamMember, (team_id, user_id)) is not None


async def can_share_with_user(session: AsyncSession, target_user_id: str, user_id: str) -> bool:
    """A user may share directly only with someone they share a team with -
    the same scope the ``/sharing/targets`` picker offers."""
    my_teams = await user_team_ids(session, user_id)
    if not my_teams:
        return False
    stmt = (
        select(TeamMember.team_id)
        .where(TeamMember.user_id == target_user_id, TeamMember.team_id.in_(my_teams))
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def user_can_read_log(session: AsyncSession, log_id: str, user_id: str) -> bool:
    """Does *user_id* have read access to *log_id*'s data via a shared dashboard?

    True iff some dashboard bound to this log is shared with the user (directly
    or through a team). The owner case is handled by the caller - this answers
    only the cross-account question. A dashboard delete cascades its shares, so
    revoking access is automatic.
    """
    team_ids = await user_team_ids(session, user_id)
    dash_stmt = (
        select(DashboardShare.id)
        .join(Dashboard, Dashboard.id == DashboardShare.dashboard_id)
        .where(
            Dashboard.event_log_id == log_id,
            _share_target_clause(user_id, team_ids),
        )
        .limit(1)
    )
    return (await session.execute(dash_stmt)).first() is not None
