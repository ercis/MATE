"""Jobs toolset: list/get/wait/cancel/retry background jobs + queue control.

``wait_for_job`` is the agent-facing long poll: it blocks on the platform
event bus (bounded well under the tool timeout) and returns the terminal row -
or the current row with ``timed_out: true``, never an error, so an agent can
simply call it again to keep waiting. Control tools mirror the REST routes in
:mod:`mate.api.routes.jobs`; ownership is 404-on-foreign like everywhere else.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import get_owned_job
from mate.api.config import get_settings
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Job
from mate.api.events import get_event_bus
from mate.api.jobs.runtime import get_job_runtime
from mate.api.mcp.core import MCPContext, authz, confirm_preview, guarded
from mate.api.mcp.errors import CODE_CONFLICT, from_http_exception, tool_error
from mate.api.mcp.pagination import clamp_limit, decode_cursor, page_envelope
from mate.api.mcp.registry import mcp_tool
from mate.api.mcp.scopes import SCOPE_JOBS_CONTROL, SCOPE_JOBS_READ
from mate.api.schemas.jobs import JobDetail

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TERMINAL_TOPICS = frozenset({"job.completed", "job.failed", "job.cancelled"})
# Idle-tick cadence inside wait_for_job. The bus queue is bounded (oldest
# dropped), so every idle tick re-reads the row as a fallback - a dropped
# terminal event must not strand the wait until its deadline.
_WAIT_IDLE_TICK_S = 2.0
# Headroom under the hard tool timeout so wait_for_job returns its timed_out
# payload instead of tripping ``guarded``'s [timeout] error.
_WAIT_TIMEOUT_HEADROOM_S = 5.0


def _job_detail(row: Job) -> dict[str, Any]:
    """The exact shape the REST route serves (``payload_json`` incl. the
    import job's ``precompute_plan`` rides along)."""
    return JobDetail.model_validate(row).model_dump(mode="json")


async def _ensure_owned_job(session: AsyncSession, job_id: str, user_id: str) -> Job:
    """Ownership gate, translated to a tool error (404 for missing AND foreign)."""
    try:
        return await get_owned_job(session, job_id, user_id)
    except HTTPException as exc:
        raise from_http_exception(exc) from exc


@mcp_tool(toolset="jobs", idempotent=True)
async def list_jobs(
    ctx: MCPContext,
    status: str | None = None,
    type: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List your background jobs, newest first.

    ``status`` filters by queued|running|paused|completed|failed|cancelled;
    ``type`` by job type (e.g. ``event_log.import``). ``cursor``/``limit``
    paginate (pass back ``next_cursor``). Each item is the full job detail
    including progress, error and ``payload_json``.
    """
    p = await authz(ctx, SCOPE_JOBS_READ)
    offset = decode_cursor(cursor)
    size = clamp_limit(limit)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            conds = [Job.user_id == p.user.id]
            if status:
                conds.append(Job.status == status)
            if type:
                conds.append(Job.type == type)
            total = (
                await session.execute(select(func.count()).select_from(Job).where(*conds))
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(Job)
                        .where(*conds)
                        .order_by(desc(Job.created_at))
                        .offset(offset)
                        .limit(size)
                    )
                )
                .scalars()
                .all()
            )
            items = [_job_detail(r) for r in rows]
        return page_envelope(items, offset=offset, limit=size, total=total)

    return await guarded(p, "list_jobs", {"status": status or "", "type": type or ""}, _impl())


@mcp_tool(toolset="jobs", idempotent=True)
async def get_job(ctx: MCPContext, job_id: str) -> dict[str, Any]:
    """Get one job's status/progress/error detail (poll target for long operations)."""
    p = await authz(ctx, SCOPE_JOBS_READ)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned_job(session, job_id, p.user.id)
            return _job_detail(row)

    return await guarded(p, "get_job", {"job_id": job_id}, _impl())


@mcp_tool(toolset="jobs", idempotent=True)
async def wait_for_job(
    ctx: MCPContext, job_id: str, timeout_seconds: float = 25.0
) -> dict[str, Any]:
    """Block until a job reaches a terminal state (completed|failed|cancelled).

    Returns the fresh job detail plus ``timed_out``. Hitting the deadline is
    NOT an error: you get the current row with ``timed_out: true`` - call
    again to keep waiting. The wait is bounded under the server's tool timeout
    regardless of ``timeout_seconds``.
    """
    p = await authz(ctx, SCOPE_JOBS_READ)

    async def _read_row() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await _ensure_owned_job(session, job_id, p.user.id)
            return _job_detail(row)

    async def _impl() -> dict[str, Any]:
        budget = max(
            1.0,
            min(
                float(timeout_seconds),
                get_settings().mcp_tool_timeout_seconds - _WAIT_TIMEOUT_HEADROOM_S,
            ),
        )
        # Subscribe BEFORE the initial row read: a job going terminal between
        # the read and the subscribe would emit before we listen, and we'd
        # idle to the deadline. Subscribing first closes that race - either
        # the initial read already sees the terminal row, or the event lands
        # in our queue.
        async with get_event_bus().subscribe(["job.*"]) as stream:
            detail = await _read_row()
            if detail["status"] in _TERMINAL_STATUSES:
                return {**detail, "timed_out": False}

            loop = asyncio.get_event_loop()
            deadline = loop.time() + budget
            # One pending pull kept alive across idle ticks (the pattern from
            # routes/jobs.stream_job): `asyncio.wait(timeout=...)` leaves the
            # task pending on timeout. NEVER `asyncio.wait_for(anext(...))`
            # and never cancel the pull on idle - cancelling the anext task
            # closes the bus generator (documented platform bug).
            nxt: asyncio.Task[Any] = asyncio.ensure_future(anext(stream))
            try:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        detail = await _read_row()
                        timed_out = detail["status"] not in _TERMINAL_STATUSES
                        return {**detail, "timed_out": timed_out}
                    done, _ = await asyncio.wait({nxt}, timeout=min(_WAIT_IDLE_TICK_S, remaining))
                    if not done:
                        # Idle tick: fall back to the row in case our terminal
                        # event was dropped from the bounded bus queue.
                        detail = await _read_row()
                        if detail["status"] in _TERMINAL_STATUSES:
                            return {**detail, "timed_out": False}
                        continue
                    try:
                        env = nxt.result()
                    except StopAsyncIteration:
                        detail = await _read_row()
                        timed_out = detail["status"] not in _TERMINAL_STATUSES
                        return {**detail, "timed_out": timed_out}
                    nxt = asyncio.ensure_future(anext(stream))
                    payload = env.payload
                    if not env.topic.startswith("job."):
                        continue
                    if payload.get("id") != job_id:
                        continue
                    if payload.get("user_id") not in (None, p.user.id):
                        continue
                    if env.topic not in _TERMINAL_TOPICS:
                        continue
                    # The runtime commits the terminal row before publishing,
                    # so a re-read here returns the fresh terminal state.
                    detail = await _read_row()
                    if detail["status"] in _TERMINAL_STATUSES:
                        return {**detail, "timed_out": False}
            finally:
                nxt.cancel()
                with contextlib.suppress(BaseException):
                    await nxt

    # gate=False: a pure-await wait must never pin a concurrency slot while idle.
    return await guarded(p, "wait_for_job", {"job_id": job_id}, _impl(), gate=False)


@mcp_tool(toolset="jobs", write=True)
async def cancel_job(ctx: MCPContext, job_id: str) -> dict[str, Any]:
    """Cooperatively cancel one of your queued/running jobs."""
    p = await authz(ctx, SCOPE_JOBS_CONTROL, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ensure_owned_job(session, job_id, p.user.id)
        ok = await get_job_runtime().cancel(job_id)
        if not ok:
            raise tool_error(
                CODE_CONFLICT, "Job cannot be cancelled - already finished or unknown."
            )
        return {"job_id": job_id, "cancelled": True}

    return await guarded(p, "cancel_job", {"job_id": job_id}, _impl(), mutation=True)


@mcp_tool(toolset="jobs", write=True)
async def retry_job(ctx: MCPContext, job_id: str) -> dict[str, str]:
    """Re-enqueue a failed job with the same payload; returns the new job id."""
    p = await authz(ctx, SCOPE_JOBS_CONTROL, write=True)

    async def _impl() -> dict[str, str]:
        sm = get_sessionmaker()
        async with sm() as session:
            await _ensure_owned_job(session, job_id, p.user.id)
        new_id = await get_job_runtime().retry(job_id)
        if new_id is None:
            raise tool_error(CODE_CONFLICT, "Only failed jobs can be retried.")
        return {"job_id": new_id}

    return await guarded(p, "retry_job", {"job_id": job_id}, _impl(), mutation=True)


@mcp_tool(toolset="jobs", write=True, destructive=True)
async def cancel_all_jobs(ctx: MCPContext, confirm: bool = False) -> dict[str, Any]:
    """Cancel every one of your queued and running jobs.

    Without ``confirm`` this is a dry run returning the queued/running counts
    that would be cancelled; pass ``confirm=true`` to execute.
    """
    p = await authz(ctx, SCOPE_JOBS_CONTROL, write=True)

    async def _impl() -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            rows = (
                await session.execute(
                    select(Job.id, Job.status).where(
                        Job.user_id == p.user.id, Job.status.in_(("queued", "running"))
                    )
                )
            ).all()
        queued = sum(1 for _jid, s in rows if s == "queued")
        running = len(rows) - queued
        if not confirm:
            return confirm_preview(
                "cancel_all_jobs",
                {"queued": queued, "running": running, "total": len(rows)},
            )
        runtime = get_job_runtime()
        cancelled = 0
        for jid, _s in rows:
            if await runtime.cancel(jid):
                cancelled += 1
        return {"confirmed": True, "cancelled": cancelled}

    return await guarded(p, "cancel_all_jobs", {"confirm": confirm}, _impl(), mutation=True)


@mcp_tool(toolset="jobs", write=True, idempotent=True)
async def pause_my_queue(ctx: MCPContext) -> dict[str, Any]:
    """Pause your job queue (running jobs finish; queued ones park until resume).

    Per-user: other accounts' queues keep flowing.
    """
    p = await authz(ctx, SCOPE_JOBS_CONTROL, write=True)

    async def _impl() -> dict[str, Any]:
        await get_job_runtime().pause_queue(p.user.id)
        return {"paused": True}

    return await guarded(p, "pause_my_queue", {}, _impl(), mutation=True)


@mcp_tool(toolset="jobs", write=True, idempotent=True)
async def resume_my_queue(ctx: MCPContext) -> dict[str, Any]:
    """Resume your paused job queue (parked jobs are re-enqueued)."""
    p = await authz(ctx, SCOPE_JOBS_CONTROL, write=True)

    async def _impl() -> dict[str, Any]:
        await get_job_runtime().resume_queue(p.user.id)
        return {"paused": False}

    return await guarded(p, "resume_my_queue", {}, _impl(), mutation=True)
