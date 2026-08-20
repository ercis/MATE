"""Pydantic schemas for the event-data, variants, data-quality and edit
audit endpoints (used by the Events / Variants / Settings tabs).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["string", "number", "datetime", "duration", "enum", "boolean"]
ColumnRole = Literal[
    "case_id",
    "activity",
    "timestamp",
    "end_timestamp",
    "resource",
    "cost",
    "role",
    "lifecycle",
    "custom",
]


class ColumnSpec(BaseModel):
    """One column in the events table - describes how to render and edit it."""

    name: str
    label: str
    role: ColumnRole
    type: ColumnType
    nullable: bool
    required: bool = False
    enum_values: list[str] | None = None


class EventsHeader(BaseModel):
    """The few aggregate counters shown above the events table - refreshed
    on every PATCH so the page header stays in sync with edits.
    """

    events_count: int
    cases_count: int
    variants_count: int
    date_min: datetime | None = None
    date_max: datetime | None = None


class EventsPage(BaseModel):
    """Response for `GET /events`."""

    rows: list[dict[str, Any]]
    total: int
    offset: int
    limit: int
    columns: list[ColumnSpec]
    header: EventsHeader


class CellPatch(BaseModel):
    """Body for `PATCH /events/{row_index}` - one cell change."""

    field: str
    value: Any | None = None


class CellPatchResult(BaseModel):
    """Response for a successful PATCH - the new row, its (potentially
    re-sorted) row_index, and refreshed header counts.
    """

    row: dict[str, Any]
    row_index: int
    new_row_index: int
    header: EventsHeader


class ColumnValueEntry(BaseModel):
    """One distinct value in a column + how many rows carry it."""

    value: str
    count: int


class ColumnValuesPage(BaseModel):
    """Distinct values for a column - backs the Events-tab filter checklist."""

    field: str
    values: list[ColumnValueEntry]
    total_distinct: int
    # True when `total_distinct` exceeds what we returned, so the UI can hint
    # that the user should narrow with the search box.
    truncated: bool


class FilterEntryModel(BaseModel):
    """One `{field, op, value?}` entry of an applied filter."""

    field: str
    op: str
    value: Any | None = None


class ActiveFilterUpdate(BaseModel):
    """Body for `PUT /active-filter` - the filter to commit. An empty list
    clears the filter (full dataset)."""

    filter: list[FilterEntryModel] = Field(default_factory=list)


class ActiveFilterResult(BaseModel):
    """Response after committing an applied filter."""

    active_filter: list[FilterEntryModel]
    modules_retriggered: bool


class TimeBounds(BaseModel):
    """Earliest/latest timestamp in a log - seeds the dashboard time-range
    slider. ``null`` bounds mean the log has no usable timestamp column or no
    rows. Timestamps are ISO-8601 strings (the raw column's value)."""

    field: str | None = None
    min_ts: str | None = None
    max_ts: str | None = None


class BulkFillBody(BaseModel):
    row_indices: list[int] = Field(..., min_length=1)
    field: str
    value: Any | None = None


class BulkFillResult(BaseModel):
    updated: int
    header: EventsHeader


class VariantRow(BaseModel):
    """Row in the variants list."""

    rank: int
    variant_id: str
    activities: list[str]
    case_count: int
    case_pct: float
    avg_duration_seconds: float | None = None
    median_duration_seconds: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class VariantsPage(BaseModel):
    rows: list[VariantRow]
    total: int
    offset: int
    limit: int


class AttributeBreakdownEntry(BaseModel):
    value: Any | None
    count: int


class AttributeBreakdown(BaseModel):
    column: str
    label: str
    top: list[AttributeBreakdownEntry]


class VariantDetail(BaseModel):
    """Response for `GET /variants/{variant_id}` - drives the variant detail page."""

    rank: int
    variant_id: str
    activities: list[str]
    case_count: int
    case_pct: float
    avg_duration_seconds: float | None = None
    median_duration_seconds: float | None = None
    p90_duration_seconds: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    duration_histogram: list[int]
    duration_bin_edges_seconds: list[float]
    attribute_breakdowns: list[AttributeBreakdown]


class VariantCase(BaseModel):
    case_id: str
    case_start: datetime | None = None
    case_end: datetime | None = None
    case_duration_seconds: float | None = None
    event_count: int


class VariantCasesPage(BaseModel):
    rows: list[VariantCase]
    total: int
    offset: int
    limit: int


class ColumnQuality(BaseModel):
    column: str
    label: str
    type: ColumnType
    role: ColumnRole
    null_count: int
    null_pct: float
    distinct_count: int


class DataQuality(BaseModel):
    total_events: int
    columns: list[ColumnQuality]


class ActivityRow(BaseModel):
    """One unique activity in the log + its event count."""

    activity: str
    count: int


class ActivitiesPage(BaseModel):
    rows: list[ActivityRow]
    total: int


class EventEditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    log_id: str
    row_index: int
    field: str
    old_value_json: Any | None = None
    new_value_json: Any | None = None
    edited_at: datetime


class EventEditsPage(BaseModel):
    rows: list[EventEditEntry]
    total: int
    offset: int
    limit: int
