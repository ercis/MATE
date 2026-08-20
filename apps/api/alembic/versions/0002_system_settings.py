"""system-wide key/value settings

Revision ID: 0002_system_settings
Revises: 0001_initial
Create Date: 2026-06-19

Adds the ``system_settings`` table - the singleton, admin-controlled analogue of
``user_settings``. First consumer is job-runtime worker concurrency (Settings →
General → Jobs), persisted so a live change survives a restart. Guarded on the
live schema in the same idempotent style as the squashed baseline.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_system_settings"
down_revision: str | None = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "system_settings" not in existing:
        op.create_table(
            "system_settings",
            sa.Column("key", sa.String(length=128), primary_key=True),
            sa.Column("value_json", sa.JSON(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("system_settings")
