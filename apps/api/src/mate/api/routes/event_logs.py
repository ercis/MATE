"""POST/GET/DELETE /api/v1/event-logs - the import surface (§6, §13)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import aiofiles
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
from mate.api.ingest import staging
from mate.api.ingest.compression import decompressed
from mate.api.ingest.detect import (
    detect_format,
    original_extension,
    sniff_format,
)
from mate.api.ingest.dispatch import IMPORT_JOB_TYPE
from mate.api.ingest.probe import probe_staged
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
    LogProbeResponse,
    ProbeColumn,
    RemapColumnRoles,
    XmlColumnMapping,
    XmlProbeResponse,
)

# The from-url / reimport / remap / duplicate / delete bodies live in the
# service layer so the MCP toolset can reuse them; these routes are thin
# adapters over it.
from mate.api.services import log_aggregates
from mate.api.storage.quota import over_quota_sync
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/event-logs", tags=["event-logs"])


def _runtime_dep() -> JobRuntime:
    return get_job_runtime()


_RuntimeDep = Annotated[JobRuntime, Depends(_runtime_dep)]


@router.post(
    "/stage",
    response_model=LogProbeResponse,
)
async def stage_event_log(
    user: CurrentUserDep,
    file: Annotated[
        UploadFile,
        File(
            description="XES, CSV, XML, JSON, or OCEL (.jsonocel/.xmlocel/.sqlite) upload, "
            "optionally compressed (.gz/.bz2/.xz/.zip)"
        ),
    ],
) -> LogProbeResponse:
    """Stage an upload and describe its columns for the import wizard.

    The bytes are written once, under `data/staging/{user}/{token}/`, and then
    sampled: the response carries the source columns with real example values
    plus the role mapping the import *would* apply (`roles`) and how confident
    each guess is (`quality`). Nothing is created in the database - the client
    posts `staging_token` back to `POST /event-logs` to confirm, or walks away
    and lets the TTL sweep reclaim the bytes.
    """
    if file.filename is None:
        raise HTTPException(status_code=400, detail="Upload is missing a filename.")

    try:
        coarse_format = detect_format(file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    if await asyncio.to_thread(over_quota_sync):
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Storage quota reached. Delete data or have the operator raise "
            "STORAGE_S3_QUOTA_BYTES.",
        )

    token = uuid7_str()
    root = await asyncio.to_thread(staging.create_staging_dir, user.id, token)
    staged_path = root / f"original.{original_extension(file.filename, coarse_format).lstrip('.')}"

    try:
        async with aiofiles.open(staged_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                await out.write(chunk)

        try:
            source_format, ocel_flavor = await asyncio.to_thread(
                sniff_format, staged_path, coarse_format, filename=file.filename
            )
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc

        try:
            probe = await asyncio.to_thread(probe_staged, staged_path, source_format)
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("event_log.probe_failed", format=source_format, error=str(exc))
            raise HTTPException(
                status_code=400, detail=f"Could not read the uploaded file: {exc}"
            ) from exc

        await asyncio.to_thread(
            staging.write_manifest,
            root,
            {
                "filename": file.filename,
                "source_format": source_format,
                "ocel_flavor": ocel_flavor,
            },
        )
    except Exception:
        # Never leave bytes behind for a staging attempt that failed - the TTL
        # sweep would eventually get them, but a 415 should cost nothing.
        await asyncio.to_thread(shutil.rmtree, root, True)
        raise

    return LogProbeResponse(
        staging_token=token,
        source_format=source_format,
        log_model="object_centric" if probe.log_model == "object_centric" else "case_centric",
        needs_mapping=probe.needs_mapping,
        columns=[
            ProbeColumn(name=c.name, coverage=c.coverage, samples=c.samples) for c in probe.columns
        ],
        roles=probe.roles,
        quality=probe.quality,
        events_sampled=probe.events_sampled,
        size_bytes=staged_path.stat().st_size,
        filename=file.filename,
        delimiter=probe.delimiter,
        event_element=probe.event_element,
        event_path=probe.event_path,
    )


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
        UploadFile | None,
        File(
            description="XES, CSV, XML, JSON, or OCEL (.jsonocel/.xmlocel/.sqlite) upload, "
            "optionally compressed (.gz/.bz2/.xz/.zip). Omit when passing staging_token."
        ),
    ] = None,
    staging_token: Annotated[
        str | None,
        Form(description="Token from POST /event-logs/stage - confirms an already-staged upload"),
    ] = None,
    name: Annotated[str | None, Form()] = None,
    csv_mapping: Annotated[str | None, Form(description="JSON-encoded CsvColumnMapping")] = None,
    xml_mapping: Annotated[str | None, Form(description="JSON-encoded XmlColumnMapping")] = None,
    json_mapping: Annotated[str | None, Form(description="JSON-encoded JsonColumnMapping")] = None,
    column_roles: Annotated[
        str | None,
        Form(description="JSON-encoded {role: source column} override, from the wizard"),
    ] = None,
    folder_id: Annotated[str | None, Form()] = None,
) -> EventLogCreateResponse:
    if (file is None) == (staging_token is None):
        raise HTTPException(
            status_code=422,
            detail="Provide either a file upload or a staging_token, not both.",
        )

    staged: staging.StagedUpload | None = None
    if staging_token is not None:
        staged = await asyncio.to_thread(staging.resolve_staged, user.id, staging_token)
        if staged is None:
            raise HTTPException(
                status_code=404,
                detail="This upload is no longer staged - it expired or was already imported. "
                "Choose the file again.",
            )
        source_filename = staged.filename
        coarse_format = staged.source_format or "csv"
    else:
        assert file is not None  # narrowed by the XOR check above
        if file.filename is None:
            raise HTTPException(status_code=400, detail="Upload is missing a filename.")
        source_filename = file.filename
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

    parsed_roles: dict[str, str] | None = None
    if column_roles:
        try:
            loaded = json.loads(column_roles)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid column_roles: {exc}") from exc
        if not isinstance(loaded, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in loaded.items()
        ):
            raise HTTPException(
                status_code=422, detail="column_roles must be an object of role → column strings."
            )
        parsed_roles = {k: v for k, v in loaded.items() if v} or None

    if folder_id is not None:
        await get_owned_folder(session, folder_id, user.id)

    # Reject up-front when the bucket is at its quota (S3 mode + quota set only),
    # before staging any bytes. A guardrail: an unknown usage never blocks.
    # (A staged upload already paid this check at /stage time.)
    if staged is None and await asyncio.to_thread(over_quota_sync):
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Storage quota reached. Delete data or have the operator raise "
            "STORAGE_S3_QUOTA_BYTES.",
        )

    log_id = uuid7_str()
    paths = log_paths(log_id, user.id)
    paths.ensure()

    if staged is not None:
        # Already on disk and already sniffed at /stage time: move the bytes into
        # the log directory instead of re-uploading them.
        original_path = paths.root / staged.file.name
        await asyncio.to_thread(shutil.move, str(staged.file), str(original_path))
        await asyncio.to_thread(staged.discard)
        source_format = staged.source_format
        ocel_flavor = staged.ocel_flavor
    else:
        assert file is not None  # narrowed at the top of the handler
        ext = original_extension(source_filename, coarse_format)
        original_path = paths.original_for(ext)

        async with aiofiles.open(original_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                await out.write(chunk)

        # Refine the coarse extension guess by inspecting the staged file: plain
        # .json / .xml auto-route to the object-centric (OCEL) or case-centric path,
        # and a bare .zip resolves to its single member's format. ValueError =
        # empty/ambiguous archive → reject before any DB row exists.
        try:
            source_format, ocel_flavor = await asyncio.to_thread(
                sniff_format, original_path, coarse_format, filename=source_filename
            )
        except ValueError as exc:
            await asyncio.to_thread(paths.remove)
            raise HTTPException(status_code=415, detail=str(exc)) from exc

    display_name = (name or source_filename).strip() or source_filename

    session.add(
        EventLog(
            id=log_id,
            user_id=user.id,
            name=display_name,
            source_format=source_format,
            source_filename=source_filename,
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
            "column_roles": parsed_roles,
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
    log_id, job_id = await log_aggregates.import_log_from_url(
        session,
        runtime,
        user.id,
        url=str(body.url),
        name=body.name,
        csv_mapping=body.csv_mapping,
        xml_mapping=body.xml_mapping,
        json_mapping=body.json_mapping,
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
        # iterparse is sync and CPU-bound, so this runs in a thread. The probe
        # accepts compressed uploads (gz/bz2/xz/zip) - same contract as import.
        from mate.api.ingest.xml_parser import autodetect_mapping, probe_xml

        def _probe_plain(p: Path) -> dict[str, Any]:
            with decompressed(p) as plain:
                return probe_xml(plain)

        try:
            probe = await asyncio.to_thread(_probe_plain, tmp_path)
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
        with contextlib.suppress(OSError):
            tmp_path.unlink()


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

        def _probe_plain(p: Path) -> dict[str, Any]:
            with decompressed(p) as plain:
                return probe_json(plain)

        try:
            probe = await asyncio.to_thread(_probe_plain, tmp_path)
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
        with contextlib.suppress(OSError):
            tmp_path.unlink()


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
    await log_aggregates.delete_log_and_data(session, runtime, row, user.id)


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
    job_id = await log_aggregates.reimport_log(session, runtime, row, user.id)
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
    job_id = await log_aggregates.remap_log(session, runtime, row, user.id, body.as_roles())
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
    duplicate = await log_aggregates.duplicate_log(session, src, user.id)
    return EventLogDetail.model_validate(duplicate)
