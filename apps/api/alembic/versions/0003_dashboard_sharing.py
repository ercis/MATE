"""dashboard sharing - teams, team members, dashboard shares

Revision ID: 0003_dashboard_sharing
Revises: 0002_system_settings
Create Date: 2026-06-21

Adds the three tables behind dashboard sharing:

  - ``teams``            - admin-managed groups used as a coarse share target.
  - ``team_members``     - membership edges (composite PK).
  - ``dashboard_shares`` - a read grant for one dashboard to one user or team.

A share is the only sanctioned cross-account path (see ``mate.api.sharing``);
everything else stays strictly per-user. Guarded on the live schema in the same
idempotent style as the squashed baseline so a half-applied boot recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_dashboard_sharing"
down_revision: str | None = "0002_system_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "teams" not in existing:
        op.create_table(
            "teams",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )

    if "team_members" not in existing:
        op.create_table(
            "team_members",
            sa.Column(
                "team_id",
                sa.String(length=36),
                sa.ForeignKey("teams.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_team_members_user", "team_members", ["user_id"])

    if "dashboard_shares" not in existing:
        op.create_table(
            "dashboard_shares",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "dashboard_id",
                sa.String(length=36),
                sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
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
        op.create_index("ix_dashboard_shares_dashboard", "dashboard_shares", ["dashboard_id"])
        op.create_index("ix_dashboard_shares_target_user", "dashboard_shares", ["target_user_id"])
        op.create_index("ix_dashboard_shares_target_team", "dashboard_shares", ["target_team_id"])


def downgrade() -> None:
    for table in ("dashboard_shares", "team_members", "teams"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
