"""Pydantic schemas for the bus topics this module emits (§5.7a).

The platform's bus stamps `user_id` (and a `log_id` hint) onto every payload, so
the model is permissive (`extra="allow"`) to accept those routing keys without a
schema conflict.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SimulationCompleted(BaseModel):
    """`agentsimulator.simulation.completed` - a simulation run finished."""

    model_config = ConfigDict(extra="allow")

    log_id: str
    num_simulations: int
    mode: str  # autonomous | orchestrated | main_results
    ngd_mean: float | None = None
    runtime_seconds: float | None = None


EVENT_SCHEMAS: dict[str, type[BaseModel]] = {
    "agentsimulator.simulation.completed": SimulationCompleted,
}
