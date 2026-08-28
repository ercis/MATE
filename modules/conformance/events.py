"""Typed bus payloads emitted by the conformance module.

Every user-scoped bus event must carry ``user_id`` - the stream fan-out filters
by it server-side, so omitting it would leak the event to every user.
"""

from __future__ import annotations

from pydantic import BaseModel


class ConformanceComputed(BaseModel):
    user_id: str
    log_id: str
    model_hash: str
    technique: str
    log_fitness: float
    precision: float
