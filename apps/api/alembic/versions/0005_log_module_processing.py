"""log module-processing lifecycle - process_logs.processing_import_job_id + expected_modules

Revision ID: 0005_log_module_processing
Revises: 0004_analytics_export_indexes
Create Date: 2026-06-22

Adds the two columns that back the new ``processing`` log state: a freshly
imported log is held disabled until every subscribing module finishes
precomputing against it.

  - ``processing_import_job_id`` - the import job whose child jobs are the
    module precompute runs to wait on (lets completion survive an API restart).
  - ``expected_modules`` - the module-id set, frozen at import time, that must
    all reach a terminal job before the log flips to ``ready``.

Both nullable, no backfill (existing rows are already ``ready``/``failed``).
Guarded on the live schema in the idempotent style of the squashed baseline so a
half-applied boot recovers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_log_module_processing"
down_revision: str | None = "0004_analytics_export_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("process_logs")}

    if "processing_import_job_id" not in existing:
        op.add_column(
            "process_logs",
            sa.Column("processing_import_job_id", sa.String(length=36), nullable=True),
        )
    if "expected_modules" not in existing:
        op.add_column("process_logs", sa.Column("expected_modules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("process_logs", "expected_modules")
    op.drop_column("process_logs", "processing_import_job_id")
