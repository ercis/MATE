"""POST/GET/DELETE /api/v1/event-logs - the import surface (§6, §13)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlparse

import aiofiles
import httpx
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select

from mate.api.auth import (
    CurrentUserDep,
    get_owned_event_log,
    get_owned_folder,
)
from mate.api.db.models import EventLog
from mate.api.db.session import SessionDep
from mate.api.ingest.detect import (
    detect_format,
    original_extension,
    sniff_format,
)
from mate.api.ingest.dispatch import IMPORT_JOB_TYPE
from mate.api.ingest.storage import log_paths
from mate.api.jobs.runtime import JobRuntime, get_job_runtime
from mate.api.schemas.event_logs import (
    CsvColumnMapping,
    EventLogCreateResponse,
    EventLogDetail,
    EventLogSummary,
    EventLogUpdate,
    JsonColumnMapping,
    JsonProbeResponse,
    RemapColumnRoles,
    XmlColumnMapping,
    XmlProbeResponse,
)
from mate.api.storage import sync as storage_sync
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/event-logs", tags=["event-logs"])


def _runtime_dep() -> JobRuntime:
    return get_job_runtime()


_RuntimeDep = Annotated[JobRuntime, Depends(_runtime_dep)]


@router.post(
    "",
    response_model=EventLogCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_event_log(
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
    file: Annotated[
        UploadFile,
        File(
            description="XES, XES.GZ, CSV, XML, JSON, or OCEL (.jsonocel/.xmlocel/.sqlite) upload"
        ),
    ],
    name: Annotated[str | None, Form()] = None,
    csv_mapping: Annotated[str | None, Form(description="JSON-encoded CsvColumnMapping")] = None,
    xml_mapping: Annotated[str | None, Form(description="JSON-encoded XmlColumnMapping")] = None,
    json_mapping: Annotated[str | None, Form(description="JSON-encoded JsonColumnMapping")] = None,
    folder_id: Annotated[str | None, Form()] = None,
) -> EventLogCreateResponse:
    if file.filename is None:
        raise HTTPException(status_code=400, detail="Upload is missing a filename.")

    try:
        coarse_format = detect_format(file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    parsed_mapping: CsvColumnMapping | None = None
    if csv_mapping:
        try:
            parsed_mapping = CsvColumnMapping.model_validate(json.loads(csv_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid csv_mapping: {exc}") from exc

    parsed_xml_mapping: XmlColumnMapping | None = None
    if xml_mapping:
        try:
            parsed_xml_mapping = XmlColumnMapping.model_validate(json.loads(xml_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid xml_mapping: {exc}") from exc

    parsed_json_mapping: JsonColumnMapping | None = None
    if json_mapping:
        try:
            parsed_json_mapping = JsonColumnMapping.model_validate(json.loads(json_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid json_mapping: {exc}") from exc

    if folder_id is not None:
        await get_owned_folder(session, folder_id, user.id)

    log_id = uuid7_str()
    paths = log_paths(log_id, user.id)
    paths.ensure()

    ext = original_extension(file.filename, coarse_format)
    original_path = paths.original_for(ext)

    async with aiofiles.open(original_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            await out.write(chunk)

    # Refine the coarse extension guess by inspecting the staged file: plain
    # .json / .xml auto-route to the object-centric (OCEL) or case-centric path.
    source_format, ocel_flavor = await asyncio.to_thread(
        sniff_format, original_path, coarse_format, filename=file.filename
    )

    display_name = (name or file.filename).strip() or file.filename

    session.add(
        EventLog(
            id=log_id,
            user_id=user.id,
            name=display_name,
            source_format=source_format,
            source_filename=file.filename,
            status="importing",
            folder_id=folder_id,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await session.commit()

    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user.id,
        title=f"Import - {display_name}",
        subtitle=f"event_log.import · {source_format}",
        payload={
            "log_id": log_id,
            "source_format": source_format,
            "ocel_flavor": ocel_flavor,
            "original_path": str(original_path),
            "csv_mapping": parsed_mapping.model_dump() if parsed_mapping else None,
            "xml_mapping": parsed_xml_mapping.model_dump() if parsed_xml_mapping else None,
            "json_mapping": parsed_json_mapping.model_dump() if parsed_json_mapping else None,
        },
    )

    log.info(
        "event_log.created",
        log_id=log_id,
        job_id=job_id,
        source_format=source_format,
    )
    return EventLogCreateResponse(log_id=log_id, job_id=job_id)


class ImportFromUrlRequest(BaseModel):
    url: HttpUrl
    name: str | None = None
    csv_mapping: str | None = None  # JSON-encoded CsvColumnMapping
    xml_mapping: str | None = None  # JSON-encoded XmlColumnMapping
    json_mapping: str | None = None  # JSON-encoded JsonColumnMapping


@router.post(
    "/from-url",
    response_model=EventLogCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_event_log_from_url(
    body: ImportFromUrlRequest,
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
) -> EventLogCreateResponse:
    """Download a remote XES / XES.GZ / CSV / XML / JSON / OCEL and queue it."""
    url_str = str(body.url)
    # Derive a filename from the URL path so detect_format can sniff the extension.
    url_path = unquote(urlparse(url_str).path)
    filename = url_path.rsplit("/", 1)[-1] or "import"

    try:
        coarse_format = detect_format(filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=415,
            detail=f"Cannot determine file format from URL path ({filename!r}). "
            "Make sure the URL ends with .xes, .xes.gz, .csv, .xml, .json, or an OCEL extension.",
        ) from exc

    parsed_mapping: CsvColumnMapping | None = None
    if body.csv_mapping:
        try:
            parsed_mapping = CsvColumnMapping.model_validate(json.loads(body.csv_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid csv_mapping: {exc}") from exc

    parsed_xml_mapping: XmlColumnMapping | None = None
    if body.xml_mapping:
        try:
            parsed_xml_mapping = XmlColumnMapping.model_validate(json.loads(body.xml_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid xml_mapping: {exc}") from exc

    parsed_json_mapping: JsonColumnMapping | None = None
    if body.json_mapping:
        try:
            parsed_json_mapping = JsonColumnMapping.model_validate(json.loads(body.json_mapping))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid json_mapping: {exc}") from exc

    # Download the remote file.
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url_str)
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=400,
                    detail=f"Remote server returned HTTP {resp.status_code} for the given URL.",
                )
            raw = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {exc}") from exc

    log_id = uuid7_str()
    paths = log_paths(log_id, user.id)
    paths.ensure()

    ext = original_extension(filename, coarse_format)
    original_path = paths.original_for(ext)

    async with aiofiles.open(original_path, "wb") as out:
        await out.write(raw)

    # Refine the coarse guess from the downloaded content (OCEL vs case-centric).
    source_format, ocel_flavor = await asyncio.to_thread(
        sniff_format, original_path, coarse_format, filename=filename
    )

    display_name = (body.name or filename).strip() or filename
    # Strip the extension from auto-derived names to keep things clean.
    if not body.name:
        for suffix in (".xes.gz", ".xes", ".csv", ".xml", ".json", ".jsonocel", ".xmlocel"):
            if display_name.lower().endswith(suffix):
                display_name = display_name[: -len(suffix)]
                break

    session.add(
        EventLog(
            id=log_id,
            user_id=user.id,
            name=display_name,
            source_format=source_format,
            source_filename=filename,
            status="importing",
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await session.commit()

    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user.id,
        title=f"Import - {display_name}",
        subtitle=f"event_log.import · {source_format} (url)",
        payload={
            "log_id": log_id,
            "source_format": source_format,
            "ocel_flavor": ocel_flavor,
            "original_path": str(original_path),
            "csv_mapping": parsed_mapping.model_dump() if parsed_mapping else None,
            "xml_mapping": parsed_xml_mapping.model_dump() if parsed_xml_mapping else None,
            "json_mapping": parsed_json_mapping.model_dump() if parsed_json_mapping else None,
        },
    )

    log.info(
        "event_log.created_from_url",
        log_id=log_id,
        job_id=job_id,
        source_format=source_format,
        url=url_str,
    )
    return EventLogCreateResponse(log_id=log_id, job_id=job_id)


@router.post("/probe-xml", response_model=XmlProbeResponse)
async def probe_xml_upload(
    file: Annotated[UploadFile, File(description="XML file to probe for fields")],
) -> XmlProbeResponse:
    """Inspect an uploaded XML file and return its candidate event element +
    field list. Drives the import-form mapping wizard before the actual upload.
    """
    # Stream the upload to a temp file so the probe can use lxml's path-based
    # parsing without holding the whole document in memory twice.
    fd, tmp_name = tempfile.mkstemp(suffix=".xml", prefix="ff-xml-probe-")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        async with aiofiles.open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                await out.write(chunk)
        # Avoid the late import of xml_parser at module-load time - lxml's
        # iterparse is sync and CPU-bound, so this runs in a thread.
        from mate.api.ingest.xml_parser import autodetect_mapping, probe_xml

        try:
            probe = await asyncio.to_thread(probe_xml, tmp_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse XML file: {exc}") from exc
        # XES- and OCEL-shaped probes ship without fields - they're handled by
        # the XES / OCEL parser at import time, so the frontend skips the wizard.
        hint = probe.get("format_hint") or "generic"
        mapping = (
            None if hint in ("xes", "ocel") else await asyncio.to_thread(autodetect_mapping, probe)
        )
        return XmlProbeResponse(
            format_hint=hint,
            event_element=probe.get("event_element"),
            events_sampled=int(probe.get("events_sampled") or 0),
            fields=probe.get("fields") or [],
            auto_mapping=mapping,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@router.post("/probe-json", response_model=JsonProbeResponse)
async def probe_json_upload(
    file: Annotated[UploadFile, File(description="JSON file to probe for fields")],
) -> JsonProbeResponse:
    """Inspect an uploaded JSON file and return its candidate event array +
    field list (or flag it as OCEL). Drives the import-form mapping wizard.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".json", prefix="ff-json-probe-")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        async with aiofiles.open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                await out.write(chunk)
        from mate.api.ingest.json_parser import autodetect_mapping, probe_json

        try:
            probe = await asyncio.to_thread(probe_json, tmp_path)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Could not parse JSON file: {exc}"
            ) from exc
        hint = probe.get("format_hint") or "generic"
        mapping = None if hint == "ocel" else await asyncio.to_thread(autodetect_mapping, probe)
        return JsonProbeResponse(
            format_hint=hint,
            event_path=probe.get("event_path"),
            events_sampled=int(probe.get("events_sampled") or 0),
            fields=probe.get("fields") or [],
            auto_mapping=mapping,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@router.get("", response_model=list[EventLogSummary])
async def list_event_logs(
    session: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query()] = None,
) -> list[EventLogSummary]:
    stmt = (
        select(EventLog)
        .where(EventLog.user_id == user.id, EventLog.deleted_at.is_(None))
        .order_by(EventLog.created_at.desc())
    )
    if status_filter:
        stmt = stmt.where(EventLog.status == status_filter)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(EventLog.name.ilike(like))
    rows = (await session.execute(stmt)).scalars().all()
    return [EventLogSummary.model_validate(r) for r in rows]


@router.get("/{log_id}", response_model=EventLogDetail)
async def get_event_log(log_id: str, session: SessionDep, user: CurrentUserDep) -> EventLogDetail:
    row = await get_owned_event_log(session, log_id, user.id)
    return EventLogDetail.model_validate(row)


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event_log(
    log_id: str,
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
) -> None:
    row = await get_owned_event_log(session, log_id, user.id)
    # Terminate active jobs (import / re-import / module runs) before tearing
    # down the row + on-disk data so workers don't keep writing to a directory
    # we're about to rmtree.
    cancelled = await runtime.cancel_for_logs([log_id])
    if cancelled:
        log.info("event_log.jobs_cancelled", log_id=log_id, count=cancelled)
    row.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    paths = log_paths(log_id, user.id)
    if paths.exists():
        try:
            shutil.rmtree(paths.root)
        except OSError as exc:
            log.warning("event_log.cleanup_failed", log_id=log_id, error=str(exc))
    # Remove the mirrored copy from the S3 primary store too (no-op in local mode).
    await storage_sync.delete_log(user.id, log_id)


@router.patch("/{log_id}", response_model=EventLogDetail)
async def update_event_log(
    log_id: str,
    payload: EventLogUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> EventLogDetail:
    row = await get_owned_event_log(session, log_id, user.id)
    if payload.name is not None:
        cleaned = payload.name.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Name cannot be empty.")
        if len(cleaned) > 255:
            raise HTTPException(status_code=422, detail="Name is too long (max 255 characters).")
        row.name = cleaned
    if payload.description is not None:
        # Empty string clears the description; any non-empty value is stored verbatim.
        cleaned_desc = payload.description.strip()
        row.description = cleaned_desc or None
    if payload.column_overrides is not None:
        # Pydantic already enforces dict shape; the schema is open-ended (labels/order/hidden).
        row.column_overrides = payload.column_overrides
    # `folder_id` is explicitly nullable - model_fields_set distinguishes
    # "key wasn't sent" from "explicitly set to null (move to root)".
    if "folder_id" in payload.model_fields_set:
        if payload.folder_id is not None:
            await get_owned_folder(session, payload.folder_id, user.id)
        row.folder_id = payload.folder_id
    if payload.position is not None:
        row.position = payload.position
    await session.commit()
    return EventLogDetail.model_validate(row)


@router.post(
    "/{log_id}/reimport",
    response_model=EventLogCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reimport_event_log(
    log_id: str,
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
) -> EventLogCreateResponse:
    """Re-run the import job using the original upload that's still on disk.

    The CSV mapping (when applicable) is recovered from the previous run's
    `meta.json` so column-mapped CSVs don't need to be re-mapped.
    """
    row = await get_owned_event_log(session, log_id, user.id)
    if row.status == "importing":
        raise HTTPException(status_code=409, detail="Import already in progress.")
    if not row.source_format:
        raise HTTPException(
            status_code=409, detail="No source format on record - cannot re-run import."
        )

    paths = log_paths(log_id, user.id)
    # The retained upload may live only in the S3 bucket on a cold cache - pull
    # the log dir back before locating it (no-op in local mode).
    await storage_sync.hydrate_log(user.id, log_id)
    # OCEL stores its upload under the real suffix (jsonocel/xmlocel/sqlite), not
    # original.ocel - locate by glob so re-import works for every format.
    original_path = paths.original_for(row.source_format)
    if not original_path.exists():
        located = paths.find_original()
        if located is None:
            raise HTTPException(
                status_code=409,
                detail="Original upload is missing on disk - cannot re-run import.",
            )
        original_path = located

    saved_mapping: dict[str, Any] | None = None
    # OCEL reader flavor is content-detected at first import; recover it from
    # meta so re-import picks the same pm4py reader (the .json/.xml suffix alone
    # is not enough to distinguish OCEL json from OCEL xml).
    ocel_flavor: str | None = None
    if paths.meta.exists():
        try:
            meta = json.loads(paths.meta.read_text())
            if isinstance(meta, dict):
                mapping = meta.get("mapping")
                saved_mapping = mapping if isinstance(mapping, dict) else None
                flavor = meta.get("ocel_flavor")
                ocel_flavor = flavor if isinstance(flavor, str) else None
        except (OSError, json.JSONDecodeError):
            saved_mapping = None

    csv_mapping_data = saved_mapping if row.source_format == "csv" else None
    xml_mapping_data = saved_mapping if row.source_format == "xml" else None
    json_mapping_data = saved_mapping if row.source_format == "json" else None

    # Reset derived state so the listing reflects "importing" while the worker
    # rebuilds events.parquet / cases.parquet / meta.json.
    row.status = "importing"
    row.error = None
    row.events_count = None
    row.cases_count = None
    row.variants_count = None
    row.objects_count = None
    row.object_types_count = None
    row.relations_count = None
    row.date_min = None
    row.date_max = None
    row.detected_schema = None
    row.imported_at = None
    await session.commit()

    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user.id,
        title=f"Re-import - {row.name}",
        subtitle=f"event_log.import · {row.source_format}",
        payload={
            "log_id": log_id,
            "source_format": row.source_format,
            "ocel_flavor": ocel_flavor,
            "original_path": str(original_path),
            "csv_mapping": csv_mapping_data,
            "xml_mapping": xml_mapping_data,
            "json_mapping": json_mapping_data,
        },
    )
    log.info("event_log.reimport_started", log_id=log_id, job_id=job_id)
    return EventLogCreateResponse(log_id=log_id, job_id=job_id)


@router.post(
    "/{log_id}/remap",
    response_model=EventLogCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def remap_event_log(
    log_id: str,
    body: RemapColumnRoles,
    session: SessionDep,
    runtime: _RuntimeDep,
    user: CurrentUserDep,
) -> EventLogCreateResponse:
    """Re-import the log from its retained original with the user's chosen
    column roles forced. Backs the settings "Column roles" picker - the user
    points case_id / activity / timestamp (+ optional roles) at the right source
    columns and the importer rebuilds everything from scratch.
    """
    row = await get_owned_event_log(session, log_id, user.id)
    if row.log_model == "object_centric":
        raise HTTPException(
            status_code=409,
            detail="Column-role remapping does not apply to object-centric (OCEL) logs.",
        )
    if row.status == "importing":
        raise HTTPException(status_code=409, detail="Import already in progress.")
    if not row.source_format:
        raise HTTPException(status_code=409, detail="No source format on record - cannot re-map.")

    paths = log_paths(log_id, user.id)
    # Pull the retained upload back from S3 if the local cache is cold.
    await storage_sync.hydrate_log(user.id, log_id)
    original_path = paths.original_for(row.source_format)
    if not original_path.exists():
        raise HTTPException(
            status_code=409, detail="Original upload is missing on disk - cannot re-map."
        )

    roles = body.as_roles()
    # Validate the chosen source columns against what the importer last saw, when
    # we have that on record - a stale/typo'd column name would otherwise just
    # silently fall through to autodetect.
    schema = row.detected_schema if isinstance(row.detected_schema, dict) else {}
    known = schema.get("source_columns") or schema.get("columns")
    if isinstance(known, list) and known:
        unknown = sorted({c for c in roles.values() if c not in known})
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown column(s) for this log: {', '.join(unknown)}.",
            )

    row.status = "importing"
    row.error = None
    row.events_count = None
    row.cases_count = None
    row.variants_count = None
    row.date_min = None
    row.date_max = None
    await session.commit()

    # The explicit `column_roles` override is authoritative - applied centrally
    # in dispatch over the freshly re-parsed columns - so we deliberately don't
    # pass the previous csv/xml mapping (which would re-trigger the parser's own
    # rename and fight the override).
    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user.id,
        title=f"Re-map - {row.name}",
        subtitle=f"event_log.import · {row.source_format}",
        payload={
            "log_id": log_id,
            "source_format": row.source_format,
            "original_path": str(original_path),
            "csv_mapping": None,
            "xml_mapping": None,
            "column_roles": roles,
        },
    )
    log.info("event_log.remap_started", log_id=log_id, job_id=job_id, roles=roles)
    return EventLogCreateResponse(log_id=log_id, job_id=job_id)


@router.post(
    "/{log_id}/duplicate",
    response_model=EventLogDetail,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_event_log(
    log_id: str, session: SessionDep, user: CurrentUserDep
) -> EventLogDetail:
    """Fast-clone an event log by copying its on-disk directory.

    Cheaper than re-importing because the parquet outputs already exist; we
    just clone the bytes into a fresh log id and persist a new metadata row
    in the same folder, immediately after the source log.
    """
    src = await get_owned_event_log(session, log_id, user.id)
    if src.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="Only ready event logs can be duplicated.",
        )

    src_paths = log_paths(log_id, user.id)
    # On a cold S3 cache the bytes live only in the bucket - pull them first.
    await storage_sync.hydrate_log(user.id, log_id)
    if not src_paths.exists():
        raise HTTPException(
            status_code=409,
            detail="Source data is missing on disk - cannot duplicate.",
        )

    new_id = uuid7_str()
    new_paths = log_paths(new_id, user.id)
    try:
        shutil.copytree(src_paths.root, new_paths.root)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Copy failed: {exc}") from exc

    # Sit the duplicate right after the source within the same folder.
    now = datetime.now(UTC).replace(tzinfo=None)
    duplicate = EventLog(
        id=new_id,
        user_id=user.id,
        name=f"{src.name} (copy)",
        source_format=src.source_format,
        source_filename=src.source_filename,
        log_model=src.log_model,
        status="ready",
        events_count=src.events_count,
        cases_count=src.cases_count,
        variants_count=src.variants_count,
        objects_count=src.objects_count,
        object_types_count=src.object_types_count,
        relations_count=src.relations_count,
        date_min=src.date_min,
        date_max=src.date_max,
        detected_schema=src.detected_schema,
        description=src.description,
        column_overrides=src.column_overrides,
        active_filter=src.active_filter,
        folder_id=src.folder_id,
        position=src.position + 1,
        created_at=now,
        imported_at=now,
    )
    session.add(duplicate)
    await session.commit()
    # Mirror the cloned dir to the S3 primary store (no-op in local mode).
    await storage_sync.persist_log(user.id, new_id)
    log.info("event_log.duplicated", source_log_id=log_id, new_log_id=new_id)
    return EventLogDetail.model_validate(duplicate)
