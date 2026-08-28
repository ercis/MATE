"""Pydantic schemas for the bus topics this module emits (§5.7a).

The platform's bus stamps `user_id` (and a `log_id` hint) onto every payload, so the
model is permissive (`extra="allow"`) to accept those routing keys without a schema
conflict.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AnalysisCompleted(BaseModel):
    """`actor_performance.analysis.completed` - a decomposition run finished."""

    model_config = ConfigDict(extra="allow")

    log_id: str
    edges: int
    task_instances: int
    runtime_seconds: float | None = None


EVENT_SCHEMAS: dict[str, type[BaseModel]] = {
    "actor_performance.analysis.completed": AnalysisCompleted,
}
