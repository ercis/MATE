"""per-user API tokens (PAT) - api_tokens table

Revision ID: 0007_api_tokens
Revises: 0006_control_policies
Create Date: 2026-06-29

Adds the ``api_tokens`` table backing per-user personal access tokens, the
machine-to-machine credential an external MCP client presents to ``/mcp``.
Only the blake2b ``token_hash`` is stored (unique); the plaintext is shown
once at creation. Guarded on the live schema in the idempotent style of the
squashed baseline so a half-applied boot recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_api_tokens"
down_revision: str | None = "0006_control_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "api_tokens" not in existing:
        op.create_table(
            "api_tokens",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
            sa.Column("token_prefix", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("revoked", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
        op.create_index("ix_api_tokens_user", "api_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user", table_name="api_tokens")
    op.drop_table("api_tokens")
