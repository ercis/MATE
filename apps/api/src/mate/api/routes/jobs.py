"""/api/v1/jobs/* - full surface (§7.9.5).

GET  /jobs                       - paginated/filtered list (drives the drawer)
GET  /jobs/{id}                  - detail / poll
POST /jobs/{id}/cancel           - cooperative cancel
POST /jobs/{id}/retry            - re-enqueue a failed job; returns new id
POST /jobs/queue/pause           - stop pulling new jobs
POST /jobs/queue/resume          - resume
GET  /events                     - topic-filtered SSE stream (`?topic=job.*`)
GET  /jobs/{id}/stream           - high-frequency SSE progress for a single job
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from mate.api.auth import (
    CurrentUserDep,
    get_owned_job,
)
from mate.api.db.models import Job
from mate.api.db.session import SessionDep
from mate.api.events import get_event_bus
from mate.api.jobs.runtime import get_job_runtime
from mate.api.schemas.jobs import JobDetail

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobDetail])
async def list_jobs(
    session: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[JobDetail]:
    stmt = select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    if type_filter:
        stmt = stmt.where(Job.type == type_filter)
    if since:
        stmt = stmt.where(Job.created_at >= since)
    rows = (await session.execute(stmt)).scalars().all()
    return [JobDetail.model_validate(r) for r in rows]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: str, session: SessionDep, user: CurrentUserDep) -> JobDetail:
    row = await get_owned_job(session, job_id, user.id)
    return JobDetail.model_validate(row)


@router.post("/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(job_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    await get_owned_job(session, job_id, user.id)
    runtime = get_job_runtime()
    ok = await runtime.cancel(job_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Job cannot be cancelled - already finished or unknown.",
        )


@router.post("/cancel-all")
async def cancel_all_jobs(session: SessionDep, user: CurrentUserDep) -> dict[str, int]:
    """Cancel every queued and running job owned by the user."""
    ids = (
        (
            await session.execute(
                select(Job.id).where(
                    Job.user_id == user.id,
                    Job.status.in_(("queued", "running")),
                )
            )
        )
        .scalars()
        .all()
    )
    runtime = get_job_runtime()
    cancelled = 0
    for job_id in ids:
        if await runtime.cancel(job_id):
            cancelled += 1
    return {"cancelled": cancelled}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, session: SessionDep, user: CurrentUserDep) -> dict[str, str]:
    await get_owned_job(session, job_id, user.id)
    runtime = get_job_runtime()
    new_id = await runtime.retry(job_id)
    if new_id is None:
        raise HTTPException(
            status_code=409,
            detail="Only failed jobs can be retried.",
        )
    return {"job_id": new_id}


@router.post("/queue/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_queue(user: CurrentUserDep) -> None:
    """Pause only the caller's jobs - other users' queues keep flowing."""
    await get_job_runtime().pause_queue(user.id)


@router.post("/queue/resume", status_code=status.HTTP_204_NO_CONTENT)
async def resume_queue(user: CurrentUserDep) -> None:
    await get_job_runtime().resume_queue(user.id)


# -- Streaming (SSE) ---------------------------------------------------------
#
# Server-Sent Events, not WebSocket - see ``events_sse.py`` for why the prod
# proxy chain forces this. Auth is the standard ``Authorization: Bearer``
# header via ``CurrentUserDep``.

# Idle keep-alive cadence; matches ``events_sse._HEARTBEAT_S``.
_HEARTBEAT_S = 15.0
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sse(envelope: dict[str, Any]) -> str:
    return f"data: {json.dumps(envelope, default=_json_default)}\n\n"


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, session: SessionDep, user: CurrentUserDep) -> StreamingResponse:
    """High-frequency per-job progress (toast inline bar + drawer focused row).

    Subscribes to `job.progress` / `job.started` / `job.completed` / etc. and
    filters by id. Spec §7.9.5: we send an initial `job.snapshot` of the row so
    a late subscriber (or a reconnect) paints immediately without missing the
    early lifecycle events.
    """
    bus = get_event_bus()

    row = await session.get(Job, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    snapshot = {
        "topic": "job.snapshot",
        "payload": JobDetail.model_validate(row).model_dump(mode="json"),
    }

    async def _gen() -> AsyncIterator[str]:
        yield _sse(snapshot)
        async with bus.subscribe(["job.*"]) as stream:
            while True:
                try:
                    env = await asyncio.wait_for(anext(stream), _HEARTBEAT_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                except StopAsyncIteration:
                    return
                payload = env.payload
                if payload.get("id") != job_id:
                    continue
                if payload.get("user_id") not in (None, user.id):
                    continue
                yield _sse(env.to_json())
                if env.topic in {"job.completed", "job.failed", "job.cancelled"}:
                    # Terminal event - end the stream cleanly.
                    return

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
