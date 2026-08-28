"""Pydantic v2 schemas for the /watched-folders API surface.

A watched folder is a persistent, auto-scanned import source - see
``mate.api.routes.watched_folders`` and ``mate.api.ingest.watch``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mate.api.schemas.common import UtcDateTime

WatchMode = Literal["manual", "interval", "continuous"]
WatchStatus = Literal["active", "paused", "error"]

# Floor on the interval mode so a user can't poll the backend pathologically often.
MIN_INTERVAL_SECONDS = 30


class WatchedFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Empty ⇒ Mate-managed default location under the user's storage area.
    source_path: str | None = Field(default=None, max_length=1024)
    mode: WatchMode = "manual"
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    # Optional {"csv_mapping": {...}, "xml_mapping": {...}, "json_mapping": {...}}.
    default_mapping: dict[str, Any] | None = None
    # Create a new destination folder named after the watch (default), or reuse
    # an existing one via dest_folder_id.
    create_dest_folder: bool = True
    dest_folder_id: str | None = None

    @model_validator(mode="after")
    def _require_interval(self) -> WatchedFolderCreate:
        if self.mode == "interval" and self.interval_seconds is None:
            raise ValueError("interval_seconds is required when mode is 'interval'.")
        return self


class WatchedFolderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    mode: WatchMode | None = None
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    # Pause/resume - only the two user-settable states (never "error").
    status: Literal["active", "paused"] | None = None
    default_mapping: dict[str, Any] | None = None


class WatchedFolderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    source_path: str
    mode: WatchMode | str
    interval_seconds: int | None = None
    status: WatchStatus | str
    dest_folder_id: str | None = None
    last_scanned_at: UtcDateTime | None = None
    last_error: str | None = None
    created_at: UtcDateTime
    # Ledger rollup (filled by the route, not the ORM).
    imported_count: int = 0
    failed_count: int = 0


class WatchedFileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_name: str
    status: str
    size: int | None = None
    log_id: str | None = None
    error: str | None = None
    imported_at: UtcDateTime


class WatchedFolderDetail(WatchedFolderSummary):
    default_mapping: dict[str, Any] | None = None
    files: list[WatchedFileSummary] = Field(default_factory=list)


class ScanResponse(BaseModel):
    found: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
