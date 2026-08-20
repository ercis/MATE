"""initial schema - full consolidated baseline

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-29

Squashed seed: this single revision creates the complete current schema. It
folds in every migration that historically followed it (multi-user keying,
module installs, analytics source/duration, the event-log filter / column-role /
object-centric columns, dashboards, storage config, and watched folders) so a
fresh database reaches head in one step. The table shapes mirror
``mate.api.db.models`` exactly.

Idempotent by table: the dev database is bind-mounted and SQLite runs DDL
non-transactionally, so a boot that creates a table but crashes before stamping
this revision would otherwise collide ("table already exists") on the retry.
Each ``create_table`` is guarded on the live schema so the seed recovers that
stuck state and is a no-op on an already-populated DB alike.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels = None
depends_on = None


# Reverse-dependency drop order - children before parents.
_TABLES = (
    "watched_folder_files",
    "watched_folders",
    "dashboards",
    "analytics_events",
    "analytics_sessions",
    "event_edits",
    "storage_config",
    "user_settings",
    "module_layouts",
    "module_installs",
    "module_configs",
    "jobs",
    "process_logs",
    "process_folders",
    "users",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("preferred_username", sa.String(length=255), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        )

    if "process_folders" not in existing:
        op.create_table(
            "process_folders",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column(
                "parent_id",
                sa.String(length=36),
                sa.ForeignKey("process_folders.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_process_folders_user_parent",
            "process_folders",
            ["user_id", "parent_id"],
        )

    if "process_logs" not in existing:
        op.create_table(
            "process_logs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("source_format", sa.String(length=32), nullable=True),
            sa.Column("source_filename", sa.String(length=512), nullable=True),
            sa.Column(
                "log_model",
                sa.String(length=16),
                nullable=False,
                server_default="case_centric",
            ),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="importing",
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("events_count", sa.Integer(), nullable=True),
            sa.Column("cases_count", sa.Integer(), nullable=True),
            sa.Column("variants_count", sa.Integer(), nullable=True),
            sa.Column("objects_count", sa.Integer(), nullable=True),
            sa.Column("object_types_count", sa.Integer(), nullable=True),
            sa.Column("relations_count", sa.Integer(), nullable=True),
            sa.Column("date_min", sa.DateTime(), nullable=True),
            sa.Column("date_max", sa.DateTime(), nullable=True),
            sa.Column("detected_schema", sa.JSON(), nullable=True),
            sa.Column("column_roles", sa.JSON(), nullable=True),
            sa.Column(
                "mapping_needs_review",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("column_overrides", sa.JSON(), nullable=True),
            sa.Column("active_filter", sa.JSON(), nullable=True),
            sa.Column(
                "folder_id",
                sa.String(length=36),
                sa.ForeignKey("process_folders.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("imported_at", sa.DateTime(), nullable=True),
            sa.Column("last_edited_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_process_logs_user_status", "process_logs", ["user_id", "status"])
        op.create_index(
            "ix_process_logs_user_created_at",
            "process_logs",
            ["user_id", "created_at"],
        )
        op.create_index(
            "ix_process_logs_user_folder_id",
            "process_logs",
            ["user_id", "folder_id"],
        )

    if "jobs" not in existing:
        op.create_table(
            "jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("subtitle", sa.String(length=255), nullable=True),
            sa.Column("module_id", sa.String(length=64), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("progress_total", sa.Integer(), nullable=True),
            sa.Column("stage", sa.String(length=64), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("eta_seconds", sa.Float(), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "parent_job_id",
                sa.String(length=36),
                sa.ForeignKey("jobs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_jobs_user_status", "jobs", ["user_id", "status"])
        op.create_index("ix_jobs_user_type", "jobs", ["user_id", "type"])
        op.create_index("ix_jobs_user_module", "jobs", ["user_id", "module_id"])
        op.create_index("ix_jobs_user_created_at", "jobs", ["user_id", "created_at"])

    if "module_configs" not in existing:
        op.create_table(
            "module_configs",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("module_id", sa.String(length=64), primary_key=True),
            sa.Column("config_json", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "module_installs" not in existing:
        op.create_table(
            "module_installs",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("module_id", sa.String(length=64), primary_key=True),
            sa.Column("source", sa.String(length=16), nullable=True),
            sa.Column("installed_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_module_installs_module_id", "module_installs", ["module_id"])

    if "module_layouts" not in existing:
        op.create_table(
            "module_layouts",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "log_id",
                sa.String(length=36),
                sa.ForeignKey("process_logs.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("module_id", sa.String(length=64), primary_key=True),
            sa.Column("layout_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "user_settings" not in existing:
        op.create_table(
            "user_settings",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("key", sa.String(length=128), primary_key=True),
            sa.Column("value_json", sa.JSON(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "storage_config" not in existing:
        op.create_table(
            "storage_config",
            sa.Column("id", sa.String(length=16), nullable=False),
            sa.Column("mode", sa.String(length=8), nullable=False, server_default="local"),
            sa.Column("endpoint_url", sa.String(length=512), nullable=True),
            sa.Column("bucket", sa.String(length=255), nullable=True),
            sa.Column("region", sa.String(length=64), nullable=True),
            sa.Column("access_key", sa.String(length=255), nullable=True),
            sa.Column("secret_key_enc", sa.Text(), nullable=True),
            sa.Column("path_style", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("prefix", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("quota_bytes", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "event_edits" not in existing:
        op.create_table(
            "event_edits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "log_id",
                sa.String(length=36),
                sa.ForeignKey("process_logs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("row_index", sa.Integer(), nullable=False),
            sa.Column("field", sa.String(length=128), nullable=False),
            sa.Column("old_value_json", sa.JSON(), nullable=True),
            sa.Column("new_value_json", sa.JSON(), nullable=True),
            sa.Column("edited_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_event_edits_user_log_edited_at",
            "event_edits",
            ["user_id", "log_id", "edited_at"],
        )

    if "analytics_sessions" not in existing:
        op.create_table(
            "analytics_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("anon_user_id", sa.String(length=36), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("entry_path", sa.String(length=512), nullable=True),
            sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index(
            "ix_analytics_sessions_user_anon",
            "analytics_sessions",
            ["user_id", "anon_user_id"],
        )
        op.create_index(
            "ix_analytics_sessions_user_last_seen",
            "analytics_sessions",
            ["user_id", "last_seen_at"],
        )

    if "analytics_events" not in existing:
        op.create_table(
            "analytics_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("session_id", sa.String(length=36), nullable=False),
            sa.Column("anon_user_id", sa.String(length=36), nullable=False),
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                server_default="client",
            ),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("event_name", sa.String(length=128), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("path", sa.String(length=512), nullable=True),
            sa.Column("referrer", sa.String(length=512), nullable=True),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("viewport_w", sa.Integer(), nullable=True),
            sa.Column("viewport_h", sa.Integer(), nullable=True),
            sa.Column("ua_class", sa.String(length=32), nullable=True),
            sa.Column("locale", sa.String(length=16), nullable=True),
            sa.Column("tz", sa.String(length=64), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("server_received_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_analytics_events_user_session",
            "analytics_events",
            ["user_id", "session_id", "occurred_at"],
        )
        op.create_index(
            "ix_analytics_events_user_type_name",
            "analytics_events",
            ["user_id", "event_type", "event_name"],
        )
        op.create_index(
            "ix_analytics_events_user_occurred",
            "analytics_events",
            ["user_id", "occurred_at"],
        )

    if "dashboards" not in existing:
        op.create_table(
            "dashboards",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("event_log_id", sa.String(length=36), nullable=True),
            sa.Column(
                "log_model",
                sa.String(length=16),
                nullable=False,
                server_default="case_centric",
            ),
            sa.Column("layout_json", sa.JSON(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["event_log_id"], ["process_logs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_dashboards_user_created_at", "dashboards", ["user_id", "created_at"])

    if "watched_folders" not in existing:
        op.create_table(
            "watched_folders",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("dest_folder_id", sa.String(length=36), nullable=True),
            sa.Column(
                "source_path",
                sa.String(length=1024),
                nullable=False,
                server_default="",
            ),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("interval_seconds", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("default_mapping", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["dest_folder_id"], ["process_folders.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_watched_folders_user_status", "watched_folders", ["user_id", "status"])

    if "watched_folder_files" not in existing:
        op.create_table(
            "watched_folder_files",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("watch_id", sa.String(length=36), nullable=False),
            sa.Column("source_name", sa.String(length=1024), nullable=False),
            sa.Column("size", sa.Integer(), nullable=True),
            sa.Column("etag", sa.String(length=255), nullable=True),
            sa.Column("mtime", sa.Float(), nullable=True),
            sa.Column("log_id", sa.String(length=36), nullable=True),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="imported",
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("imported_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["watch_id"], ["watched_folders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["log_id"], ["process_logs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_watched_folder_files_watch_name",
            "watched_folder_files",
            ["watch_id", "source_name"],
            unique=True,
        )


def downgrade() -> None:
    # Single baseline → downgrading drops everything. SQLite removes a table's
    # indexes with the table, so an explicit index drop isn't needed.
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
