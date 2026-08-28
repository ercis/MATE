"""drop flows - the Builder (flows) feature was removed

Revision ID: 0011_drop_flows
Revises: 0010_flow_sharing
Create Date: 2026-07-07

The node-graph Builder is gone: drops ``flow_shares`` (grants first, it FKs
into ``flows``) then ``flows``. Forward-only removal - 0009/0010 stay so
existing databases keep a linear history. ``IF EXISTS`` keeps it idempotent in
the style of the baseline. No other table references flows (dashboards held
flow references in layout JSON only).
"""

from __future__ import annotations

from alembic import op

revision: str = "0011_drop_flows"
down_revision: str | None = "0010_flow_sharing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flow_shares")
    op.execute("DROP TABLE IF EXISTS flows")


def downgrade() -> None:
    # Intentionally a no-op: the feature is removed and its data is not
    # recoverable. Re-creating the empty tables without the feature would be
    # meaningless; restore 0009/0010 semantics only by reverting the removal.
    pass
