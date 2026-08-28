"""Pydantic schemas for the object-centric (OCEL) data endpoints.

The object-centric counterpart to ``schemas/event_log_data.py``. These back the
/ocel/* routes that serve an OCEL log's overview, object types, objects, events,
and event↔object relationships.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from mate.api.schemas.common import UtcDateTime


class OcelObjectTypeEntry(BaseModel):
    type: str
    count: int


class OcelOverview(BaseModel):
    events_count: int
    objects_count: int
    object_types_count: int
    relations_count: int
    date_min: UtcDateTime | None = None
    date_max: UtcDateTime | None = None
    object_types: list[OcelObjectTypeEntry]
    activities: list[str]


class _OcelPage(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    total: int
    offset: int
    limit: int


class OcelObjectsPage(_OcelPage):
    pass


class OcelEventsPage(_OcelPage):
    pass


class OcelRelationsPage(_OcelPage):
    pass
