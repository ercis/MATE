"""System-level diagnostics - disk usage, version, copy-diagnostics blob.

Backs the *Settings → General → Data & storage* gauge and the *Settings →
About → Copy diagnostics* button (§7.6.1, §7.6.3).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import psutil
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mate.api import __version__
from mate.api.auth import ADMIN_ROLE, AdminUserDep, CurrentUserDep
from mate.api.config import get_settings
from mate.api.jobs.runtime import (
    MAX_WORKERS,
    MIN_WORKERS,
    get_job_runtime,
    save_persisted_concurrency,
)
from mate.api.modules import get_module_loader
from mate.api.system.log_buffer import LOG_RING_MAXLEN, recent_log_lines
from mate.api.system.metrics import SystemResourcesOut, get_resource_sampler

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/system", tags=["system"])


def _dir_size_bytes(path: Path, *, max_entries: int = 100_000) -> int:
    """Recursive byte total under `path`. Bounded scan so a pathological
    deeply-nested module folder can't lock the request indefinitely.
    """
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


@router.get("/storage")
async def storage(user: CurrentUserDep) -> dict[str, Any]:
    """Disk usage breakdown for the platform's bind-mounted data + modules
    directories, plus filesystem total/free so the frontend can render a
    gauge. All values in bytes.
    """
    settings = get_settings()
    data_dir = settings.data_dir.resolve()
    modules_dir = settings.modules_dir.resolve()
    try:
        usage = shutil.disk_usage(data_dir if data_dir.exists() else data_dir.parent)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"shutil.disk_usage failed: {exc}") from exc
    # The recursive size walk can touch tens of thousands of files (uv caches,
    # module venvs, parquet) on a slow bind-mount. Run it off the event loop so
    # it can't stall every other request (including /health) for minutes.
    by_dir: dict[str, int] = {}
    for label, p in (("data", data_dir), ("modules", modules_dir)):
        by_dir[label] = await asyncio.to_thread(_dir_size_bytes, p) if p.exists() else 0
    return {
        "fs_total": usage.total,
        "fs_used": usage.used,
        "fs_free": usage.free,
        "by_dir": by_dir,
        "data_dir": str(data_dir),
        "modules_dir": str(modules_dir),
    }


class JobsConfigOut(BaseModel):
    worker_concurrency: int
    min: int = MIN_WORKERS
    max: int = MAX_WORKERS
    # Whether the caller may change it. The slider renders read-only otherwise.
    is_admin: bool


class JobsConfigIn(BaseModel):
    worker_concurrency: int = Field(ge=MIN_WORKERS, le=MAX_WORKERS)


@router.get("/jobs", response_model=JobsConfigOut)
async def get_jobs_config(user: CurrentUserDep) -> JobsConfigOut:
    """Live job-runtime worker concurrency + bounds (Settings → General → Jobs).

    Readable by any user so the slider shows the current value; only admins can
    change it (see the PUT). The ``is_admin`` flag lets the page render a
    read-only state rather than a hard 403 (mirrors ``admin.export_info``).
    """
    return JobsConfigOut(
        worker_concurrency=get_job_runtime().concurrency(),
        is_admin=ADMIN_ROLE in user.roles,
    )


@router.put("/jobs", response_model=JobsConfigOut)
async def put_jobs_config(body: JobsConfigIn, user: AdminUserDep) -> JobsConfigOut:
    """Resize the worker pool live and persist the value (admin only).

    The change takes effect immediately (graceful - running jobs are never
    interrupted) and survives a restart via ``system_settings``.
    """
    applied = await get_job_runtime().set_concurrency(body.worker_concurrency)
    await save_persisted_concurrency(applied)
    log.info("system_jobs_concurrency_set", admin_id=user.id, workers=applied)
    return JobsConfigOut(worker_concurrency=applied, is_admin=True)


class DiagnosticsSystemOut(BaseModel):
    """Host + interpreter facts. All non-secret, safe to paste into a thread."""

    platform: str
    machine: str
    processor: str
    python_version: str
    python_implementation: str
    cpu_count_logical: int | None
    cpu_count_physical: int | None
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    process_uptime_seconds: float | None
    process_started_at: str | None
    process_rss_bytes: int | None


class DiagnosticsVersionsOut(BaseModel):
    """Versions of the platform's load-bearing native/data libraries."""

    duckdb: str | None
    pyarrow: str | None
    pandas: str | None
    pm4py: str | None
    sqlalchemy: str | None
    fastapi: str | None
    sqlite: str


class DiagnosticsLogsOut(BaseModel):
    """Recent-log tail. Populated only for admins (see the handler).

    The platform has no on-disk log file - lines come from a bounded in-memory
    ring buffer that captures structlog output since the last (re)start.
    """

    available: bool
    note: str | None = None
    source: str = "in_memory_ring"
    capacity: int = LOG_RING_MAXLEN
    line_count: int = 0
    byte_count: int = 0
    truncated: bool = False
    lines: list[str] = Field(default_factory=list)


class DiagnosticsOut(BaseModel):
    platform_version: str
    python: str
    is_admin: bool
    system: DiagnosticsSystemOut
    versions: DiagnosticsVersionsOut
    settings: dict[str, Any]
    modules: list[dict[str, Any]]
    module_count: int
    logs: DiagnosticsLogsOut


def _pkg_version(name: str) -> str | None:
    """Distribution version from installed metadata (cheap, no import), or None."""
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return None


def _diagnostics_system() -> DiagnosticsSystemOut:
    """Host/interpreter snapshot. psutil is already a platform dependency."""
    uptime: float | None = None
    started: str | None = None
    rss: int | None = None
    try:
        proc = psutil.Process(os.getpid())
        create_time = proc.create_time()
        uptime = round(max(0.0, time.time() - create_time), 1)
        started = datetime.fromtimestamp(create_time, tz=UTC).isoformat()
        rss = int(proc.memory_info().rss)
    except (psutil.Error, OSError):
        pass

    mem_total: int | None = None
    mem_available: int | None = None
    try:
        vm = psutil.virtual_memory()
        mem_total, mem_available = int(vm.total), int(vm.available)
    except (psutil.Error, OSError):
        pass

    try:
        cores_physical = psutil.cpu_count(logical=False)
    except (psutil.Error, OSError):
        cores_physical = None

    return DiagnosticsSystemOut(
        platform=platform.platform(),
        machine=platform.machine(),
        processor=platform.processor(),
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        cpu_count_logical=os.cpu_count(),
        cpu_count_physical=cores_physical,
        memory_total_bytes=mem_total,
        memory_available_bytes=mem_available,
        process_uptime_seconds=uptime,
        process_started_at=started,
        process_rss_bytes=rss,
    )


def _diagnostics_versions() -> DiagnosticsVersionsOut:
    return DiagnosticsVersionsOut(
        duckdb=_pkg_version("duckdb"),
        pyarrow=_pkg_version("pyarrow"),
        pandas=_pkg_version("pandas"),
        pm4py=_pkg_version("pm4py"),
        sqlalchemy=_pkg_version("SQLAlchemy"),
        fastapi=_pkg_version("fastapi"),
        sqlite=sqlite3.sqlite_version,
    )


@router.get("/diagnostics", response_model=DiagnosticsOut)
async def diagnostics(user: CurrentUserDep) -> DiagnosticsOut:
    """Single JSON blob for the *Copy diagnostics* button. Everything a
    support thread might ask for, in one round-trip.

    System info + versions are returned to any authenticated user. The raw
    **log tail is admin-only** (``ADMIN_ROLE``): application logs interleave
    every user's activity, so serving it to non-admins would leak cross-tenant
    data. Non-admins get the ``logs`` envelope with ``available=false`` and an
    explanatory note instead of a hard 403, so the page still renders.
    """
    settings = get_settings()
    try:
        loader = get_module_loader()
        manifests = [
            {
                "id": m.id,
                "version": m.version,
                "category": m.category,
                "isolation": m.dependencies.python.isolation,
            }
            for m in loader.manifests()
        ]
    except HTTPException:
        manifests = []

    is_admin = ADMIN_ROLE in user.roles
    if is_admin:
        tail = recent_log_lines()
        logs = DiagnosticsLogsOut(
            available=True,
            note=(
                "Most recent in-memory log lines. The platform has no on-disk log "
                "file - structlog renders JSON to stdout; this is a bounded ring "
                "buffer captured since the last (re)start."
            ),
            capacity=tail.capacity,
            line_count=len(tail.lines),
            byte_count=tail.byte_count,
            truncated=tail.truncated,
            lines=tail.lines,
        )
    else:
        logs = DiagnosticsLogsOut(
            available=False,
            note=(
                "Restricted to admin accounts: application logs may contain other users' activity."
            ),
        )

    return DiagnosticsOut(
        platform_version=__version__,
        python=sys.version,
        is_admin=is_admin,
        system=_diagnostics_system(),
        versions=_diagnostics_versions(),
        settings={
            "env": settings.env,
            "log_level": settings.log_level,
            "worker_concurrency": settings.worker_concurrency,
            "data_dir": str(settings.data_dir),
            "modules_dir": str(settings.modules_dir),
        },
        modules=manifests,
        module_count=len(manifests),
        logs=logs,
    )


@router.get("/resources", response_model=SystemResourcesOut)
async def system_resources(user: AdminUserDep) -> SystemResourcesOut:
    """Live host CPU/RAM + per-source breakdown for *Admin → System* (admin only).

    Reads the in-memory snapshot maintained by the background sampler - no psutil
    work happens in the request path.
    """
    return get_resource_sampler().snapshot()
