from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, PlainSerializer


def as_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC.

    Every datetime we persist is naive UTC (`db.models._utcnow` strips the
    tzinfo; ingest normalizes event timestamps with ``utc=True`` +
    ``tz_localize(None)``). A naive value serialized as-is yields ISO text
    without an offset, which browsers parse as *local* wall-clock - shifting
    every displayed timestamp by the viewer's UTC offset ("imported 2 hours
    ago" right after an upload in UTC+2). Attaching UTC here makes the JSON
    carry ``Z`` so clients parse the true instant.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc_isoformat(value: datetime) -> str:
    """ISO-8601 with explicit UTC offset - for hand-built JSON payloads."""
    return as_utc(value).isoformat()


# Drop-in replacement for `datetime` in response schemas: validates identically
# but always serializes as timezone-aware UTC (``...Z`` in JSON mode). Use this
# for every datetime field that leaves the API.
UtcDateTime = Annotated[datetime, PlainSerializer(as_utc, return_type=datetime)]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
