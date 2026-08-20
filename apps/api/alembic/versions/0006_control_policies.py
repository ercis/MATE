"""admin-vs-user control policies - control_policies table

Revision ID: 0006_control_policies
Revises: 0005_log_module_processing
Create Date: 2026-06-23

Adds the ``control_policies`` table - the generic backbone of the admin control
framework. Each controllable setting (``scope="setting"``) or installed module
(``scope="module"``) gets one row keyed by ``(scope, key)``. ``control_mode``
flips between ``"user"`` (default; per-user value as before) and ``"admin"``
(the single ``admin_value_json`` is shared across all users). ``updated_by`` is
a SET NULL FK to ``users.id`` so deleting the admin who set a policy leaves the
policy intact.

No backfill (absence of a row == ``control_mode="user"``). Guarded on the live
schema in the idempotent style of the squashed baseline so a half-applied boot
recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_control_policies"
down_revision: str | None = "0005_log_module_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "control_policies" not in existing:
        op.create_table(
            "control_policies",
            sa.Column("scope", sa.String(length=16), primary_key=True),
            sa.Column("key", sa.String(length=160), primary_key=True),
            sa.Column(
                "control_mode",
                sa.String(length=8),
                server_default="user",
                nullable=False,
            ),
            sa.Column("admin_value_json", sa.JSON(), nullable=True),
            sa.Column(
                "updated_by",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_control_policies_scope", "control_policies", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_control_policies_scope", table_name="control_policies")
    op.drop_table("control_policies")
