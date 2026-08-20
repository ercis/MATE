"""/api/v1/admin/jobs - cross-user job monitoring + control (admin role).

Unlike ``/api/v1/jobs/*`` (strictly per-user, ownership-enforced via
``get_owned_job``), this surface lets an admin see and control EVERY user's
jobs - the operator view behind the admin "Jobs" tab. Listing joins each job to
its owner and, when the payload carries a ``log_id``, to its event log so the UI
can group by user or by event log. Control reuses the exact ``JobRuntime``
primitives the per-user routes use (cancel / retry / cancel-all / per-user
pause-resume), just without the ownership gate.

Admin-gated by the Keycloak ``admin`` role. The event-bus ``user_id``
tenant-isolation invariant does not apply here: these are deliberately
cross-user, admin-only REST calls.

See ``apps/web/app/(platform)/admin/jobs`` for the UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select

from mate.api.auth import AdminUserDep
from mate.api.db.models import EventLog, Job, User
from mate.api.db.session import SessionDep
from mate.api.jobs.runtime import get_job_runtime
from mate.api.modules.job_logs import get_job_log_buffer

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/jobs", tags=["admin"])

# Statuses a job can still be acted on (cancel / cancel-all target these).
_ACTIVE = ("queued", "running")


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


class AdminJobRow(BaseModel):
    id: str
    type: str
    title: str
    subtitle: str | None
    module_id: str | None
    status: str
    progress_current: int
    progress_total: int | None
    stage: str | None
    message: str | None
    error: str | None
    rate: float | None
    eta_seconds: float | None
    priority: int
    parent_job_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    owner_id: str
    owner_email: str | None
    owner_username: str | None
    log_id: str | None
    log_name: str | None
    queue_paused: bool


class LabelCount(BaseModel):
    label: str
    count: int


class AdminJobSummary(BaseModel):
    by_status: list[LabelCount]
    active_total: int
    paused_users: list[str]


class AdminJobList(BaseModel):
    total: int
    items: list[AdminJobRow]
    summary: AdminJobSummary


class AdminJobLogLine(BaseModel):
    ts: float
    level: str
    event: str
    fields: dict[str, Any]


class AdminJobLogs(BaseModel):
    job_id: str
    lines: list[AdminJobLogLine]
    truncated: bool


@router.get("/{job_id}/logs", response_model=AdminJobLogs)
async def job_logs(
    job_id: str,
    user: AdminUserDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> AdminJobLogs:
    """Recent module-log lines captured for one job (admin operator view).

    Lines are the job's ``ctx.logger`` output - both in-process and subprocess
    modules funnel through the loader's bus-forwarding logger, which mirrors each
    line into a bounded per-job ring (``mate.api.modules.job_logs``). In-memory
    and cross-user by design, like the rest of this admin surface. Empty for a job
    that logged nothing, ran before this build, or whose lines aged out of the
    ring; ``truncated`` flags that older lines were evicted (per-job cap).
    """
    buf = get_job_log_buffer()
    lines = buf.get(job_id, limit=limit)
    return AdminJobLogs(
        job_id=job_id,
        lines=[
            AdminJobLogLine(ts=ln.ts, level=ln.level, event=ln.event, fields=ln.fields)
            for ln in lines
        ],
        truncated=buf.truncated(job_id),
    )


def _payload_log_id(payload: object) -> str | None:
    """Pull the affiliated event-log id out of a job payload, if any.

    Jobs don't carry an indexed ``log_id`` column - the affiliation lives in
    ``payload_json["log_id"]`` (same convention as ``runtime.cancel_for_logs``).
    """
    if isinstance(payload, dict):
        raw = payload.get("log_id")
        if isinstance(raw, str):
            return raw
    return None


@router.get("", response_model=AdminJobList)
async def list_jobs(
    user: AdminUserDep,
    session: SessionDep,
    q: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    user_id: str | None = None,
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminJobList:
    """List jobs across all users with owner + event-log context.

    ``q`` matches the job title or the owner's email/username (case-insensitive).
    The status breakdown in ``summary`` applies only the *entity* filters
    (``user_id`` / ``type`` / ``q``) so the status chips stay meaningful as the
    user toggles between statuses.
    """
    # Entity filters scope which jobs are in view (owner / type / search); the
    # status + active filters then slice within that scope.
    entity_filters: list[ColumnElement[bool]] = []
    if user_id:
        entity_filters.append(Job.user_id == user_id)
    if type_filter:
        entity_filters.append(Job.type == type_filter)
    if q:
        like = f"%{q}%"
        entity_filters.append(
            or_(
                Job.title.ilike(like),
                User.email.ilike(like),
                User.preferred_username.ilike(like),
            )
        )

    row_filters = list(entity_filters)
    if active_only:
        row_filters.append(Job.status.in_(_ACTIVE))
    if status_filter:
        row_filters.append(Job.status == status_filter)

    total = int(
        await session.scalar(
            select(func.count())
            .select_from(Job)
            .join(User, Job.user_id == User.id)
            .where(*row_filters)
        )
        or 0
    )

    rows = (
        await session.execute(
            select(Job, User)
            .join(User, Job.user_id == User.id)
            .where(*row_filters)
            .order_by(Job.created_at.desc(), Job.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    # Batch-resolve event-log names for the page (most jobs carry none).
    log_ids = {lid for job, _ in rows if (lid := _payload_log_id(job.payload_json)) is not None}
    log_names: dict[str, str] = {}
    if log_ids:
        log_names = {
            str(lid): name
            for lid, name in (
                await session.execute(
                    select(EventLog.id, EventLog.name).where(EventLog.id.in_(log_ids))
                )
            ).all()
        }

    paused = set(get_job_runtime().paused_user_ids())

    items = [
        AdminJobRow(
            id=job.id,
            type=job.type,
            title=job.title,
            subtitle=job.subtitle,
            module_id=job.module_id,
            status=job.status,
            progress_current=job.progress_current,
            progress_total=job.progress_total,
            stage=job.stage,
            message=job.message,
            error=job.error,
            rate=job.rate,
            eta_seconds=job.eta_seconds,
            priority=job.priority,
            parent_job_id=job.parent_job_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            owner_id=owner.id,
            owner_email=owner.email,
            owner_username=owner.preferred_username,
            log_id=(lid := _payload_log_id(job.payload_json)),
            log_name=log_names.get(lid) if lid else None,
            queue_paused=owner.id in paused,
        )
        for job, owner in rows
    ]

    by_status = [
        LabelCount(label=str(s), count=int(c))
        for s, c in (
            await session.execute(
                select(Job.status, func.count())
                .join(User, Job.user_id == User.id)
                .where(*entity_filters)
                .group_by(Job.status)
                .order_by(func.count().desc())
            )
        ).all()
    ]
    active_total = int(
        await session.scalar(
            select(func.count())
            .select_from(Job)
            .join(User, Job.user_id == User.id)
            .where(*entity_filters, Job.status.in_(_ACTIVE))
        )
        or 0
    )

    return AdminJobList(
        total=total,
        items=items,
        summary=AdminJobSummary(
            by_status=by_status,
            active_total=active_total,
            paused_users=sorted(paused),
        ),
    )


# --------------------------------------------------------------------------
# Control - cross-user mutations (admin only)
# --------------------------------------------------------------------------


@router.post("/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(job_id: str, user: AdminUserDep) -> None:
    """Cancel any user's queued/running job."""
    ok = await get_job_runtime().cancel(job_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Job cannot be cancelled - already finished or unknown.",
        )
    log.info("admin_job_cancel", admin_id=user.id, job_id=job_id)


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, user: AdminUserDep) -> dict[str, str]:
    """Re-enqueue any user's failed job; returns the new job id."""
    new_id = await get_job_runtime().retry(job_id)
    if new_id is None:
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried.")
    log.info("admin_job_retry", admin_id=user.id, job_id=job_id, new_id=new_id)
    return {"job_id": new_id}


class CancelAllBody(BaseModel):
    # None → every user's active jobs; set → scope to one user.
    user_id: str | None = None


@router.post("/cancel-all")
async def cancel_all(
    body: CancelAllBody, user: AdminUserDep, session: SessionDep
) -> dict[str, int]:
    """Cancel every active (queued/running) job, optionally scoped to one user."""
    stmt = select(Job.id).where(Job.status.in_(_ACTIVE))
    if body.user_id:
        stmt = stmt.where(Job.user_id == body.user_id)
    ids = (await session.execute(stmt)).scalars().all()

    runtime = get_job_runtime()
    cancelled = 0
    for job_id in ids:
        if await runtime.cancel(job_id):
            cancelled += 1
    log.info("admin_job_cancel_all", admin_id=user.id, scope=body.user_id, cancelled=cancelled)
    return {"cancelled": cancelled}


class QueueBody(BaseModel):
    user_id: str


@router.post("/queue/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_queue(body: QueueBody, user: AdminUserDep) -> None:
    """Pause a specific user's queue - other tenants keep flowing."""
    await get_job_runtime().pause_queue(body.user_id)
    log.info("admin_queue_pause", admin_id=user.id, target=body.user_id)


@router.post("/queue/resume", status_code=status.HTTP_204_NO_CONTENT)
async def resume_queue(body: QueueBody, user: AdminUserDep) -> None:
    await get_job_runtime().resume_queue(body.user_id)
    log.info("admin_queue_resume", admin_id=user.id, target=body.user_id)
