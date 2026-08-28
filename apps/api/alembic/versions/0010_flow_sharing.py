"""flow sharing - read grants for flows (mirrors dashboard_shares)

Revision ID: 0010_flow_sharing
Revises: 0009_flows
Create Date: 2026-06-29

Adds the ``flow_shares`` table so a flow can be shared read-only with a user or
team, the same way dashboards are. Guarded on the live schema in the idempotent
style of the baseline.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010_flow_sharing"
down_revision: str | None = "0009_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "flow_shares" not in existing:
        op.create_table(
            "flow_shares",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "flow_id",
                sa.String(length=36),
                sa.ForeignKey("flows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "target_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "target_team_id",
                sa.String(length=36),
                sa.ForeignKey("teams.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "created_by",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_flow_shares_flow", "flow_shares", ["flow_id"])
        op.create_index("ix_flow_shares_target_user", "flow_shares", ["target_user_id"])
        op.create_index("ix_flow_shares_target_team", "flow_shares", ["target_team_id"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flow_shares")
