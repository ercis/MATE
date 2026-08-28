"""scopes on api_tokens

Revision ID: 0008_api_token_scopes
Revises: 0007_api_tokens
Create Date: 2026-06-29

Adds ``api_tokens.scopes`` (JSON list of granted OAuth-style scopes). Empty
list == all read scopes (back-compat). Guarded on the live schema in the
idempotent style of the squashed baseline.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_api_token_scopes"
down_revision: str | None = "0007_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("api_tokens")}
    if "scopes" not in cols:
        op.add_column(
            "api_tokens",
            sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_column("api_tokens", "scopes")
