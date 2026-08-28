"""Admin toolset: cross-user platform control - OAuth only, never a PAT.

Disabled unless ``MCP_TOOLSETS`` includes ``admin`` (or is "all"), and every
tool additionally gates through :func:`mate.api.mcp.core.authz_admin`: an
OAuth (Keycloak) principal carrying BOTH the ``admin`` scope and the ``admin``
realm role. PATs are role-less by construction, so they can never reach these.

Bodies reuse the admin REST route internals one-to-one
(:mod:`mate.api.routes.admin_jobs`, :mod:`~mate.api.routes.admin_insights`,
:mod:`~mate.api.routes.admin_teams`, :mod:`~mate.api.routes.system`,
:mod:`~mate.api.routes.mcp_admin`) so the two surfaces can't drift.

Hard exclusions by policy: ``/admin/export`` equivalents, raw event-log
downloads, and metadata-db export - the event-log listing here is metadata
only. (The storage backend itself is env-configured and has no writable
surface anywhere; see docs/S3_OFFLOAD.md.)
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, select

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import ApiToken, Dashboard, DashboardShare, Job, Team, TeamMember, User
from mate.api.jobs.runtime import MAX_WORKERS, MIN_WORKERS
from mate.api.mcp.core import MCPContext, authz_admin, cap, confirm_preview, guarded
from mate.api.mcp.errors import CODE_INVALID, CODE_NOT_FOUND, from_http_exception, tool_error
from mate.api.mcp.pagination import clamp_limit, decode_cursor, page_envelope
from mate.api.mcp.registry import mcp_tool
from mate.api.routes.admin_insights import event_logs as event_logs_route
from mate.api.routes.admin_insights import jobs_insights as jobs_insights_route
from mate.api.routes.admin_insights import overview as overview_route
from mate.api.routes.admin_insights import storage_insights as storage_insights_route
from mate.api.routes.admin_insights import usage_insights as usage_insights_route
from mate.api.routes.admin_insights import users_insights as users_insights_route
from mate.api.routes.admin_jobs import CancelAllBody, QueueBody
from mate.api.routes.admin_jobs import cancel_all as cancel_all_route
from mate.api.routes.admin_jobs import cancel_job as cancel_job_route
from mate.api.routes.admin_jobs import job_logs as job_logs_route
from mate.api.routes.admin_jobs import kill_job as kill_job_route
from mate.api.routes.admin_jobs import list_jobs as list_jobs_route
from mate.api.routes.admin_jobs import pause_queue as pause_queue_route
from mate.api.routes.admin_jobs import resume_queue as resume_queue_route
from mate.api.routes.admin_jobs import retry_job as retry_job_route
from mate.api.routes.admin_teams import add_member as add_member_route
from mate.api.routes.admin_teams import create_team as create_team_route
from mate.api.routes.admin_teams import delete_team as delete_team_route
from mate.api.routes.admin_teams import list_all_shares as list_all_shares_route
from mate.api.routes.admin_teams import list_teams as list_teams_route
from mate.api.routes.admin_teams import remove_member as remove_member_route
from mate.api.routes.admin_teams import revoke_share as revoke_share_route
from mate.api.routes.admin_teams import update_team as update_team_route
from mate.api.routes.mcp_admin import McpAdminUpdate
from mate.api.routes.mcp_admin import admin_list_tokens as list_tokens_route
from mate.api.routes.mcp_admin import admin_revoke_token as revoke_token_route
from mate.api.routes.mcp_admin import get_mcp_config as get_mcp_config_route
from mate.api.routes.mcp_admin import put_mcp_config as put_mcp_config_route
from mate.api.routes.system import JobsConfigIn
from mate.api.routes.system import get_jobs_config as get_jobs_config_route
from mate.api.routes.system import put_jobs_config as put_jobs_config_route
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.sharing import MemberAdd, TeamCreate, TeamUpdate
from mate.api.sharing import user_label

_INSIGHTS_SECTIONS = ("overview", "users", "storage", "jobs", "usage")
_MINT_POLICIES = ("all_users", "admin_only", "disabled")
_ACTIVE_STATUSES = ("queued", "running")
_JOB_LOGS_MAX = 1000


def _validated_team_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 255:
        raise tool_error(CODE_INVALID, "Team name must be 1-255 characters.")
    return cleaned


# ── jobs ─────────────────────────────────────────────────────────────────────


@mcp_tool(toolset="admin", idempotent=True)
async def admin_list_jobs(
    ctx: MCPContext,
    q: str | None = None,
    status: str | None = None,
    type: str | None = None,
    user_id: str | None = None,
    active_only: bool = False,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List jobs across ALL users, newest first, joined with owner + log name.

    ``q`` matches the job title or the owner's email/username; ``status`` /
    ``type`` / ``user_id`` / ``active_only`` filter. ``cursor``/``limit``
    paginate (pass back ``next_cursor``). ``summary`` carries the status
    breakdown (entity filters only), the active total and the paused users.
    """
    p = await authz_admin(ctx)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            res = await list_jobs_route(
                user=p.user,
                session=session,
                q=q,
                status_filter=status,
                type_filter=type,
                user_id=user_id,
                active_only=active_only,
                limit=size,
                offset=offset,
            )
        items = [r.model_dump(mode="json") for r in res.items]
        out = page_envelope(items, offset=offset, limit=size, total=res.total)
        out["summary"] = res.summary.model_dump(mode="json")
        return cast(dict[str, Any], cap(out))

    labels = {"q": q or "", "status": status or "", "type": type or "", "user_id": user_id or ""}
    return await guarded(p, "admin_list_jobs", labels, _impl())


@mcp_tool(toolset="admin", idempotent=True)
async def admin_get_job_logs(ctx: MCPContext, job_id: str, limit: int = 500) -> dict[str, Any]:
    """Recent ``ctx.logger`` lines captured for one job (any user's).

    Reads the bounded in-memory per-job ring (operator view) - empty for a job
    that logged nothing, ran before this process started, or whose lines aged
    out; ``truncated`` flags evicted older lines.
    """
    p = await authz_admin(ctx)
    size = max(1, min(int(limit), _JOB_LOGS_MAX))

    async def _impl() -> dict[str, Any]:
        res = await job_logs_route(job_id, user=p.user, limit=size)
        return cast(dict[str, Any], cap(res.model_dump(mode="json")))

    return await guarded(p, "admin_get_job_logs", {"job_id": job_id}, _impl())


@mcp_tool(toolset="admin", write=True)
async def admin_cancel_job(ctx: MCPContext, job_id: str) -> dict[str, Any]:
    """Cooperatively cancel any user's queued/running job."""
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        try:
            await cancel_job_route(job_id, user=p.user)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return {"job_id": job_id, "cancelled": True}

    return await guarded(p, "admin_cancel_job", {"job_id": job_id}, _impl(), mutation=True)


@mcp_tool(toolset="admin", write=True)
async def admin_retry_job(ctx: MCPContext, job_id: str) -> dict[str, str]:
    """Re-enqueue any user's failed job with the same payload; returns the new job id."""
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, str]:
        try:
            return await retry_job_route(job_id, user=p.user)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc

    return await guarded(p, "admin_retry_job", {"job_id": job_id}, _impl(), mutation=True)


@mcp_tool(toolset="admin", write=True, destructive=True)
async def admin_kill_job(ctx: MCPContext, job_id: str, confirm: bool = False) -> dict[str, Any]:
    """Hard-kill any user's job NOW - SIGKILLs its whole process tree, skipping
    the cooperative grace window. For a job that ignores a normal cancel.

    Without ``confirm`` this is a dry run returning the job's type/title/owner;
    pass ``confirm=true`` to execute.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(
                    select(Job, User).join(User, Job.user_id == User.id).where(Job.id == job_id)
                )
            ).first()
        if row is None:
            raise tool_error(CODE_NOT_FOUND, "Job not found")
        job, owner = row
        if not confirm:
            return confirm_preview(
                "admin_kill_job",
                {
                    "job_id": job.id,
                    "type": job.type,
                    "title": job.title,
                    "status": job.status,
                    "owner_id": owner.id,
                    "owner_email": owner.email,
                },
            )
        try:
            await kill_job_route(job_id, user=p.user)
        except HTTPException as exc:
            raise from_http_exception(exc) from exc
        return {"confirmed": True, "job_id": job_id, "killed": True}

    return await guarded(
        p, "admin_kill_job", {"job_id": job_id, "confirm": confirm}, _impl(), mutation=True
    )


@mcp_tool(toolset="admin", write=True, destructive=True)
async def admin_cancel_all(
    ctx: MCPContext, user_id: str | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Cancel every active (queued/running) job - across ALL users, or scoped
    to one user via ``user_id``.

    Without ``confirm`` this is a dry run returning the affected counts; pass
    ``confirm=true`` to execute.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            conds: list[ColumnElement[bool]] = [Job.status.in_(_ACTIVE_STATUSES)]
            if user_id:
                conds.append(Job.user_id == user_id)
            counts = {
                str(s): int(c)
                for s, c in (
                    await session.execute(
                        select(Job.status, func.count()).where(*conds).group_by(Job.status)
                    )
                ).all()
            }
            queued = counts.get("queued", 0)
            running = counts.get("running", 0)
            if not confirm:
                return confirm_preview(
                    "admin_cancel_all",
                    {
                        "user_id": user_id,
                        "queued": queued,
                        "running": running,
                        "total": queued + running,
                    },
                )
            res = await cancel_all_route(
                CancelAllBody(user_id=user_id), user=p.user, session=session
            )
        return {"confirmed": True, **res}

    return await guarded(
        p,
        "admin_cancel_all",
        {"user_id": user_id or "", "confirm": confirm},
        _impl(),
        mutation=True,
    )


async def _ensure_user_exists(user_id: str) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(User, user_id) is None:
            raise tool_error(CODE_NOT_FOUND, "User not found")


@mcp_tool(toolset="admin", write=True, idempotent=True)
async def admin_pause_user_queue(ctx: MCPContext, user_id: str) -> dict[str, Any]:
    """Pause one user's job queue (running jobs finish; queued ones park).

    Per-tenant: every other user's queue keeps flowing. Resume with
    ``admin_resume_user_queue``.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        await _ensure_user_exists(user_id)
        await pause_queue_route(QueueBody(user_id=user_id), user=p.user)
        return {"user_id": user_id, "paused": True}

    return await guarded(p, "admin_pause_user_queue", {"user_id": user_id}, _impl(), mutation=True)


@mcp_tool(toolset="admin", write=True, idempotent=True)
async def admin_resume_user_queue(ctx: MCPContext, user_id: str) -> dict[str, Any]:
    """Resume one user's paused job queue (parked jobs are re-enqueued)."""
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        await _ensure_user_exists(user_id)
        await resume_queue_route(QueueBody(user_id=user_id), user=p.user)
        return {"user_id": user_id, "paused": False}

    return await guarded(p, "admin_resume_user_queue", {"user_id": user_id}, _impl(), mutation=True)


# ── users / insights (read-only) ─────────────────────────────────────────────


@mcp_tool(toolset="admin", idempotent=True)
async def admin_list_users(ctx: MCPContext) -> list[dict[str, Any]]:
    """List every user account: id, email, username, name, created/last-seen."""
    p = await authz_admin(ctx)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                (await session.execute(select(User).order_by(User.created_at.asc(), User.id)))
                .scalars()
                .all()
            )
            items = [
                {
                    "id": u.id,
                    "email": u.email,
                    "username": u.preferred_username,
                    "name": u.name,
                    "created_at": utc_isoformat(u.created_at),
                    "last_seen_at": utc_isoformat(u.last_seen_at),
                }
                for u in rows
            ]
        return cast(list[dict[str, Any]], cap(items))

    return await guarded(p, "admin_list_users", {}, _impl())


@mcp_tool(toolset="admin", idempotent=True)
async def admin_insights(ctx: MCPContext, section: str, days: int = 30) -> dict[str, Any]:
    """Platform-wide admin metrics. ``section`` picks the dashboard:

    ``overview`` (KPIs + time series), ``users`` (activity/recency),
    ``storage`` (per-user log/event totals + backend state, without the
    expensive disk walk), ``jobs`` (live runtime snapshot + outcomes) or
    ``usage`` (module + AI usage). ``days`` (1-365) bounds the time series
    where the section supports it.
    """
    p = await authz_admin(ctx)
    if section not in _INSIGHTS_SECTIONS:
        raise tool_error(CODE_INVALID, f"section must be one of {', '.join(_INSIGHTS_SECTIONS)}")

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            if section == "overview":
                res = await overview_route(user=p.user, session=session, days=days)
            elif section == "users":
                res = await users_insights_route(user=p.user, session=session, days=days)
            elif section == "storage":
                # include_disk=0: never the bounded-but-heavy per-user byte walk.
                res = await storage_insights_route(user=p.user, session=session, include_disk=0)
            elif section == "jobs":
                res = await jobs_insights_route(user=p.user, session=session, days=days)
            else:
                res = await usage_insights_route(user=p.user, session=session, days=days)
        return cast(dict[str, Any], cap(res.model_dump(mode="json")))

    return await guarded(p, "admin_insights", {"section": section, "days": days}, _impl())


@mcp_tool(toolset="admin", idempotent=True)
async def admin_list_event_logs(
    ctx: MCPContext,
    q: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List every user's (non-deleted) event logs - METADATA ONLY, with owner.

    Rows carry owner email/username, name, status, log model, counts and
    dates - never event data. There is deliberately no download tool: raw log
    contents don't leave the platform over MCP. ``q`` matches the log name or
    the owner's email/username; ``cursor``/``limit`` paginate.
    """
    p = await authz_admin(ctx)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            res = await event_logs_route(
                user=p.user,
                session=session,
                q=q,
                status=status,
                sort="created_at",
                order="desc",
                limit=size,
                offset=offset,
            )
        items = [r.model_dump(mode="json") for r in res.items]
        out = page_envelope(items, offset=offset, limit=size, total=res.total)
        return cast(dict[str, Any], cap(out))

    labels = {"q": q or "", "status": status or ""}
    return await guarded(p, "admin_list_event_logs", labels, _impl())


# ── platform config ──────────────────────────────────────────────────────────


@mcp_tool(toolset="admin", idempotent=True)
async def admin_get_worker_pool(ctx: MCPContext) -> dict[str, Any]:
    """Current job-runtime worker concurrency plus the allowed min/max bounds."""
    p = await authz_admin(ctx)

    async def _impl() -> dict[str, Any]:
        res = await get_jobs_config_route(user=p.user)
        return res.model_dump(mode="json")

    return await guarded(p, "admin_get_worker_pool", {}, _impl())


@mcp_tool(toolset="admin", write=True, idempotent=True)
async def admin_set_worker_pool(ctx: MCPContext, concurrency: int) -> dict[str, Any]:
    """Resize the job worker pool live and persist the value across restarts.

    Graceful: running jobs are never interrupted (scale-down retires workers
    as they go idle). ``concurrency`` must be within the returned min/max.
    """
    p = await authz_admin(ctx, write=True)
    if not MIN_WORKERS <= int(concurrency) <= MAX_WORKERS:
        raise tool_error(
            CODE_INVALID, f"concurrency must be between {MIN_WORKERS} and {MAX_WORKERS}."
        )

    async def _impl() -> dict[str, Any]:
        res = await put_jobs_config_route(
            JobsConfigIn(worker_concurrency=int(concurrency)), user=p.user
        )
        return res.model_dump(mode="json")

    return await guarded(
        p, "admin_set_worker_pool", {"concurrency": concurrency}, _impl(), mutation=True
    )


@mcp_tool(toolset="admin", idempotent=True)
async def admin_get_mcp_config(ctx: MCPContext) -> dict[str, Any]:
    """MCP server governance state: boot flags, live enabled/read-only toggles,
    PAT mint policy and the toolsets registered at boot."""
    p = await authz_admin(ctx)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            res = await get_mcp_config_route(session=session, user=p.user)
        return res.model_dump(mode="json")

    return await guarded(p, "admin_get_mcp_config", {}, _impl())


@mcp_tool(toolset="admin", write=True, idempotent=True)
async def admin_set_mcp_config(
    ctx: MCPContext,
    enabled: bool | None = None,
    mint_policy: str | None = None,
    read_only: bool | None = None,
) -> dict[str, Any]:
    """Update live MCP governance: ``enabled`` (availability kill switch),
    ``mint_policy`` (all_users|admin_only|disabled) and ``read_only`` (write
    lock). Only the fields you pass change; returns the new effective config.

    WARNING: ``enabled=false`` over MCP is allowed as the admin kill switch -
    it disables the whole MCP server, INCLUDING this very connection, within
    ~5s (the availability cache TTL). Re-enable from the web admin UI
    (Admin → MCP), not over MCP.
    """
    p = await authz_admin(ctx, write=True)
    if mint_policy is not None and mint_policy not in _MINT_POLICIES:
        raise tool_error(CODE_INVALID, f"mint_policy must be one of {', '.join(_MINT_POLICIES)}")

    async def _impl() -> dict[str, Any]:
        # The membership check above narrows ``mint_policy`` to the policy Literal.
        body = McpAdminUpdate(enabled=enabled, mint_policy=mint_policy, read_only=read_only)
        sm = get_sessionmaker()
        async with sm() as session:
            res = await put_mcp_config_route(body, session=session, user=p.user)
        return res.model_dump(mode="json")

    labels = {
        "enabled": "" if enabled is None else str(enabled),
        "mint_policy": mint_policy or "",
        "read_only": "" if read_only is None else str(read_only),
    }
    return await guarded(p, "admin_set_mcp_config", labels, _impl(), mutation=True)


# ── teams / shares / tokens ──────────────────────────────────────────────────


@mcp_tool(toolset="admin", idempotent=True)
async def admin_list_teams(ctx: MCPContext) -> list[dict[str, Any]]:
    """List all teams with member counts (teams are the dashboard-share targets)."""
    p = await authz_admin(ctx)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await list_teams_route(session=session, _admin=p.user)
        return cast(list[dict[str, Any]], cap([t.model_dump(mode="json") for t in rows]))

    return await guarded(p, "admin_list_teams", {}, _impl())


@mcp_tool(toolset="admin", write=True)
async def admin_create_team(ctx: MCPContext, name: str) -> dict[str, Any]:
    """Create a team (name 1-255 chars). Add people via ``admin_add_team_member``."""
    p = await authz_admin(ctx, write=True)
    cleaned = _validated_team_name(name)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            res = await create_team_route(TeamCreate(name=cleaned), session=session, _admin=p.user)
        return res.model_dump(mode="json")

    return await guarded(p, "admin_create_team", {"name": cleaned}, _impl(), mutation=True)


@mcp_tool(toolset="admin", write=True, idempotent=True)
async def admin_rename_team(ctx: MCPContext, team_id: str, name: str) -> dict[str, Any]:
    """Rename a team (existing shares keep working; only the label changes)."""
    p = await authz_admin(ctx, write=True)
    cleaned = _validated_team_name(name)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                res = await update_team_route(
                    team_id, TeamUpdate(name=cleaned), session=session, _admin=p.user
                )
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return res.model_dump(mode="json")

    return await guarded(
        p, "admin_rename_team", {"team_id": team_id, "name": cleaned}, _impl(), mutation=True
    )


@mcp_tool(toolset="admin", write=True, destructive=True)
async def admin_delete_team(ctx: MCPContext, team_id: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a team - cascades its memberships AND every dashboard share
    targeting it, revoking that access atomically.

    Without ``confirm`` this is a dry run returning the member count and the
    number of shares that would be revoked; pass ``confirm=true`` to execute.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            team = await session.get(Team, team_id)
            if team is None or team.deleted_at is not None:
                raise tool_error(CODE_NOT_FOUND, "Team not found.")
            member_count = int(
                (
                    await session.execute(select(func.count()).where(TeamMember.team_id == team_id))
                ).scalar_one()
            )
            share_count = int(
                (
                    await session.execute(
                        select(func.count()).where(DashboardShare.target_team_id == team_id)
                    )
                ).scalar_one()
            )
            if not confirm:
                return confirm_preview(
                    "admin_delete_team",
                    {
                        "team_id": team_id,
                        "name": team.name,
                        "member_count": member_count,
                        "share_count": share_count,
                    },
                )
            try:
                await delete_team_route(team_id, session=session, _admin=p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"confirmed": True, "deleted": True, "team_id": team_id}

    return await guarded(
        p, "admin_delete_team", {"team_id": team_id, "confirm": confirm}, _impl(), mutation=True
    )


@mcp_tool(toolset="admin", write=True)
async def admin_add_team_member(ctx: MCPContext, team_id: str, user_id: str) -> dict[str, Any]:
    """Add a user to a team (grants access to dashboards shared with that team)."""
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            try:
                res = await add_member_route(
                    team_id, MemberAdd(user_id=user_id), session=session, _admin=p.user
                )
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return res.model_dump(mode="json")

    return await guarded(
        p,
        "admin_add_team_member",
        {"team_id": team_id, "user_id": user_id},
        _impl(),
        mutation=True,
    )


@mcp_tool(toolset="admin", write=True, destructive=True)
async def admin_remove_team_member(
    ctx: MCPContext, team_id: str, user_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Remove a user from a team - they immediately lose access to every
    dashboard shared with that team (those shares orphan for them).

    Without ``confirm`` this is a dry run returning the member and how many
    team-targeted shares they would lose; pass ``confirm=true`` to execute.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            member = await session.get(TeamMember, (team_id, user_id))
            if member is None:
                raise tool_error(CODE_NOT_FOUND, "Member not found.")
            team = await session.get(Team, team_id)
            target = await session.get(User, user_id)
            share_count = int(
                (
                    await session.execute(
                        select(func.count()).where(DashboardShare.target_team_id == team_id)
                    )
                ).scalar_one()
            )
            if not confirm:
                return confirm_preview(
                    "admin_remove_team_member",
                    {
                        "team_id": team_id,
                        "team_name": team.name if team is not None else None,
                        "user_id": user_id,
                        "member": user_label(target),
                        "team_share_count": share_count,
                    },
                )
            try:
                await remove_member_route(team_id, user_id, session=session, _admin=p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"confirmed": True, "removed": True, "team_id": team_id, "user_id": user_id}

    return await guarded(
        p,
        "admin_remove_team_member",
        {"team_id": team_id, "user_id": user_id, "confirm": confirm},
        _impl(),
        mutation=True,
    )


@mcp_tool(toolset="admin", idempotent=True)
async def admin_list_dashboard_shares(ctx: MCPContext) -> list[dict[str, Any]]:
    """List every dashboard share across all users (owner, dashboard, target)."""
    p = await authz_admin(ctx)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await list_all_shares_route(session=session, _admin=p.user)
        return cast(list[dict[str, Any]], cap([s.model_dump(mode="json") for s in rows]))

    return await guarded(p, "admin_list_dashboard_shares", {}, _impl())


@mcp_tool(toolset="admin", write=True, destructive=True)
async def admin_revoke_dashboard_share(
    ctx: MCPContext, share_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Revoke any dashboard share - the target immediately loses access.

    Without ``confirm`` this is a dry run returning the dashboard, owner and
    target; pass ``confirm=true`` to execute.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            share = await session.get(DashboardShare, share_id)
            if share is None:
                raise tool_error(CODE_NOT_FOUND, "Share not found.")
            if not confirm:
                dash = await session.get(Dashboard, share.dashboard_id)
                owner = await session.get(User, dash.user_id) if dash is not None else None
                if share.target_team_id is not None:
                    team = await session.get(Team, share.target_team_id)
                    target_kind = "team"
                    target_label = team.name if team is not None else "Deleted team"
                else:
                    target_kind = "user"
                    target_label = user_label(await session.get(User, share.target_user_id))
                return confirm_preview(
                    "admin_revoke_dashboard_share",
                    {
                        "share_id": share_id,
                        "dashboard_id": share.dashboard_id,
                        "dashboard_name": dash.name if dash is not None else "Deleted dashboard",
                        "owner": user_label(owner),
                        "target_kind": target_kind,
                        "target_label": target_label,
                    },
                )
            try:
                await revoke_share_route(share_id, session=session, _admin=p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"confirmed": True, "revoked": True, "share_id": share_id}

    return await guarded(
        p,
        "admin_revoke_dashboard_share",
        {"share_id": share_id, "confirm": confirm},
        _impl(),
        mutation=True,
    )


@mcp_tool(toolset="admin", idempotent=True)
async def admin_list_api_tokens(ctx: MCPContext) -> list[dict[str, Any]]:
    """List every user's API tokens (PATs) - prefix only, never the secret."""
    p = await authz_admin(ctx)

    async def _impl() -> list[dict[str, Any]]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await list_tokens_route(session=session, user=p.user)
        return cast(list[dict[str, Any]], cap([t.model_dump(mode="json") for t in rows]))

    return await guarded(p, "admin_list_api_tokens", {}, _impl())


@mcp_tool(toolset="admin", write=True, destructive=True)
async def admin_revoke_api_token(
    ctx: MCPContext, token_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Revoke any user's API token (irreversible; the PAT stops working at once).

    Without ``confirm`` this is a dry run returning the token's name, prefix
    and owner; pass ``confirm=true`` to execute.
    """
    p = await authz_admin(ctx, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(
                    select(ApiToken, User.email)
                    .join(User, ApiToken.user_id == User.id)
                    .where(ApiToken.id == token_id)
                )
            ).first()
            if row is None:
                raise tool_error(CODE_NOT_FOUND, "Token not found")
            token, owner_email = row
            if not confirm:
                return confirm_preview(
                    "admin_revoke_api_token",
                    {
                        "token_id": token.id,
                        "name": token.name,
                        "token_prefix": token.token_prefix,
                        "owner_id": token.user_id,
                        "owner_email": owner_email,
                        "already_revoked": token.revoked,
                    },
                )
            try:
                await revoke_token_route(token_id, session=session, user=p.user)
            except HTTPException as exc:
                raise from_http_exception(exc) from exc
        return {"confirmed": True, "revoked": True, "token_id": token_id}

    return await guarded(
        p,
        "admin_revoke_api_token",
        {"token_id": token_id, "confirm": confirm},
        _impl(),
        mutation=True,
    )
