"""Pydantic request/response schemas for /api/v1/dashboards.

A dashboard is a grid of cards (each `(module_id, widget_id)`) bound to one
event log. The placed cards and their react-grid-layout geometry travel
together as a list of `DashboardItem` so a save is one atomic write and the
export payload is a straight passthrough.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mate.api.schemas.event_logs import LogModel

# Canvas grid granularity - how finely cards snap and how much they're spaced.
# The pixel geometry per level lives in the web app (dashboard-queries.ts); the
# backend only stores the chosen level.
Granularity = Literal["free", "fine", "medium", "low"]


class CardChrome(BaseModel):
    """Board-wide card appearance toggles. Applied to every placed card when the
    board renders (the per-card widget itself is untouched)."""

    border: bool = True


class FilterPreset(BaseModel):
    """A named, saved set of global column filters (a "saved filter"). The board can
    mark one as active (``CanvasSettings.active_preset_id``) so it applies on
    load in view mode. ``filters`` mirrors the web ``FilterEntry`` shape but is
    kept as opaque dicts here - the same leniency the ephemeral filter header
    gets; ``EventLogAccess`` validates fields when it bakes the predicate."""

    id: str
    name: str
    filters: list[dict[str, Any]] = Field(default_factory=list)


class CanvasSettings(BaseModel):
    """Per-board canvas preferences. Stored alongside ``items`` in the layout
    blob. Fields here must be declared explicitly: the blob round-trips through
    this model (``model_validate`` / ``model_dump``), so any undeclared key
    would be silently dropped on the next save."""

    granularity: Granularity = "medium"
    chrome: CardChrome = Field(default_factory=CardChrome)
    presets: list[FilterPreset] = Field(default_factory=list)
    active_preset_id: str | None = None


class DashboardItem(BaseModel):
    """One placed card and its grid geometry. The column count is set by the
    board's granularity (web ``GRANULARITY``), so ``x``/``w`` are bounded by the
    finest level's column count (60) rather than a fixed 12."""

    i: str  # stable client id for this placement
    module_id: str
    widget_id: str
    title: str | None = None
    x: int = Field(default=0, ge=0, le=59)
    y: int = 0
    w: int = Field(default=6, ge=1, le=60)
    h: int = Field(default=8, ge=1, le=48)
    # Per-placement card options (e.g. which metric a chart shows). Opaque to
    # the backend; the widget interprets it.
    config: dict[str, Any] = Field(default_factory=dict)


class DashboardSummary(BaseModel):
    """List-view row - no card payload, just enough for the grid of boards."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    event_log_id: str | None = None
    log_model: LogModel = "case_centric"
    card_count: int = 0
    updated_at: datetime


class DashboardDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    event_log_id: str | None = None
    log_model: LogModel = "case_centric"
    items: list[DashboardItem] = Field(default_factory=list)
    settings: CanvasSettings = Field(default_factory=CanvasSettings)
    created_at: datetime
    updated_at: datetime
    # False when the board was opened via a share - the UI then renders it
    # read-only (no edit toolbar, no save). The owner-only mutation routes also
    # 404 for a non-owner, so this is defence-in-depth, not the only gate.
    is_owner: bool = True


class DashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    event_log_id: str | None = None
    # The board's data model, fixed at creation - drives which cards the palette
    # offers and which logs are bindable. Not editable afterwards.
    log_model: LogModel = "case_centric"
    items: list[DashboardItem] = Field(default_factory=list)
    settings: CanvasSettings = Field(default_factory=CanvasSettings)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Name cannot be empty.")
        return cleaned


class DashboardUpdate(BaseModel):
    """Partial update - only fields present in the body are touched. `items`
    and `event_log_id` are nullable+present-aware so a board can be cleared or
    unbound from its log explicitly."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    event_log_id: str | None = None
    items: list[DashboardItem] | None = None
    settings: CanvasSettings | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Name cannot be empty.")
        return cleaned


class DashboardExport(BaseModel):
    """Portable, id-free representation for download / re-import."""

    kind: str = "mate.dashboard"
    version: int = 1
    name: str
    description: str | None = None
    log_model: LogModel = "case_centric"
    items: list[DashboardItem] = Field(default_factory=list)
    settings: CanvasSettings = Field(default_factory=CanvasSettings)


class DashboardImport(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    log_model: LogModel = "case_centric"
    items: list[DashboardItem] = Field(default_factory=list)
    settings: CanvasSettings = Field(default_factory=CanvasSettings)
    event_log_id: str | None = None
