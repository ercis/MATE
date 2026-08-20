"""analytics export indexes - cross-user occurred_at / (event_type, occurred_at)

Revision ID: 0004_analytics_export_indexes
Revises: 0003_dashboard_sharing
Create Date: 2026-06-21

The admin behaviour-export filters (``routes/admin.py``) read ``analytics_events``
WITHOUT a leading ``user_id`` - every existing index on the table is
``(user_id, ...)``-leading and so can't serve those scans. Two additive indexes
cover the cross-user filter/sort paths:

  - ``ix_analytics_events_occurred``       on ``(occurred_at)``           - date
    windowing + the export's oldest-first ordering across all users.
  - ``ix_analytics_events_type_occurred``  on ``(event_type, occurred_at)`` -
    the common "one event type over a date range" filter.

Additive and idempotent (guarded on the live schema like the squashed baseline)
so a half-applied boot recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_analytics_export_indexes"
down_revision: str | None = "0003_dashboard_sharing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {ix["name"] for ix in sa.inspect(bind).get_indexes("analytics_events")}

    if "ix_analytics_events_occurred" not in existing:
        op.create_index("ix_analytics_events_occurred", "analytics_events", ["occurred_at"])
    if "ix_analytics_events_type_occurred" not in existing:
        op.create_index(
            "ix_analytics_events_type_occurred",
            "analytics_events",
            ["event_type", "occurred_at"],
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_analytics_events_type_occurred")
    op.execute("DROP INDEX IF EXISTS ix_analytics_events_occurred")
