"""Format detection + the import job handler.

The handler is registered against the `JobRuntime` at app startup. Its payload
shape (set by the route in `routes/event_logs.py`) is::

    {
        "log_id":        str,            # destination directory + DB row id
        "source_format": str,
        "original_path": str,            # the staged upload at data/event_logs/<id>/original.<ext>
        "csv_mapping":   dict | None,    # serialised CsvColumnMapping
    }

On success `events.parquet` / `cases.parquet` / `meta.json` are written and the
row in `process_logs` is moved to status='ready' - or 'processing' when an
installed module subscribes to the import topic, in which case
`mate.api.modules.processing` un-gates the log to 'ready' once those modules
finish precomputing. On failure the status is flipped to 'failed' with `error`
set.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import EventLog, Job
from mate.api.ingest.aggregation import compute_cases
from mate.api.ingest.compression import decompressed
from mate.api.ingest.csv_parser import parse_csv
from mate.api.ingest.json_parser import parse_json
from mate.api.ingest.mapping import (
    apply_roles,
    dedupe_case_insensitive_columns,
    resolve_roles,
)
from mate.api.ingest.ocel import OcelParseResult, parse_ocel
from mate.api.ingest.parquet_coerce import (
    coerce_object_columns,
    normalize_timestamps,
    to_datetime_robust,
)
from mate.api.ingest.storage import LogPaths, log_paths
from mate.api.ingest.xes import parse_xes
from mate.api.ingest.xml_parser import parse_xml
from mate.api.jobs.runtime import JobHandle, JobRuntime
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.event_logs import (
    CsvColumnMapping,
    JsonColumnMapping,
    XmlColumnMapping,
)
from mate.api.storage import sync as storage_sync

log = structlog.get_logger(__name__)

IMPORT_JOB_TYPE = "event_log.import"


class IngestStats(dict[str, Any]):
    pass


def _parse_case_centric(
    source_format: str,
    original_path: Path,
    csv_mapping_data: dict[str, Any] | None,
    xml_mapping_data: dict[str, Any] | None,
    json_mapping_data: dict[str, Any] | None,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    """Parse the staged upload into rows (sync; runs in a worker thread).

    The staged original may be compressed (gz / bz2 / xz / zip, possibly
    nested) - `decompressed` magic-sniffs the bytes and hands the parser a
    plain temp copy that lives only for the duration of the parse.

    ``on_progress`` is called with the running row count by the streaming
    parsers (see `_progress_bridge`). CSV/JSON parse in one shot, so they stay
    silent and the job's "parsing" stage remains indeterminate for them.
    """
    with decompressed(original_path) as src:
        if source_format in {"xes", "xes.gz"}:
            rows, detected = parse_xes(src, on_progress=on_progress)
            return rows, detected, None
        if source_format == "csv":
            mapping = (
                CsvColumnMapping.model_validate(csv_mapping_data) if csv_mapping_data else None
            )
            rows, detected, used = parse_csv(src, mapping)
            return rows, detected, used.model_dump()
        if source_format == "xml":
            xml_mapping = (
                XmlColumnMapping.model_validate(xml_mapping_data) if xml_mapping_data else None
            )
            rows, detected, used_xml = parse_xml(src, xml_mapping, on_progress=on_progress)
            return rows, detected, used_xml.model_dump()
        if source_format == "json":
            json_mapping = (
                JsonColumnMapping.model_validate(json_mapping_data) if json_mapping_data else None
            )
            rows, detected, used_json = parse_json(src, json_mapping)
            return rows, detected, used_json.model_dump()
    raise ValueError(f"Source format {source_format!r} is not supported in v1.")


# Parsers tick every 1000 rows, which is far too chatty for the bus on a large
# log. Rate-limit to one event per interval; the DB write throttles itself via
# `settings.progress_persist_every`.
_PARSE_PROGRESS_INTERVAL_SECONDS = 0.5


def _progress_bridge(handle: JobHandle) -> Callable[[int], None]:
    """Publish parser row counts from the parse thread onto the event loop.

    The parsers are sync and run inside `asyncio.to_thread`, so they cannot
    await `handle.progress`. This hands them a plain callable that schedules the
    coroutine back on the loop, throttled, and *never raises* into the parse:
    `handle.progress` polls the cancel token, and a cancel is already handled by
    the `raise_if_cancelled` right after the parse returns.
    """
    loop = asyncio.get_running_loop()
    state = {"last": 0.0}

    def tick(count: int) -> None:
        now = time.monotonic()
        if now - state["last"] < _PARSE_PROGRESS_INTERVAL_SECONDS:
            return
        state["last"] = now
        future = asyncio.run_coroutine_threadsafe(
            handle.progress(count, total=None, stage="parsing", message="Reading events"),
            loop,
        )
        # Retrieve the result so a cancelled/failed publish never surfaces as an
        # "exception was never retrieved" warning at GC time.
        future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

    return tick


def _parse_ocel_source(original_path: Path, flavor: str) -> OcelParseResult:
    """OCEL parse with the same transparent-decompression contract as above."""
    with decompressed(original_path) as src:
        return parse_ocel(src, flavor=flavor)


async def _import_handler(handle: JobHandle) -> None:
    payload = handle.payload
    log_id: str = payload["log_id"]
    source_format: str = payload["source_format"]
    original_path = Path(payload["original_path"])
    csv_mapping_data: dict[str, Any] | None = payload.get("csv_mapping")
    xml_mapping_data: dict[str, Any] | None = payload.get("xml_mapping")
    json_mapping_data: dict[str, Any] | None = payload.get("json_mapping")
    ocel_flavor: str = payload.get("ocel_flavor") or "json"

    paths = log_paths(log_id, handle.user_id)
    paths.ensure()

    log.info(
        "ingest.start",
        log_id=log_id,
        source_format=source_format,
        path=str(original_path),
    )

    await handle.progress(0, total=None, stage="parsing", message="Reading source file", force=True)

    # Object-centric (OCEL) logs take a fully separate path: no case_id, no root
    # events.parquet/cases.parquet, no variants - they persist under ocel/ only.
    if source_format == "ocel":
        await _import_ocel(handle, log_id, original_path, paths, flavor=ocel_flavor)
        return

    rows, detected, effective_mapping = await asyncio.to_thread(
        _parse_case_centric,
        source_format,
        original_path,
        csv_mapping_data,
        xml_mapping_data,
        json_mapping_data,
        _progress_bridge(handle),
    )

    # The parser ran inside a single uninterruptible `to_thread` - cancel can't
    # land mid-parse, so an in-flight parse finishes first. Poll here, the first
    # gap with no progress call, so a cancel issued during parsing is honoured
    # before we spend more work normalising/writing.
    handle.raise_if_cancelled()

    total_events = len(rows)
    await handle.progress(
        total_events,
        total=total_events,
        stage="normalizing",
        message="Normalising events",
        force=True,
    )

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("Source file contained zero events.")

    # Canonical column-role resolution (best-effort, never blocks the import).
    # Runs over whatever columns the parser produced: a perfect header match
    # (incl. case-insensitive, e.g. `Activity` → `activity`) maps cleanly; an
    # imperfect match is still applied but flags `mapping_needs_review` so the
    # user can correct the roles in settings (which re-imports). An explicit
    # `column_roles` override (from that re-map) wins outright.
    column_roles_override: dict[str, str] | None = payload.get("column_roles")
    # Candidate columns the user can map from - captured *before* canonicalising
    # so they're stable across re-imports (the parser is deterministic) and the
    # settings "Column roles" picker offers exactly these names.
    source_columns = list(df.columns)
    resolution = resolve_roles(source_columns, sample=df, overrides=column_roles_override)
    df = apply_roles(df, resolution)
    column_roles = dict(resolution.roles)
    mapping_needs_review = resolution.needs_review

    missing = [r for r in ("case_id", "activity", "timestamp") if r not in df.columns]
    if missing:
        raise ValueError(
            "Could not identify the mandatory column(s): "
            + ", ".join(missing)
            + ". Open the log's settings → Column roles to map them manually."
        )

    df["case_id"] = df["case_id"].astype(str)
    df["activity"] = df["activity"].astype(str)
    # `utc=True` so logs that mix UTC offsets (e.g. XES with summer- and
    # winter-time events) collapse to a single tz-aware dtype instead of
    # pandas picking one offset and silently NaT-ing the rest. We then drop
    # the tz to keep the parquet / SQLite `DateTime` column shape unchanged.
    df["timestamp"] = to_datetime_robust(df["timestamp"], utc=True)
    df = df.dropna(subset=["timestamp"])
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    # Rows whose timestamp still failed to parse are dropped — never silently:
    # the count is surfaced in detected_schema (meta.json + the log row).
    rows_dropped_invalid_timestamp = total_events - len(df)
    if rows_dropped_invalid_timestamp > 0:
        log.warning(
            "ingest.rows_dropped_invalid_timestamp",
            log_id=log_id,
            dropped=rows_dropped_invalid_timestamp,
            total=total_events,
        )
    if "end_timestamp" in df.columns:
        end_ts = to_datetime_robust(df["end_timestamp"], utc=True)
        df["end_timestamp"] = end_ts.dt.tz_localize(None)
    df = df.sort_values(["case_id", "timestamp"], kind="mergesort").reset_index(drop=True)

    # Guarantee no two columns collide case-insensitively before the frame is
    # ever persisted: everything downstream reads events.parquet through DuckDB,
    # which folds case-variant columns together (e.g. a domain `Activity` next
    # to the canonical `activity`) and would otherwise hand modules the wrong
    # column. Canonical roles keep their exact name; case-clashing extras are
    # moved aside to `<name>__src`.
    df = df.rename(columns=dedupe_case_insensitive_columns(list(df.columns)))

    await handle.progress(
        total_events,
        total=total_events,
        stage="writing",
        message="Writing events.parquet",
        force=True,
    )

    # Parquet-safe dtype coercion for messy object columns (see helper docs).
    # case_id and activity are excluded - they're contractually strings (pm4py
    # / Discovery treat case_id as the trace key) and an all-digit case_id
    # would otherwise get silently re-typed to int here.
    fixed_columns = coerce_object_columns(df, string_only={"case_id", "activity"})

    # The "writing" progress tick above already auto-raised on a pending cancel
    # (JobHandle.progress polls the token first); this guards the cases write,
    # which has no progress call before it.
    df.to_parquet(paths.events, index=False, engine="pyarrow", compression="zstd")

    handle.raise_if_cancelled()
    cases_df = compute_cases(df)
    cases_df.to_parquet(paths.cases, index=False, engine="pyarrow", compression="zstd")

    detected_schema = {
        **detected,
        "columns": list(df.columns),
        "source_columns": source_columns,
        "row_count": len(df),
        "rows_dropped_invalid_timestamp": rows_dropped_invalid_timestamp,
        "column_roles": column_roles,
        # How each role was matched (user / exact / fuzzy / fallback) - the same
        # signal the import wizard shows as its confidence chip, kept so the
        # log's settings can render it after the fact.
        "column_role_quality": dict(resolution.quality),
        "mapping_needs_review": mapping_needs_review,
    }

    meta = {
        "log_id": log_id,
        "source_format": source_format,
        "source_filename": original_path.name,
        "imported_at": datetime.now(UTC).isoformat(),
        "log_model": "case_centric",
        "events_count": len(df),
        "cases_count": int(cases_df.shape[0]),
        "variants_count": int(cases_df["variant_id"].nunique()),
        "date_min": _to_iso(df["timestamp"].min()),
        "date_max": _to_iso(df["timestamp"].max()),
        "detected_schema": detected_schema,
        "mapping": effective_mapping,
    }
    paths.write_meta(meta)

    # Decide the resting status: `ready` immediately when no installed module
    # subscribes to `log.imported`, else `processing` until those modules finish
    # precomputing (the coordinator un-gates the log off their terminal jobs).
    async with handle.sessionmaker() as session:
        expected, plan = await _precompute_plan("log.imported", handle.user_id, session)
        await session.execute(
            update(EventLog)
            .where(EventLog.id == log_id)
            .values(
                status="ready" if not expected else "processing",
                processing_import_job_id=None if not expected else handle.id,
                expected_modules=None if not expected else sorted(expected),
                log_model="case_centric",
                events_count=meta["events_count"],
                cases_count=meta["cases_count"],
                variants_count=meta["variants_count"],
                date_min=df["timestamp"].min().to_pydatetime()
                if pd.notna(df["timestamp"].min())
                else None,
                date_max=df["timestamp"].max().to_pydatetime()
                if pd.notna(df["timestamp"].max())
                else None,
                detected_schema=detected_schema,
                column_roles=column_roles,
                mapping_needs_review=mapping_needs_review,
                imported_at=datetime.now(UTC).replace(tzinfo=None),
                error=None,
            )
        )
        if expected:
            await _stash_precompute_plan(session, handle.id, plan)
        await session.commit()

    # Persist the freshly-written log dir to the S3 primary store (no-op in
    # local mode). Best-effort: a failure is logged, the local copy still serves.
    await storage_sync.persist_log(handle.user_id, log_id)

    await handle.progress(
        total_events,
        total=total_events,
        stage="done",
        message="Import complete",
        force=True,
    )
    log.info(
        "ingest.complete",
        log_id=log_id,
        events=meta["events_count"],
        cases=meta["cases_count"],
    )

    # Hand the precompute DAG plan to the jobs UI before the children spawn, so a
    # live import's group card can render waiting/skipped steps immediately (the
    # frontend store otherwise only sees the plan on a full `GET /jobs` rehydrate).
    if expected:
        await handle.bus.publish(
            "job.plan",
            {"id": handle.id, "user_id": handle.user_id, "precompute_plan": plan},
        )

    await handle.bus.publish(
        "log.imported",
        {
            "log_id": log_id,
            "user_id": handle.user_id,
            "import_job_id": handle.id,
            "events_count": meta["events_count"],
            "cases_count": meta["cases_count"],
            "detected_schema": detected_schema,
            "fixed_columns": fixed_columns,
        },
    )
    # No subscribing module → the log is already openable; emit `log.ready` so
    # the frontend flips it without waiting on a (never-coming) module finish.
    if not expected:
        await handle.bus.publish("log.ready", {"user_id": handle.user_id, "log_id": log_id})


async def _import_ocel(
    handle: JobHandle,
    log_id: str,
    original_path: Path,
    paths: LogPaths,
    *,
    flavor: str = "json",
) -> None:
    """Import an object-centric (OCEL) log.

    Parses with pm4py into the four canonical tables and persists them under
    ``ocel/`` - deliberately NOT writing the case-centric root
    events.parquet/cases.parquet (their absence is the isolation guard).

    ``flavor`` (``json`` / ``xml`` / ``sqlite``) selects the pm4py reader; it's
    content-detected at upload and recovered from meta.json on re-import.
    """
    result = await asyncio.to_thread(_parse_ocel_source, original_path, flavor)

    # First gap after the single uninterruptible parse `to_thread` - honour a
    # cancel issued during parsing before normalising/writing the OCEL tables.
    handle.raise_if_cancelled()

    await handle.progress(
        result.stats["events_count"],
        total=result.stats["events_count"],
        stage="normalizing",
        message="Normalising object-centric tables",
        force=True,
    )

    events = result.events
    objects = result.objects
    relations = result.relations
    o2o = result.o2o
    cols = result.columns

    # Parquet-safe dtype coercion for every frame. Identifier / type / activity /
    # qualifier columns are forced to string so all-digit oids aren't re-typed.
    coerce_object_columns(events, string_only={cols["event_id"], cols["event_activity"]})
    coerce_object_columns(objects, string_only={cols["object_id"], cols["object_type"]})
    coerce_object_columns(
        relations,
        string_only={
            cols["event_id"],
            cols["event_activity"],
            cols["object_id"],
            cols["object_type"],
            cols["qualifier"],
        },
    )
    coerce_object_columns(o2o, string_only=set(o2o.columns))

    # Timestamps → tz-naive UTC (mirrors the case-centric path).
    events = normalize_timestamps(events, cols["event_timestamp"])
    if cols["event_timestamp"] in relations.columns and not relations.empty:
        relations = normalize_timestamps(relations, cols["event_timestamp"])

    await handle.progress(
        result.stats["events_count"],
        total=result.stats["events_count"],
        stage="writing",
        message="Writing OCEL tables",
        force=True,
    )

    paths.ensure()
    # Last gap before the OCEL parquet writes - the "writing" progress tick above
    # already polls the token, but guard explicitly so a cancel during the
    # coerce/normalize step stops before any file lands.
    handle.raise_if_cancelled()
    events.to_parquet(paths.ocel_events, index=False, engine="pyarrow", compression="zstd")
    objects.to_parquet(paths.ocel_objects, index=False, engine="pyarrow", compression="zstd")
    relations.to_parquet(paths.ocel_relations, index=False, engine="pyarrow", compression="zstd")
    o2o.to_parquet(paths.ocel_o2o, index=False, engine="pyarrow", compression="zstd")

    ts_series = events[cols["event_timestamp"]]
    date_min = ts_series.min() if not events.empty else None
    date_max = ts_series.max() if not events.empty else None

    meta = {
        "log_id": log_id,
        "source_format": "ocel",
        "ocel_flavor": flavor,
        "source_filename": original_path.name,
        "imported_at": datetime.now(UTC).isoformat(),
        "log_model": "object_centric",
        "events_count": result.stats["events_count"],
        "objects_count": result.stats["objects_count"],
        "object_types_count": result.stats["object_types_count"],
        "relations_count": result.stats["relations_count"],
        "date_min": _to_iso(date_min),
        "date_max": _to_iso(date_max),
        "detected_schema": result.detected_schema,
    }
    paths.write_meta(meta)

    async with handle.sessionmaker() as session:
        expected, plan = await _precompute_plan("ocel.imported", handle.user_id, session)
        await session.execute(
            update(EventLog)
            .where(EventLog.id == log_id)
            .values(
                status="ready" if not expected else "processing",
                processing_import_job_id=None if not expected else handle.id,
                expected_modules=None if not expected else sorted(expected),
                log_model="object_centric",
                events_count=result.stats["events_count"],
                objects_count=result.stats["objects_count"],
                object_types_count=result.stats["object_types_count"],
                relations_count=result.stats["relations_count"],
                cases_count=None,
                variants_count=None,
                date_min=date_min.to_pydatetime() if pd.notna(date_min) else None,
                date_max=date_max.to_pydatetime() if pd.notna(date_max) else None,
                detected_schema=result.detected_schema,
                column_roles=None,
                mapping_needs_review=False,
                imported_at=datetime.now(UTC).replace(tzinfo=None),
                error=None,
            )
        )
        if expected:
            await _stash_precompute_plan(session, handle.id, plan)
        await session.commit()

    # Persist the freshly-written OCEL log dir to the S3 primary store (no-op in
    # local mode). Best-effort: a failure is logged, the local copy still serves.
    await storage_sync.persist_log(handle.user_id, log_id)

    await handle.progress(
        result.stats["events_count"],
        total=result.stats["events_count"],
        stage="done",
        message="Import complete",
        force=True,
    )
    log.info(
        "ingest.complete",
        log_id=log_id,
        events=result.stats["events_count"],
        objects=result.stats["objects_count"],
        log_model="object_centric",
    )

    if expected:
        await handle.bus.publish(
            "job.plan",
            {"id": handle.id, "user_id": handle.user_id, "precompute_plan": plan},
        )

    # Distinct topic from `log.imported` so case-centric subscribers never wake
    # for an OCEL import.
    await handle.bus.publish(
        "ocel.imported",
        {
            "log_id": log_id,
            "user_id": handle.user_id,
            "import_job_id": handle.id,
            "events_count": result.stats["events_count"],
            "objects_count": result.stats["objects_count"],
            "object_types_count": result.stats["object_types_count"],
            "relations_count": result.stats["relations_count"],
            "detected_schema": result.detected_schema,
        },
    )
    if not expected:
        await handle.bus.publish("log.ready", {"user_id": handle.user_id, "log_id": log_id})


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        # Normalized to naive UTC at parse - keep the offset in the JSON form.
        return utc_isoformat(value)
    if isinstance(value, datetime):
        return utc_isoformat(value)
    return str(value)


async def _precompute_plan(
    topic: str, user_id: str, session: AsyncSession
) -> tuple[set[str], list[dict[str, Any]]]:
    """Modules the just-imported log must wait on, plus the DAG plan for the UI.

    Delegates to the process-global :class:`ModuleProcessingCoordinator`. When it
    isn't wired (e.g. a bare-runtime test that never started the module loader)
    we return ``(set(), [])`` so the import falls back to the legacy "ready
    immediately" behaviour instead of failing. The returned set is the transitive
    precompute closure (direct subscribers plus everything chained off their
    ``<id>.completed`` events).
    """
    from mate.api.modules.processing import get_coordinator

    coordinator = get_coordinator()
    if coordinator is None:
        return set(), []
    return await coordinator.precompute_plan(topic, user_id, session)


async def _stash_precompute_plan(
    session: AsyncSession, import_job_id: str, plan: list[dict[str, Any]]
) -> None:
    """Persist the precompute DAG plan onto the import job's payload so the jobs
    UI can render waiting/skipped steps for not-yet-submitted chained modules."""
    job_row = await session.get(Job, import_job_id)
    if job_row is not None:
        payload = dict(job_row.payload_json or {})
        payload["precompute_plan"] = plan
        job_row.payload_json = payload


def register_import_handler(runtime: JobRuntime) -> None:
    runtime.register(IMPORT_JOB_TYPE, _import_handler)


__all__ = [
    "IMPORT_JOB_TYPE",
    "IngestStats",
    "register_import_handler",
]
