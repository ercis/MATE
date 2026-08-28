"""Pydantic request/response schemas for /api/v1/dashboards.

A dashboard is a grid of cards (each `(module_id, widget_id)`) bound to one
event log. The placed cards and their react-grid-layout geometry travel
together as a list of `DashboardItem` so a save is one atomic write and the
export payload is a straight passthrough.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mate.api.schemas.common import UtcDateTime
from mate.api.schemas.event_logs import LogModel

# The board grid. One fixed 12-column layout - there is no per-board snap
# level any more. Absolute size floors live on the widget manifest
# (``min_px_w``/``min_px_h``) so a card's minimum is a real pixel size instead
# of a cell count whose meaning changed with the board's granularity.
GRID_COLS = 12
# Vertical bound. Rows are ~18px, so this is a ~7000px-tall board - far past
# any real layout, but finite.
MAX_ROW = 400
# Bumped when stored geometry changes meaning. v1 = the four-granularity era
# (``x``/``w`` in 12/24/40/60 columns); v2 = the fixed 12-column grid.
GRID_VERSION = 2

# LEGACY. The pre-v2 snap levels and their column counts, kept solely to
# interpret stored geometry written before the 12-column grid. Never written.
Granularity = Literal["free", "fine", "medium", "low"]
LEGACY_GRANULARITY_COLS: dict[str, int] = {"free": 60, "fine": 40, "medium": 24, "low": 12}
# The widest grid that ever existed, and therefore the widest geometry an old
# export file can contain. Used as the *input* bound on DashboardItem - see the
# note there for why that is wider than GRID_COLS.
LEGACY_MAX_COLS = 60


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

    model_config = ConfigDict(populate_by_name=True)

    grid_version: int = GRID_VERSION
    chrome: CardChrome = Field(default_factory=CardChrome)
    presets: list[FilterPreset] = Field(default_factory=list)
    active_preset_id: str | None = None
    # The board's *committed live view* - the owner's current column-filter bar
    # and time-range window, persisted here so a shared board opens on exactly
    # the owner's filtered view (the recipient reads them off the detail
    # response and seeds their ephemeral filter state). ``None`` (absent, e.g.
    # legacy boards) means "never committed" - the web app then falls back to
    # the active preset for columns. Opaque dicts, same as ``FilterPreset``.
    column_filters: list[dict[str, Any]] | None = None
    time_filters: list[dict[str, Any]] | None = None

    # LEGACY, read-only. Pre-v2 blobs always carried a ``granularity`` key (it
    # was a required field with a default, so ``model_dump`` always emitted
    # it). Its presence is therefore an exact marker for "this board's x/w are
    # in some other column count", and its value is the only way to decode
    # them - dropping the key like any other unknown would make the geometry
    # permanently uninterpretable. ``exclude=True`` keeps it out of every
    # ``model_dump``, so a coerced board is rewritten without it and is never
    # rescaled twice (see ``routes.dashboards.coerce_grid``).
    legacy_granularity: Granularity | None = Field(default=None, alias="granularity", exclude=True)


class DatasetRef(BaseModel):
    """Points a ``kind="viz"`` card at a module dataset (manifest ``datasets:``).
    The card fetches the data from the module's own route and renders it with a
    generic visualization."""

    module_id: str
    dataset_id: str


class DashboardItem(BaseModel):
    """One placed card and its grid geometry, on the fixed 12-column grid
    (``GRID_COLS``). Geometry written before v2 used the board's granularity as
    its column count (12/24/40/60); it is rescaled once by Alembic ``0014`` and
    at import/create by ``routes.dashboards.coerce_grid``.

    A card's *minimum* size is not expressed here - it comes from the widget
    manifest (``min_w``/``min_h`` in grid units plus the absolute
    ``min_px_w``/``min_px_h`` floors) and is enforced by the canvas.

    Two card kinds share this shape (discriminated by ``kind``):
      * ``widget`` (default) - a module-authored React component, addressed by
        ``module_id`` + ``widget_id`` (the original, pre-existing shape).
      * ``viz`` - a generic visualization bound to a module **dataset**,
        addressed by ``dataset_ref`` + ``viz_id`` + ``mapping``.
    Items stored before ``kind`` existed deserialize as ``widget`` (the default)
    and keep working, so no migration is needed."""

    i: str  # stable client id for this placement
    kind: Literal["widget", "viz"] = "widget"
    # widget cards: both required when kind == "widget".
    module_id: str | None = None
    widget_id: str | None = None
    # viz cards: dataset_ref is required at drop; viz_id/mapping are filled in
    # when the user configures the card (a freshly dropped viz card renders an
    # "unconfigured" state until then).
    dataset_ref: DatasetRef | None = None
    viz_id: str | None = None
    # Field-mapping: viz field key -> column id (or list). Opaque to the backend.
    mapping: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    # NOTE: these bounds are deliberately WIDER than the 12-column grid.
    #
    # This model parses inbound payloads as well as stored blobs, and board
    # export files written before v2 carry geometry in up to 60 columns. Those
    # files live on users' disks forever, so validating them against the new
    # grid would 422 every legacy import - and there is no way to tell a legacy
    # file from a legacy client, since they are the same payload shape.
    #
    # So the *schema* accepts the widest geometry that was ever legal, and the
    # 12-column invariant is enforced at write instead: `layout_blob` coerces
    # and clamps every item, so nothing outside the grid is ever stored. The
    # bounds here remain finite purely as an abuse guard.
    x: int = Field(default=0, ge=0, le=LEGACY_MAX_COLS - 1)
    y: int = Field(default=0, ge=0, le=MAX_ROW)
    w: int = Field(default=6, ge=1, le=LEGACY_MAX_COLS)
    h: int = Field(default=8, ge=1, le=48)
    # Per-placement card options (e.g. which metric a chart shows, or a viz's
    # non-field options). Opaque to the backend; the widget/viz interprets it.
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "widget":
            if not self.module_id or not self.widget_id:
                raise ValueError("A widget card requires module_id and widget_id.")
        elif self.kind == "viz" and self.dataset_ref is None:
            raise ValueError("A viz card requires dataset_ref.")
        return self


class DashboardSummary(BaseModel):
    """List-view row - no card payload, just enough for the grid of boards."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    event_log_id: str | None = None
    log_model: LogModel = "case_centric"
    card_count: int = 0
    updated_at: UtcDateTime


class DashboardDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    event_log_id: str | None = None
    log_model: LogModel = "case_centric"
    items: list[DashboardItem] = Field(default_factory=list)
    settings: CanvasSettings = Field(default_factory=CanvasSettings)
    created_at: UtcDateTime
    updated_at: UtcDateTime
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
    # When set, the board is seeded from a curated starter template: the
    # template's ``items``/``settings``/``log_model`` win over the (empty) fields
    # above. ``None`` (the default) keeps the classic blank-board behaviour, so
    # this is backward compatible. An unknown id is a 404 at create time.
    template_id: str | None = None

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


class DashboardTemplate(BaseModel):
    """One curated starter board in the "start from template" picker. The card
    payload is seeded server-side at create time, so this listing shape carries
    only the label + a ``card_count`` preview (not the ``items`` themselves)."""

    id: str
    name: str
    description: str
    log_model: LogModel = "case_centric"
    card_count: int = 0
