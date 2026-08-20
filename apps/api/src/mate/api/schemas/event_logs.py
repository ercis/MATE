"""Pydantic v2 schemas for the /event-logs API surface.

Mirrors INSTRUCTIONS.md §6 and §3.2 - `id` is a UUID v7 string, status is one
of {importing, processing, ready, failed}.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# `importing` = parsing the source; `processing` = parsed, modules precomputing
# (the log is disabled until they finish); `ready` = openable; `failed` = error.
EventLogStatus = Literal["importing", "processing", "ready", "failed"]
SourceFormat = Literal["xes", "xes.gz", "csv", "xml", "json", "ocel"]
# The single case-centric vs object-centric isolation switch. A log is exactly
# one model; the two code paths never mix.
LogModel = Literal["case_centric", "object_centric"]


class CsvColumnMapping(BaseModel):
    """Mapping from CSV columns to canonical event log fields. The frontend
    column-mapping wizard (phase 7) submits this alongside the upload; for now
    it is also accepted directly via the API.
    """

    case_id: str
    activity: str
    timestamp: str
    end_timestamp: str | None = None
    resource: str | None = None
    cost: str | None = None
    delimiter: str | None = ","
    timestamp_format: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class XmlColumnMapping(BaseModel):
    """Mapping from XML element / attribute names to canonical event log fields.

    Generic XML logs flatten a repeating *event element* into a row; each
    event's attributes and direct child elements form the field bag. The
    frontend probes the file to surface candidate fields, then submits this
    mapping alongside the upload.
    """

    event_element: str
    case_id: str
    activity: str
    timestamp: str
    end_timestamp: str | None = None
    resource: str | None = None
    cost: str | None = None
    timestamp_format: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class JsonColumnMapping(BaseModel):
    """Mapping from JSON record fields to canonical event log fields.

    Generic (case-centric) JSON logs are a list of event records, each a flat
    bag of scalar fields. ``event_path`` names the object key the array lives
    under (``None`` for a top-level array). The frontend probes the file to
    surface candidate fields, then submits this mapping alongside the upload.
    """

    event_path: str | None = None
    case_id: str
    activity: str
    timestamp: str
    end_timestamp: str | None = None
    resource: str | None = None
    cost: str | None = None
    timestamp_format: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class XmlProbeField(BaseModel):
    """A field discovered in a probed XML / JSON file."""

    name: str
    coverage: float
    samples: list[str] = Field(default_factory=list)


class XmlProbeResponse(BaseModel):
    # "ocel": the file is object-centric and auto-routes server-side - the
    # frontend skips the mapping wizard, same as for "xes".
    format_hint: Literal["generic", "xes", "ocel"] = "generic"
    event_element: str | None = None
    events_sampled: int = 0
    fields: list[XmlProbeField] = Field(default_factory=list)
    auto_mapping: XmlColumnMapping | None = None


class JsonProbeResponse(BaseModel):
    format_hint: Literal["generic", "ocel"] = "generic"
    event_path: str | None = None
    events_sampled: int = 0
    fields: list[XmlProbeField] = Field(default_factory=list)
    auto_mapping: JsonColumnMapping | None = None


class ImportPayload(BaseModel):
    """Optional metadata sent alongside a multipart upload (form-encoded JSON)."""

    name: str | None = None
    csv_mapping: CsvColumnMapping | None = None
    xml_mapping: XmlColumnMapping | None = None
    json_mapping: JsonColumnMapping | None = None


class EventLogCreateResponse(BaseModel):
    log_id: str
    job_id: str


class EventLogSummary(BaseModel):
    """Row shape for `GET /event-logs`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: EventLogStatus | str
    source_format: str | None = None
    source_filename: str | None = None
    # Case-centric vs object-centric (OCEL). Drives the entire detail-page tab
    # set, header counts, and which API endpoints / modules apply.
    log_model: LogModel = "case_centric"
    events_count: int | None = None
    # Case-centric counts - NULL for object-centric logs.
    cases_count: int | None = None
    variants_count: int | None = None
    # Object-centric counts - NULL for case-centric logs.
    objects_count: int | None = None
    object_types_count: int | None = None
    relations_count: int | None = None
    date_min: datetime | None = None
    date_max: datetime | None = None
    error: str | None = None
    folder_id: str | None = None
    position: int = 0
    created_at: datetime
    imported_at: datetime | None = None
    last_edited_at: datetime | None = None
    # True when the importer had to guess a mandatory column - drives the
    # "review column mapping" warning in the process overview.
    mapping_needs_review: bool = False


class EventLogDetail(EventLogSummary):
    detected_schema: dict[str, Any] | None = None
    description: str | None = None
    column_overrides: dict[str, Any] | None = None
    active_filter: list[dict[str, Any]] | None = None
    # Resolved role → source-column mapping (case_id / activity / timestamp / …).
    column_roles: dict[str, Any] | None = None


class RemapColumnRoles(BaseModel):
    """Manual column-role mapping submitted from a log's settings. Re-imports
    the log from its retained original with these roles forced. The three
    mandatory roles are required; the rest are optional."""

    case_id: str = Field(min_length=1)
    activity: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    end_timestamp: str | None = None
    resource: str | None = None
    cost: str | None = None
    role: str | None = None
    lifecycle: str | None = None

    def as_roles(self) -> dict[str, str]:
        """role → source column, dropping the unset optional roles."""
        return {k: v for k, v in self.model_dump().items() if v}


class EventLogUpdate(BaseModel):
    """Mutable fields on an existing log: display name, free-text notes,
    per-column display overrides (label / order / hidden, see Settings tab).
    """

    name: str | None = None
    description: str | None = None
    column_overrides: dict[str, Any] | None = None
    folder_id: str | None = Field(default=None, description="Pass null to move to root")
    position: int | None = None


# ── Folders ───────────────────────────────────────────────────────────────────


class FolderSummary(BaseModel):
    """Row shape for `GET /folders`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    parent_id: str | None = None
    position: int = 0
    created_at: datetime


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = Field(default=None, description="Pass null to move to root")
    position: int | None = None


class ReorderItem(BaseModel):
    """One entry in a bulk-reorder payload."""

    kind: Literal["folder", "log"]
    id: str
    parent_id: str | None = None  # parent folder for both folders and logs
    position: int


class ReorderRequest(BaseModel):
    items: list[ReorderItem]
