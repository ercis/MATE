"""Pydantic schemas for the Job model (subset used in phase 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

JobStatus = Literal["queued", "running", "paused", "completed", "failed", "cancelled"]


class JobDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    title: str
    subtitle: str | None = None
    module_id: str | None = None
    payload_json: dict[str, Any]
    status: JobStatus | str
    progress_current: int
    progress_total: int | None = None
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    rate: float | None = None
    eta_seconds: float | None = None
    priority: int
    parent_job_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
