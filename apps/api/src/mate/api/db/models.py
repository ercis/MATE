"""SQLAlchemy ORM models for the metadata SQLite database.

Schema follows INSTRUCTIONS.md §7.9.5 (Job model fields) and §3.3 (process logs
metadata). Module-related tables are scaffolded here even though they are
populated by phase 5 - the column shape is fixed in v1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class User(Base):
    """Local mirror of Keycloak users.

    Populated JIT on the first authenticated request from a new `sub`. The
    `id` column is the Keycloak `sub` claim (UUID) and the FK target for
    every per-user table below.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    preferred_username: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Folder(Base):
    """Hierarchical folder for organising event logs on /processes.

    Folders can nest arbitrarily; `parent_id` is null for top-level folders.
    `position` orders siblings within the same parent (lower = first).
    """

    __tablename__ = "process_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("process_folders.id", ondelete="CASCADE"),
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_process_folders_user_parent", "user_id", "parent_id"),)


class EventLog(Base):
    """A user-facing process log. The `id` is also the directory name in
    `data/event_logs/{id}/` and the URL identifier in `/processes/{logId}`.
    """

    __tablename__ = "process_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    source_format: Mapped[str | None] = mapped_column(String(32))
    source_filename: Mapped[str | None] = mapped_column(String(512))

    # The log's data model - the single isolation switch between case-centric
    # (XES/CSV/XML → events.parquet keyed by case_id) and object-centric (OCEL →
    # ocel/*.parquet). A log is exactly one model; the two never mix. Defaults to
    # "case_centric" so every pre-OCEL row stays case-centric.
    log_model: Mapped[str] = mapped_column(
        String(16), default="case_centric", server_default="case_centric", nullable=False
    )

    status: Mapped[str] = mapped_column(String(16), default="importing", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    # While `status == "processing"`, the log is held disabled until every
    # subscribing module finishes precomputing against it. The import job whose
    # children are the precompute jobs to wait on, and the module-id set frozen
    # at import time (deterministic - avoids a "0/0 → flip early" race). Both are
    # cleared the moment the log goes `ready`. See `mate.api.modules.processing`.
    processing_import_job_id: Mapped[str | None] = mapped_column(String(36))
    expected_modules: Mapped[list[str] | None] = mapped_column(JSON)

    events_count: Mapped[int | None] = mapped_column(Integer)
    # Case-centric counts - left NULL for object-centric logs (their NULLness is
    # itself a tell that the case-centric path never ran).
    cases_count: Mapped[int | None] = mapped_column(Integer)
    variants_count: Mapped[int | None] = mapped_column(Integer)
    # Object-centric counts - left NULL for case-centric logs.
    objects_count: Mapped[int | None] = mapped_column(Integer)
    object_types_count: Mapped[int | None] = mapped_column(Integer)
    relations_count: Mapped[int | None] = mapped_column(Integer)
    date_min: Mapped[datetime | None] = mapped_column(DateTime)
    date_max: Mapped[datetime | None] = mapped_column(DateTime)

    detected_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Resolved canonical column mapping: role ("case_id"/"activity"/"timestamp"/
    # optional) → the source column it was taken from. Set by the importer and
    # editable from the log's settings ("Column roles"), which re-imports.
    column_roles: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # True when the importer had to *guess* one of the mandatory roles (a fuzzy
    # or type-based match, not an exact header). Surfaces a "review mapping"
    # warning in the process overview until the user confirms it in settings.
    mapping_needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    description: Mapped[str | None] = mapped_column(Text)
    column_overrides: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Applied Events-tab filter: a JSON array of {field, op, value?} entries.
    # NULL/[] means the full dataset. Every non-editor consumer (Variants /
    # Activities / Data-quality and all modules) reads through this overlay.
    active_filter: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    folder_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("process_folders.id", ondelete="SET NULL"),
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_process_logs_user_status", "user_id", "status"),
        Index("ix_process_logs_user_created_at", "user_id", "created_at"),
        Index("ix_process_logs_user_folder_id", "user_id", "folder_id"),
    )


class WatchedFolder(Base):
    """A persistent import *source* - a storage location scanned over time.

    Unlike the one-shot upload paths, a watched folder points at a location in
    the active storage backend (an S3 key prefix in S3 mode, a filesystem path in
    local mode) that an external pipeline fills. Mate lists it on a cadence and
    imports any new/changed file through the normal `event_log.import` job,
    landing the result in `dest_folder_id`. Source files are never moved or
    deleted; the `watched_folder_files` ledger records what has been imported so
    a file isn't re-imported unless its fingerprint changes.
    """

    __tablename__ = "watched_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # `/processes` folder imported logs land in (created at watch creation).
    dest_folder_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("process_folders.id", ondelete="SET NULL")
    )

    # Location to scan, interpreted against the active backend. Empty/managed
    # defaults to `users/{user_id}/watched/{id}`; an S3 prefix or filesystem path
    # otherwise. Used literally (no admin-prefix prepended) so a watch can point
    # at an existing location an upstream pipeline already writes to.
    source_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)

    # Scan cadence: "manual" (only via /scan), "interval" (every
    # interval_seconds), "continuous" (a fixed fast cadence). Only the latter two
    # are picked up by the background poller.
    mode: Mapped[str] = mapped_column(
        String(16), default="manual", server_default="manual", nullable=False
    )
    interval_seconds: Mapped[int | None] = mapped_column(Integer)

    # "active" | "paused" | "error". Paused watches are skipped by the poller but
    # can still be scanned manually.
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    # Optional column mapping forced on every imported file (unattended imports
    # can't show the wizard). NULL ⇒ rely on the importer's autodetect.
    default_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_watched_folders_user_status", "user_id", "status"),)


class WatchedFolderFile(Base):
    """Dedup ledger: one row per source file a watched folder has seen.

    The fingerprint (size + etag for S3 / mtime for local) lets a scan skip files
    already imported and re-import only when a file changes. Never deleted.
    """

    __tablename__ = "watched_folder_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watch_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("watched_folders.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    size: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(255))
    mtime: Mapped[float | None] = mapped_column(Float)

    log_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("process_logs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="imported", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_watched_folder_files_watch_name", "watch_id", "source_name", unique=True),
    )


class Job(Base):
    """Persisted job - see §7.9.5 / §8.

    The drawer / dock / toasts in the frontend (phase 4 and beyond) read from
    this table; for phase 3 only `import` jobs are produced.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(255))
    module_id: Mapped[str | None] = mapped_column(String(64))

    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    rate: Mapped[float | None] = mapped_column()
    eta_seconds: Mapped[float | None] = mapped_column()
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parent_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_jobs_user_status", "user_id", "status"),
        Index("ix_jobs_user_type", "user_id", "type"),
        Index("ix_jobs_user_module", "user_id", "module_id"),
        Index("ix_jobs_user_created_at", "user_id", "created_at"),
    )


class ModuleConfig(Base):
    """Per-module per-user configuration - populated by Settings → Modules."""

    __tablename__ = "module_configs"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    module_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ModuleInstall(Base):
    """Per-user record of which modules a user has installed / made available.

    Module *code* lives once on shared disk (``modules/<id>/``) and is loaded
    once into the process - true per-user code isolation is out of scope. This
    table reference-counts *ownership* so listing, availability, and deletion
    are per-user: a user only sees and can manage modules they installed, and
    the on-disk artifact is removed only when its last owner uninstalls it.
    """

    __tablename__ = "module_installs"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    module_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str | None] = mapped_column(String(16))
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_module_installs_module_id", "module_id"),)


class ModuleLayout(Base):
    """Per-user, per-(log, module) widget layout."""

    __tablename__ = "module_layouts"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    log_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("process_logs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    module_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class UserSetting(Base):
    """Free-form per-user key/value settings (Settings → General, AI, Privacy)."""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[dict[str, Any] | list[Any] | str | int | float | bool | None] = (
        mapped_column(JSON)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SystemSetting(Base):
    """Free-form *system-wide* key/value settings - the singleton analogue of
    :class:`UserSetting`.

    Unlike per-user settings these apply platform-wide and are admin-controlled
    (e.g. job-runtime worker concurrency, surfaced at Settings → General → Jobs
    and persisted so a live change survives a restart). Keyed by a plain string;
    the value is whatever JSON the setting needs.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[dict[str, Any] | list[Any] | str | int | float | bool | None] = (
        mapped_column(JSON)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ControlPolicy(Base):
    """Admin-vs-user control policy for a single server-side setting or module.

    The generic backbone of the admin control framework: every controllable
    thing (a server-side setting like ``ai.config``, or an installed module's
    config) has one row keyed by ``(scope, key)``. ``control_mode="user"`` (the
    default) means each user owns their own value as before; ``"admin"`` means
    the single ``admin_value_json`` here is the shared value used for *all*
    users, who then see a read-only "controlled by your administrator" state.

    A ``None`` ``admin_value_json`` under ``control_mode="admin"`` means
    "controlled, but the admin hasn't entered a value yet". Resolved at the
    existing per-user read chokepoints via ``mate.api.policy.resolve``. Secrets
    (e.g. AI API keys) live inside ``admin_value_json`` and are never serialized
    back out - the routes mask them, exactly like the per-user path.
    """

    __tablename__ = "control_policies"

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    control_mode: Mapped[str] = mapped_column(
        String(8), default="user", server_default="user", nullable=False
    )
    admin_value_json: Mapped[dict[str, Any] | list[Any] | str | int | float | bool | None] = (
        mapped_column(JSON)
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (Index("ix_control_policies_scope", "scope"),)


class StorageConfig(Base):
    """Global (VM-wide) storage backend configuration - a single row.

    Unlike :class:`UserSetting` this is *not* per-user: it selects where every
    user's event logs and module outputs are durably stored. ``mode="local"``
    (the default) keeps everything on disk exactly as before; ``mode="s3"``
    treats a connected S3/Ceph-RGW bucket as the primary store while local disk
    acts as a working cache (see ``mate.api.storage``). Set and edited only by
    an admin via ``/api/v1/admin/storage``. The single row is keyed by the
    constant :data:`SINGLETON_ID`.
    """

    __tablename__ = "storage_config"

    SINGLETON_ID = "singleton"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=SINGLETON_ID)
    mode: Mapped[str] = mapped_column(
        String(8), default="local", server_default="local", nullable=False
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(512))
    bucket: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(64))
    access_key: Mapped[str | None] = mapped_column(String(255))
    # Fernet ciphertext of the secret access key - never stored or returned in
    # plaintext (see ``storage/config.py``).
    secret_key_enc: Mapped[str | None] = mapped_column(Text)
    # Ceph RGW and most non-AWS S3 need path-style addressing
    # (``host/bucket/key`` rather than ``bucket.host/key``).
    path_style: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    prefix: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    # Admin-entered total quota (bytes) for the storage-overview bar. Optional -
    # S3 itself doesn't report it back without admin caps the RGW user lacks.
    quota_bytes: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class EventEdit(Base):
    """Audit trail for manual cell edits made via the Events tab.

    Each row records one field change. We never delete rows from this table -
    Settings → Edit history surfaces the most recent N for a given log.
    """

    __tablename__ = "event_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    log_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("process_logs.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value_json: Mapped[Any | None] = mapped_column(JSON)
    new_value_json: Mapped[Any | None] = mapped_column(JSON)
    edited_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_event_edits_user_log_edited_at", "user_id", "log_id", "edited_at"),)


class AnalyticsSession(Base):
    """Aggregate row per browser session - one per visit/idle-timeout window.

    Updated via UPSERT on each ingested batch so `GET /analytics/summary` can
    answer "sessions in the last 30 days" without scanning the events table.
    """

    __tablename__ = "analytics_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    anon_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_path: Mapped[str | None] = mapped_column(String(512))
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_analytics_sessions_user_anon", "user_id", "anon_user_id"),
        Index("ix_analytics_sessions_user_last_seen", "user_id", "last_seen_at"),
    )


class AnalyticsEvent(Base):
    """Append-only behaviour-tracking event row.

    Capture is gated by the ``analytics.config`` UserSetting on both client
    and server. No PII is stored - see ``routes/analytics.py`` for the
    server-side enabled-gate and the explicit "never capture" list in the
    Privacy settings copy.
    """

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    anon_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # "client" for browser-emitted events (clicks, page views, web-vitals);
    # "server" for backend-emitted ones (business-op timings, job outcomes).
    source: Mapped[str] = mapped_column(
        String(16), default="client", server_default="client", nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Wall-clock duration in ms for timed server events (request handling, job
    # runtime). Null for instantaneous client events.
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[str | None] = mapped_column(String(512))
    referrer: Mapped[str | None] = mapped_column(String(512))
    properties: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    viewport_w: Mapped[int | None] = mapped_column(Integer)
    viewport_h: Mapped[int | None] = mapped_column(Integer)
    ua_class: Mapped[str | None] = mapped_column(String(32))
    locale: Mapped[str | None] = mapped_column(String(16))
    tz: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_analytics_events_user_session", "user_id", "session_id", "occurred_at"),
        Index("ix_analytics_events_user_type_name", "user_id", "event_type", "event_name"),
        Index("ix_analytics_events_user_occurred", "user_id", "occurred_at"),
        # Cross-user (no leading user_id) - serve the admin behaviour-export
        # filters in routes/admin.py (Alembic 0004).
        Index("ix_analytics_events_occurred", "occurred_at"),
        Index("ix_analytics_events_type_occurred", "event_type", "occurred_at"),
    )


class Dashboard(Base):
    """A user-built dashboard: a grid of cards drawn from any installed module.

    A dashboard binds to a single event log (`event_log_id`); every card on it
    renders against that log. `layout_json` holds the full board state - the
    placed cards and their react-grid-layout geometry - as::

        {"items": [{"i": "<uuid>", "module_id": "...", "widget_id": "...",
                    "title": "...", "x": 0, "y": 0, "w": 6, "h": 8,
                    "config": {}}]}

    Storing cards + geometry in one blob keeps a save atomic and makes the
    export/import payload a straight passthrough. The bound log is nulled (not
    cascade-deleted) when the log goes away so the board survives as a shell.
    """

    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_log_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("process_logs.id", ondelete="SET NULL")
    )
    # The board's data model, fixed at creation. Mirrors `process_logs.log_model`
    # ("case_centric" | "object_centric"): the palette only offers cards whose
    # widgets declare this model, and the log picker only lists matching logs.
    log_model: Mapped[str] = mapped_column(
        String(16), default="case_centric", server_default="case_centric", nullable=False
    )
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (Index("ix_dashboards_user_created_at", "user_id", "created_at"),)


class Team(Base):
    """A named group of users (a "workspace") used as a dashboard-share target.

    Teams are admin-managed: an operator creates a team and assigns members via
    the admin panel. A team is the coarse share target - sharing a dashboard
    with a team grants every current member read access. Soft-deleted
    (``deleted_at``) so historical shares keep a stable name to display.
    """

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class TeamMember(Base):
    """Membership edge: which users belong to which team.

    Composite PK ``(team_id, user_id)`` makes membership a set (no duplicates).
    ``role`` distinguishes a team ``owner`` (reserved for future self-service
    management) from a plain ``member``; today both are assigned by an admin.
    """

    __tablename__ = "team_members"

    team_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        String(16), default="member", server_default="member", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_team_members_user", "user_id"),)


class DashboardShare(Base):
    """A grant of read access to one dashboard for one target.

    Exactly one of ``target_user_id`` (share with a single member) or
    ``target_team_id`` (share with a whole team) is set - the route layer
    enforces the xor. ``created_by`` is the sharer (the dashboard owner) for
    audit. A share is the *only* sanctioned way a dashboard - and, transitively,
    the data of its bound event log - crosses an account boundary; see
    ``mate.api.sharing``. Read-only: recipients never mutate the dashboard or
    the log.
    """

    __tablename__ = "dashboard_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE")
    )
    target_team_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("teams.id", ondelete="CASCADE")
    )
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_dashboard_shares_dashboard", "dashboard_id"),
        Index("ix_dashboard_shares_target_user", "target_user_id"),
        Index("ix_dashboard_shares_target_team", "target_team_id"),
    )
