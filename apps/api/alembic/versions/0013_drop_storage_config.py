"""drop storage_config - the storage backend is env-configured now

Revision ID: 0013_drop_storage_config
Revises: 0012_card_policies
Create Date: 2026-07-23

The storage backend (local vs S3) used to be a DB-stored singleton row edited
via Admin → Storage. It is now configured exclusively through the platform env
(``STORAGE_MODE`` + ``STORAGE_S3_*``, see docs/S3_OFFLOAD.md), so the row - and
the Fernet-encrypted secret inside it - has no reader left. Dropping the table
deliberately discards any stored connection details; operators move them into
``.env`` before upgrading (the values are visible in the old admin UI).

``downgrade`` recreates the empty table (baseline shape) so older code can
boot; it cannot restore the discarded row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013_drop_storage_config"
down_revision: str | None = "0012_card_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "storage_config" in existing:
        op.drop_table("storage_config")


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
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
