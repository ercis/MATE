"""flows - node-graph data pipelines (the schematic builder)

Revision ID: 0009_flows
Revises: 0008_api_token_scopes
Create Date: 2026-06-29

Adds the ``flows`` table: a user-built node graph (source -> module ->
transform -> viz) stored as ``graph_json``, parallel to ``dashboards``. Guarded
on the live schema in the same idempotent style as the squashed baseline so a
half-applied boot recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_flows"
down_revision: str | None = "0008_api_token_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "flows" not in existing:
        op.create_table(
            "flows",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "event_log_id",
                sa.String(length=36),
                sa.ForeignKey("process_logs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "log_model", sa.String(length=16), nullable=False, server_default="case_centric"
            ),
            sa.Column("graph_json", sa.JSON(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_flows_user_created_at", "flows", ["user_id", "created_at"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flows")
