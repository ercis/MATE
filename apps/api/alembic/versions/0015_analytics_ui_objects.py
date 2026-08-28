"""analytics UI-log objects - OCEL object registry + O2O/E2O relation tables

Revision ID: 0015_analytics_ui_objects
Revises: 0014_dashboard_grid_12col
Create Date: 2026-07-31

Implements the object side of the Abb & Rehse reference data model for
process-related UI logs (Information Systems 124 (2024) 102386). Events stay in
``analytics_events``; three new tables materialise the object-centric view so
the log can be exported as OCEL 2.0:

  - ``analytics_objects``          - one row per observed object (ui_element,
    ui_group, application, system, user, task, job, module, log, dashboard).
  - ``analytics_object_relations`` - static O2O ``part_of`` hierarchy.
  - ``analytics_event_objects``    - E2O rows (event -> object + qualifier),
    cascading away with their event.

Additive and idempotent (guarded on the live schema like the squashed
baseline) so a half-applied boot recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015_analytics_ui_objects"
down_revision: str | None = "0014_dashboard_grid_12col"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "analytics_objects" not in existing:
        op.create_table(
            "analytics_objects",
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("object_id", sa.String(128), primary_key=True),
            sa.Column("object_type", sa.String(32), nullable=False),
            sa.Column("attrs", sa.JSON()),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_analytics_objects_user_type", "analytics_objects", ["user_id", "object_type"]
        )

    if "analytics_object_relations" not in existing:
        op.create_table(
            "analytics_object_relations",
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("src_object_id", sa.String(128), primary_key=True),
            sa.Column("tgt_object_id", sa.String(128), primary_key=True),
            sa.Column("qualifier", sa.String(64), primary_key=True),
        )
        op.create_index(
            "ix_analytics_object_relations_user_src",
            "analytics_object_relations",
            ["user_id", "src_object_id"],
        )

    if "analytics_event_objects" not in existing:
        op.create_table(
            "analytics_event_objects",
            sa.Column(
                "event_id",
                sa.Integer(),
                sa.ForeignKey("analytics_events.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("object_id", sa.String(128), primary_key=True),
            sa.Column("qualifier", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
        )
        op.create_index(
            "ix_analytics_event_objects_user_object",
            "analytics_event_objects",
            ["user_id", "object_id"],
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_event_objects")
    op.execute("DROP TABLE IF EXISTS analytics_object_relations")
    op.execute("DROP TABLE IF EXISTS analytics_objects")
