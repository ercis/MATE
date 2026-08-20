"""Scan a watched folder and import any new / changed source files.

:func:`scan_watch` is the single shared core behind both the manual
``POST /watched-folders/{id}/scan`` endpoint and the background poller. It lists
the watch's source location, diffs it against the ``watched_folder_files`` dedup
ledger, and for each new/changed file stages it onto local disk and submits the
normal ``event_log.import`` job (landing the result in the watch's destination
folder). Source files are never moved or deleted.

Errors are contained: a per-file failure is recorded on its ledger row, a
whole-scan failure (unreachable source) is recorded on the watch row. Nothing is
raised to the caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import EventLog, WatchedFolder, WatchedFolderFile
from mate.api.ingest.detect import detect_format, original_extension, sniff_format
from mate.api.ingest.dispatch import IMPORT_JOB_TYPE
from mate.api.ingest.source import SourceFile, list_source, stage_source
from mate.api.ingest.storage import log_paths
from mate.api.jobs.runtime import JobRuntime
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

_KNOWN_SUFFIXES = (
    ".xes.gz",
    ".xes",
    ".csv",
    ".jsonocel",
    ".xmlocel",
    ".ocelsqlite",
    ".sqlite",
    ".xml",
    ".json",
)


@dataclass
class ScanResult:
    found: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _display_name(name: str) -> str:
    base = Path(name).name
    lower = base.lower()
    for suffix in _KNOWN_SUFFIXES:
        if lower.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _unchanged(row: WatchedFolderFile, sf: SourceFile) -> bool:
    """True if an already-imported ledger row still matches the live file."""
    if row.status != "imported":
        return False
    if sf.size is not None and row.size is not None and sf.size != row.size:
        return False
    if sf.etag is not None and row.etag is not None:
        return sf.etag == row.etag
    if sf.mtime is not None and row.mtime is not None:
        return sf.mtime == row.mtime
    # Neither side carries a strong fingerprint - fall back to size equality.
    return sf.size == row.size


def _mapping_for(
    default_mapping: dict[str, object] | None, source_format: str
) -> dict[str, object] | None:
    """Pick the column mapping for a format out of the watch's default_mapping.

    ``default_mapping`` is an optional ``{"csv_mapping": {...}, "xml_mapping":
    {...}, "json_mapping": {...}}`` bag (any subset).
    """
    if not default_mapping:
        return None
    key = {"csv": "csv_mapping", "xml": "xml_mapping", "json": "json_mapping"}.get(source_format)
    if key is None:
        return None
    value = default_mapping.get(key)
    return value if isinstance(value, dict) else None


async def scan_watch(
    watch: WatchedFolder, *, session: AsyncSession, runtime: JobRuntime
) -> ScanResult:
    """List the watch's source, import new/changed files, update the ledger."""
    result = ScanResult()
    try:
        files = await asyncio.to_thread(list_source, watch.source_path)
    except Exception as exc:
        watch.status = "error"
        watch.last_error = str(exc)[:500]
        watch.last_scanned_at = _utcnow()
        await session.commit()
        log.warning("watch.scan_failed", watch_id=watch.id, error=str(exc))
        return result

    result.found = len(files)

    # Load the existing ledger for this watch once.
    ledger_rows = (
        (
            await session.execute(
                select(WatchedFolderFile).where(WatchedFolderFile.watch_id == watch.id)
            )
        )
        .scalars()
        .all()
    )
    ledger = {r.source_name: r for r in ledger_rows}

    for sf in files:
        existing = ledger.get(sf.name)
        if existing is not None and _unchanged(existing, sf):
            result.skipped += 1
            continue

        try:
            await _import_one(watch, sf, existing, session=session, runtime=runtime)
            result.imported += 1
        except Exception as exc:
            result.failed += 1
            _record_failure(watch, sf, existing, error=str(exc)[:500], session=session)
            log.warning("watch.import_failed", watch_id=watch.id, file=sf.name, error=str(exc))

    # Only clear the error state if listing itself succeeded.
    if watch.status == "error":
        watch.status = "active"
    watch.last_error = None
    watch.last_scanned_at = _utcnow()
    await session.commit()
    log.info(
        "watch.scanned",
        watch_id=watch.id,
        found=result.found,
        imported=result.imported,
        skipped=result.skipped,
        failed=result.failed,
    )
    return result


async def _import_one(
    watch: WatchedFolder,
    sf: SourceFile,
    existing: WatchedFolderFile | None,
    *,
    session: AsyncSession,
    runtime: JobRuntime,
) -> None:
    """Stage one source file and submit its import job, mirroring create_event_log."""
    coarse_format = detect_format(sf.name)
    log_id = uuid7_str()
    paths = log_paths(log_id, watch.user_id)
    paths.ensure()

    ext = original_extension(sf.name, coarse_format)
    original_path = paths.original_for(ext)
    await asyncio.to_thread(stage_source, watch.source_path, sf.name, original_path)

    source_format, ocel_flavor = await asyncio.to_thread(
        sniff_format, original_path, coarse_format, filename=sf.name
    )

    display_name = _display_name(sf.name)
    session.add(
        EventLog(
            id=log_id,
            user_id=watch.user_id,
            name=display_name,
            source_format=source_format,
            source_filename=Path(sf.name).name,
            status="importing",
            folder_id=watch.dest_folder_id,
            created_at=_utcnow(),
        )
    )

    mapping = _mapping_for(watch.default_mapping, source_format)
    _upsert_ledger(
        watch,
        sf,
        existing,
        log_id=log_id,
        status="imported",
        error=None,
        session=session,
    )
    # Persist the EventLog row + ledger upsert before the worker starts.
    await session.commit()

    await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=watch.user_id,
        title=f"Import - {display_name}",
        subtitle=f"event_log.import · {source_format} (watch)",
        payload={
            "log_id": log_id,
            "source_format": source_format,
            "ocel_flavor": ocel_flavor,
            "original_path": str(original_path),
            "csv_mapping": mapping if source_format == "csv" else None,
            "xml_mapping": mapping if source_format == "xml" else None,
            "json_mapping": mapping if source_format == "json" else None,
        },
    )


def _record_failure(
    watch: WatchedFolder,
    sf: SourceFile,
    existing: WatchedFolderFile | None,
    *,
    error: str,
    session: AsyncSession,
) -> None:
    _upsert_ledger(watch, sf, existing, log_id=None, status="failed", error=error, session=session)


def _upsert_ledger(
    watch: WatchedFolder,
    sf: SourceFile,
    existing: WatchedFolderFile | None,
    *,
    log_id: str | None,
    status: str,
    error: str | None,
    session: AsyncSession,
) -> None:
    if existing is not None:
        existing.size = sf.size
        existing.etag = sf.etag
        existing.mtime = sf.mtime
        existing.log_id = log_id
        existing.status = status
        existing.error = error
        existing.imported_at = _utcnow()
        return
    session.add(
        WatchedFolderFile(
            id=uuid7_str(),
            watch_id=watch.id,
            source_name=sf.name,
            size=sf.size,
            etag=sf.etag,
            mtime=sf.mtime,
            log_id=log_id,
            status=status,
            error=error,
            imported_at=_utcnow(),
        )
    )


__all__ = ["ScanResult", "scan_watch"]
