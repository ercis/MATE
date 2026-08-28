"""FastAPI entry point.

Starts the SQLite engine (PRAGMAs applied lazily on first connect), the
DuckDB connection pool, and the asyncio job runtime with the import handler
registered.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import shutil
import signal
import sys
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

# Process trees discovered from real-world logs can nest deep enough to
# exceed CPython's default 1000-frame recursion limit when FastAPI's
# jsonable_encoder walks the response dict. Raising it once at import time
# is cheap and avoids a class of RecursionError → 500 failures on the
# /process-tree routes.
sys.setrecursionlimit(10_000)

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import select

from mate.api import __version__
from mate.api.config import get_settings
from mate.api.db.engine import dispose_engine, get_sessionmaker
from mate.api.db.models import Job, ModuleInstall, WatchedFolder
from mate.api.duckdb.pool import get_duckdb_pool
from mate.api.events import EventBus, set_event_bus
from mate.api.ingest.dispatch import register_import_handler
from mate.api.ingest.staging import sweep_staging
from mate.api.ingest.watch import scan_watch
from mate.api.jobs.maintenance import prune_old_jobs
from mate.api.jobs.runtime import JobRuntime, load_persisted_concurrency, set_job_runtime
from mate.api.jobs.supervisor import get_child_supervisor
from mate.api.middleware import UsageTrackingMiddleware
from mate.api.modules import CapabilityRegistry, ModuleLoader, set_module_loader
from mate.api.modules.hot_reload import HotReload, sweep_stale_workdirs
from mate.api.modules.install_jobs import register_module_install_handlers
from mate.api.modules.maintenance import gc_orphaned_uploaded_modules
from mate.api.modules.processing import ModuleProcessingCoordinator, set_coordinator
from mate.api.routes import v1
from mate.api.routes.analytics import prune_expired, record_server_event
from mate.api.schemas.common import HealthResponse
from mate.api.services.analytics_objects import ObjectRef
from mate.api.services.usage_recorder import server_event_writer_loop
from mate.api.shutdown import install_signal_observer, mark_shutting_down
from mate.api.storage import get_storage_settings
from mate.api.storage.db_backup import backup_sync, db_backup_loop
from mate.api.storage.eviction import eviction_loop
from mate.api.storage.module_archive import restore_missing_modules_sync
from mate.api.system.metrics import ResourceSampler, set_resource_sampler

# On-demand thread-stack dump for diagnosing an event-loop wedge without killing the
# process first: `docker exec mate-api kill -USR1 1` writes every thread's real stack
# to stderr (captured by `docker compose logs`). Main-thread-only; suppressed elsewhere
# (e.g. a non-main-thread test import).
with suppress(ValueError):
    faulthandler.register(signal.SIGUSR1)

# Daily - re-evaluated every loop iteration against the current
# `analytics.config.retention_days` setting.
_RETENTION_INTERVAL_SECONDS = 24 * 60 * 60


async def _retention_loop() -> None:
    """Periodically prune expired analytics rows + terminal jobs.

    Both no-op when their retention window is unset (forever); a deployment that
    keeps everything pays nothing. Errors are swallowed so a transient DB hiccup
    never tears down the loop.
    """
    log = structlog.get_logger("retention")
    sm = get_sessionmaker()
    job_retention_days = get_settings().job_retention_days
    while True:
        try:
            await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)
            async with sm() as session:
                pruned = await prune_expired(session)
                if pruned:
                    log.info("retention.analytics_pruned", events=pruned)
            if job_retention_days > 0:
                async with sm() as session:
                    removed = await prune_old_jobs(session, job_retention_days)
                    if removed:
                        log.info("retention.jobs_pruned", jobs=removed)
            # Uploads staged by the import wizard and never confirmed (the user
            # closed the tab on the mapping step) are pure garbage after the TTL.
            swept = await asyncio.to_thread(sweep_staging)
            if swept:
                log.info("retention.staging_swept", directories=swept)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("retention.failed", error=str(exc))


async def _job_event_recorder_loop(bus: EventBus) -> None:
    """Mirror job outcomes into the unified analytics stream as ``job`` events.

    Subscribes to the platform's terminal ``job.*`` topics and records one
    server-side analytics event per finished job, carrying the job type,
    status, module, and real runtime duration (from the ``jobs`` row). This is
    why the job runtime itself needs no tracking code. Gated per-user by
    ``record_server_event``; failures are swallowed so the bus keeps draining.
    """
    log = structlog.get_logger("usage.jobs")
    sm = get_sessionmaker()
    async with bus.subscribe(("job.completed", "job.failed", "job.cancelled")) as stream:
        async for envelope in stream:
            try:
                payload = envelope.payload
                job_id = payload.get("id")
                user_id = payload.get("user_id")
                if not job_id or not user_id:
                    continue
                async with sm() as session:
                    job = await session.get(Job, job_id)
                    if job is None:
                        continue
                    duration_ms: int | None = None
                    if job.started_at and job.finished_at:
                        duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)
                    job_objects = [ObjectRef(f"job:{job.id}", "job", "resource")]
                    if job.module_id:
                        job_objects.append(
                            ObjectRef(f"module:{job.module_id}", "module", "resource")
                        )
                    await record_server_event(
                        session,
                        user_id=user_id,
                        event_type="job",
                        event_name=job.type,
                        duration_ms=duration_ms,
                        properties={
                            "job_id": job.id,
                            "status": job.status,
                            "module_id": job.module_id,
                            "error": (job.error or None) and job.error[:240],
                        },
                        objects=job_objects,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("usage.jobs.record_failed", error=str(exc))


async def _module_processing_loop(bus: EventBus, coordinator: ModuleProcessingCoordinator) -> None:
    """Un-gate a ``processing`` log once its module precompute jobs finish.

    Subscribes to the terminal ``job.*`` topics and, for each finished job that
    is a child of an ``event_log.import`` job, re-checks whether the bound log's
    expected modules have all reached a terminal state - flipping it to ``ready``
    when they have. Failures are swallowed so the bus keeps draining (a missed
    tick is recovered by the boot reconcile on the next restart).
    """
    proc_log = structlog.get_logger("modules.processing")
    async with bus.subscribe(("job.completed", "job.failed", "job.cancelled")) as stream:
        async for envelope in stream:
            try:
                await coordinator.on_terminal_job(envelope.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                proc_log.warning("modules.processing.terminal_job_failed", error=str(exc))


# How often the watched-folder poller wakes; per-watch cadence is enforced on
# top of this base tick. `continuous` mode effectively scans every tick.
_WATCH_POLL_TICK_SECONDS = 30
_WATCH_CONTINUOUS_INTERVAL_SECONDS = 60


def _watch_due(watch: WatchedFolder, now: datetime) -> bool:
    if watch.last_scanned_at is None:
        return True
    if watch.mode == "continuous":
        interval = _WATCH_CONTINUOUS_INTERVAL_SECONDS
    elif watch.mode == "interval":
        interval = watch.interval_seconds or 0
        if interval <= 0:
            return False
    else:  # manual - never auto-scanned
        return False
    return watch.last_scanned_at + timedelta(seconds=interval) <= now


async def _watched_folder_poll_loop(runtime: JobRuntime) -> None:
    """Periodically scan active watched folders and import new/changed files.

    Each tick selects non-deleted, active watches in interval/continuous mode
    whose cadence is due and runs the shared `scan_watch` per watch. Failures are
    swallowed (and recorded on the watch row by `scan_watch`) so the loop never
    dies on a transient source error.
    """
    poll_log = structlog.get_logger("ingest.watch")
    sm = get_sessionmaker()
    while True:
        try:
            await asyncio.sleep(_WATCH_POLL_TICK_SECONDS)
            now = datetime.now(UTC).replace(tzinfo=None)
            async with sm() as session:
                candidates = (
                    (
                        await session.execute(
                            select(WatchedFolder).where(
                                WatchedFolder.deleted_at.is_(None),
                                WatchedFolder.status == "active",
                                WatchedFolder.mode.in_(("interval", "continuous")),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                due = [w for w in candidates if _watch_due(w, now)]
            for watch in due:
                try:
                    async with sm() as session:
                        fresh = await session.get(WatchedFolder, watch.id)
                        if fresh is None or fresh.deleted_at is not None:
                            continue
                        await scan_watch(fresh, session=session, runtime=runtime)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    poll_log.warning("watch.poll_scan_failed", watch_id=watch.id, error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            poll_log.warning("watch.poll_failed", error=str(exc))


def _configure_logging(level: str) -> None:
    # Local import: this module's top-level imports already trip E402 (they sit
    # after ``sys.setrecursionlimit`` above), and the renderer is only needed here.
    from mate.api.system.log_buffer import ring_buffer_renderer

    logging.basicConfig(level=level.upper())
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # Renders JSON to stdout exactly like ``JSONRenderer()`` and also tees
            # each line into a bounded ring buffer for the diagnostics log tail.
            ring_buffer_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


def _purge_legacy_storage_once(settings) -> None:
    """One-shot wipe of pre-multi-user on-disk layout.

    The fresh-start migration in Alembic drops the rows for ``process_logs``
    etc. but leaves the parquet directories behind. Without this, the next
    boot would still have orphan ``data/event_logs/<id>/`` directories. We
    write a sentinel after the wipe so subsequent boots skip the check.
    """
    sentinel = settings.data_dir / ".multi_user_migrated"
    if sentinel.exists():
        return
    for legacy in (settings.data_dir / "event_logs", settings.data_dir / "module_results"):
        if legacy.exists():
            shutil.rmtree(legacy, ignore_errors=True)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("multi-user storage layout active\n")


# Per-phase grace for the lifespan teardown. Both wrapped phases are already
# internally time-boxed; this is insurance so a *future* unbounded await in
# shutdown can't wedge the `finally` - uvicorn waits on it, and a wedged finally
# is what stalled `--reload` restarts and `docker stop` (see `JobRuntime.stop`).
_SHUTDOWN_PHASE_TIMEOUT_S = 6.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    _purge_legacy_storage_once(settings)
    _configure_logging(settings.log_level)

    bus = EventBus()
    set_event_bus(bus)

    # Re-apply an admin's persisted worker concurrency over the env default so a
    # live change at Settings → General → Jobs survives a restart. Set before
    # start() so the worker pool spawns at the right size from the first boot.
    persisted_concurrency = await load_persisted_concurrency()
    if persisted_concurrency is not None:
        settings.worker_concurrency = persisted_concurrency

    runtime = JobRuntime(settings, bus=bus)
    register_import_handler(runtime)
    set_job_runtime(runtime)
    await runtime.start()

    registry = CapabilityRegistry()
    loader = ModuleLoader(
        modules_dir=settings.modules_dir.resolve(),
        uploaded_modules_dir=settings.uploaded_modules_dir.resolve(),
        bus=bus,
        runtime=runtime,
        registry=registry,
        api_app=app,
    )
    set_module_loader(loader)
    register_module_install_handlers(runtime, loader)

    # S3 mode: reclaim orphaned uploaded-module dirs (zero install rows), then
    # re-materialise any *owned* upload whose source is missing locally (fresh
    # VM). Both run before the loader discovers modules, so it sees the right set
    # and rebuilds venvs/bundles. Scoped to the install-row set so GC + restore
    # never fight over a dir.
    if get_storage_settings().is_s3:
        async with get_sessionmaker()() as session:
            rows = await session.execute(select(ModuleInstall.module_id).distinct())
            install_ids = {mid for (mid,) in rows.all()}
        up_dir = settings.uploaded_modules_dir.resolve()
        await asyncio.to_thread(gc_orphaned_uploaded_modules, up_dir, install_ids)
        await asyncio.to_thread(restore_missing_modules_sync, up_dir, install_ids)

    try:
        await loader.load_all()
    except Exception:
        # Last-resort net: a batch-aborting failure must not stop the platform
        # from booting - but it must be loud. Swallowing it silently once left
        # the whole module system dark with no modules and no log line. The
        # known per-module failure modes (invalid manifest, duplicate id,
        # unsatisfiable/cyclic requirement, install/import error) are logged +
        # skipped inside discovery and the loader; nothing routine lands here.
        structlog.get_logger("modules.loader").exception("modules.load_all_failed")

    # Holds a freshly imported log disabled (`status="processing"`) until every
    # subscribing module finishes precomputing against it. Wired after the loader
    # loads so its event-subscriber index is populated, and exposed on app state
    # + a module-level singleton so the ingest handler can freeze each log's
    # expected-module set at import time.
    coordinator = ModuleProcessingCoordinator(loader, bus, get_sessionmaker())
    app.state.module_processing_coordinator = coordinator
    set_coordinator(coordinator)
    # Re-derive completion for any log left `processing` by a previous process
    # (jobs that finished while the API was down won't re-emit their events).
    async with get_sessionmaker()() as session:
        await coordinator.reconcile_boot(session)

    # Re-enqueue precompute jobs a prior process left interrupted (its slow
    # modules were still running/queued at a `--reload` restart or crash). Runs
    # now that the loader has re-registered their handlers, and before the app
    # serves any new import, so a killed cv4cdd / complexity-over-time actually
    # reruns and writes its output instead of leaving an empty result cache the
    # panel reads as "nothing happened".
    await runtime.resume_interrupted_precompute()

    # Sweep any `ff-mod-*` temp dirs older than 24h that earlier crashes left
    # behind (the per-invocation cleanup in `_invoke_handler` handles the
    # happy path; this catches SIGKILL/restart leaks).
    sweep_stale_workdirs()

    # Dev-only watchdog so module edits hot-reload without a restart (§5.3 #7).
    hot_reload: HotReload | None = None
    if settings.env == "dev":
        hot_reload = HotReload(loader)
        hot_reload.start()

    # Touch the DuckDB pool so the first request doesn't pay the init cost.
    get_duckdb_pool()

    # Warm the storage-backend config so the sync hooks (and a possible S3
    # primary store) are live from the first request after a restart.
    get_storage_settings()

    retention_task = asyncio.create_task(_retention_loop())
    job_event_task = asyncio.create_task(_job_event_recorder_loop(bus))
    # Batched writer behind the all-requests UsageTrackingMiddleware - drafts
    # are queued in-memory on the request path and persisted here.
    usage_writer_task = asyncio.create_task(server_event_writer_loop())
    watch_poll_task = asyncio.create_task(_watched_folder_poll_loop(runtime))
    processing_task = asyncio.create_task(_module_processing_loop(bus, coordinator))
    # S3-mode local-cache reaper: bounds the working set so the bucket can be the
    # authoritative copy. No-op in local mode or with no budget set.
    eviction_task = asyncio.create_task(eviction_loop())
    # S3-mode metadata.db snapshot loop: keeps a restorable DB copy in the bucket
    # so losing the VM doesn't orphan its objects. No-op in local mode.
    db_backup_task = asyncio.create_task(db_backup_loop())

    # Live CPU/RAM sampler for Admin → System. Built after the loader so its first
    # breakdown can already see subprocess workers; manages its own asyncio task.
    sampler = ResourceSampler(settings, loader=loader, runtime=runtime)
    set_resource_sampler(sampler)
    await sampler.start()

    # Run the MCP streamable-HTTP session manager for the app's lifetime when
    # the server is mounted (a mounted sub-app's own lifespan never fires).
    mcp_cm = None
    if settings.mcp_enabled:
        from mate.api.mcp import mcp_session_manager

        mcp_cm = mcp_session_manager()
        await mcp_cm.__aenter__()

    # Chain onto uvicorn's own SIGINT/SIGTERM handlers (installed before this
    # lifespan ran) so long-lived SSE streams learn about shutdown at signal
    # time. Uvicorn drains connections BEFORE running this teardown, so a flag
    # flipped in the `finally` below arrives far too late: the drain would hit
    # `--timeout-graceful-shutdown`, force-cancel the open `/events` stream and
    # print a CancelledError ASGI traceback + a 500 on every `--reload` restart.
    restore_signals = install_signal_observer()

    try:
        yield
    finally:
        # Belt and braces for a shutdown that reaches the lifespan without a
        # signal (programmatic teardown, test harness): anything still streaming
        # gets one more chance to notice and close itself.
        mark_shutting_down()
        restore_signals()
        if mcp_cm is not None:
            try:
                await mcp_cm.__aexit__(None, None, None)
            except Exception:
                structlog.get_logger("api.shutdown").exception("shutdown.mcp_stop_failed")
        # Authoritative stop, first and unconditional: SIGKILL every child the
        # platform owns (offload children, subprocess + per-job workers) through
        # the one controller, so nothing survives shutdown regardless of how the
        # graceful per-subsystem teardown below fares. Idempotent with it. The
        # parent-death guard covers the SIGKILL case where this never runs.
        try:
            get_child_supervisor().kill_all()
        except Exception:
            structlog.get_logger("api.shutdown").exception("shutdown.kill_all_failed")
        for task in (
            retention_task,
            job_event_task,
            usage_writer_task,
            watch_poll_task,
            processing_task,
            eviction_task,
            db_backup_task,
        ):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if hot_reload is not None:
            hot_reload.stop()
        await sampler.stop()
        set_resource_sampler(None)
        set_coordinator(None)
        # Both teardowns are internally time-boxed (subprocess hosts fall back to
        # SIGKILL; runtime.stop drains are graced), but wrap them anyway: uvicorn
        # waits on this `finally`, so any future unbounded await here would wedge
        # restart/stop. On overrun, log and press on so the pool/engine still close.
        for label, coro in (
            ("unload_all", loader.unload_all()),
            ("runtime_stop", runtime.stop()),
        ):
            try:
                await asyncio.wait_for(coro, timeout=_SHUTDOWN_PHASE_TIMEOUT_S)
            except TimeoutError:
                structlog.get_logger("api.shutdown").warning("shutdown.phase_timeout", phase=label)
            except Exception:
                structlog.get_logger("api.shutdown").exception("shutdown.phase_failed", phase=label)
        set_module_loader(None)
        set_job_runtime(None)
        set_event_bus(None)
        # Final metadata.db snapshot to S3 (no-op in local mode) before the engine
        # closes, so a clean shutdown leaves the bucket fully current.
        try:
            await asyncio.to_thread(backup_sync)
        except Exception:
            structlog.get_logger("api.shutdown").warning("shutdown.db_backup_failed", exc_info=True)
        get_duckdb_pool().close_all()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.demo_mode and settings.mcp_enabled:
        # The demo bypass is refused on /mcp (see mcp/auth.resolve_mcp_principal),
        # but this combination must never reach production - flag it loudly.
        structlog.get_logger("api.startup").warning(
            "mcp.demo_mode_combo",
            detail="DEMO_MODE and MCP_ENABLED are both on; never do this in production.",
        )
    app = FastAPI(
        title="Mate API",
        version=__version__,
        description="Backend for the Mate process analysis platform.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    # Times a curated allowlist of business operations and records them as
    # server-side analytics events (transparent to streaming responses).
    app.add_middleware(UsageTrackingMiddleware)
    # Compress JSON/JS bodies (variants, events pages, module bundles): the VM
    # proxy does not compress for us. Starlette's GZipMiddleware skips
    # `text/event-stream` by default, so the SSE endpoints (/events,
    # /jobs/{id}/stream, AI chat) keep flushing live and unbuffered.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.include_router(v1)

    # Read-only MCP server for external consumers (opt-in). Mounted as a raw
    # ASGI sub-app (the module loader's @route mechanism can't host one); its
    # streamable-HTTP session manager is run from the lifespan below.
    if settings.mcp_enabled:
        from mate.api.mcp import build_mcp_asgi_app
        from mate.api.mcp.oauth import router as mcp_oauth_router

        app.mount("/mcp", build_mcp_asgi_app())
        # OAuth protected-resource metadata at the root (RFC 9728 well-known path).
        app.include_router(mcp_oauth_router)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    return app


app = create_app()
