"""OCEL 2.0 export of the behaviour-tracking UI log.

Builds a pm4py :class:`OCEL` from the ``analytics_events`` /
``analytics_objects`` / ``analytics_event_objects`` / ``analytics_object_relations``
tables and writes it in the three ocel-standard.org 2.0 interchange formats
(JSON / SQLite / XML) via pm4py's exporters - the same library Mate's own OCEL
*import* uses (``ingest/ocel.py``), so an exported UI log round-trips straight
back into the platform as an event log.

The mapping follows Abb & Rehse (Information Systems 124 (2024) 102386),
Section 7.2: the activity is the event, its action type / input value are
event attributes; ui_element, ui_group, application, system, user, and task
(plus platform resources for server events) are objects with qualified E2O
relations; the UI hierarchy is static O2O ``part_of`` rows.

Cross-user note (admin export): object ids are page/selector digests, so the
same on-screen element observed by two users merges into one OCEL object -
semantically what object-centricity wants; ``user:*`` objects stay distinct
per anon seed.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import (
    AnalyticsEvent,
    AnalyticsEventObject,
    AnalyticsObject,
    AnalyticsObjectRelation,
)

OcelFormat = Literal["json", "sqlite", "xml"]

_MEDIA_TYPES: dict[OcelFormat, str] = {
    "json": "application/json",
    "sqlite": "application/x-sqlite3",
    "xml": "application/xml",
}


@dataclass
class UiLogFrame:
    """Raw rows for one export, loaded async and assembled sync in a thread."""

    events: list[AnalyticsEvent]
    e2o: list[tuple[int, str, str]]  # (event_id, object_id, qualifier)
    objects: dict[str, tuple[str, dict[str, Any] | None]]  # id -> (type, attrs)
    o2o: list[tuple[str, str, str]]  # (src, tgt, qualifier)


async def load_ui_log(session: AsyncSession, filters: list[ColumnElement[bool]]) -> UiLogFrame:
    """Load the filtered event set plus exactly the objects/relations it touches.

    ``filters`` are ``AnalyticsEvent`` predicates (per-user exports pass a
    ``user_id`` filter; the admin export passes its shared filter set). E2O
    rows come from a join so no oversized ``IN`` lists hit SQLite.
    """
    events = list(
        (
            await session.execute(
                select(AnalyticsEvent)
                .where(*filters)
                .order_by(AnalyticsEvent.occurred_at.asc(), AnalyticsEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not events:
        return UiLogFrame(events=[], e2o=[], objects={}, o2o=[])

    e2o_rows = (
        await session.execute(
            select(
                AnalyticsEventObject.event_id,
                AnalyticsEventObject.object_id,
                AnalyticsEventObject.qualifier,
            )
            .join(AnalyticsEvent, AnalyticsEvent.id == AnalyticsEventObject.event_id)
            .where(*filters)
        )
    ).all()
    e2o = [(int(eid), str(oid), str(q)) for eid, oid, q in e2o_rows]

    user_ids = {ev.user_id for ev in events}
    object_rows = (
        await session.execute(
            select(
                AnalyticsObject.object_id,
                AnalyticsObject.object_type,
                AnalyticsObject.attrs,
            ).where(AnalyticsObject.user_id.in_(user_ids))
        )
    ).all()
    relation_rows = (
        await session.execute(
            select(
                AnalyticsObjectRelation.src_object_id,
                AnalyticsObjectRelation.tgt_object_id,
                AnalyticsObjectRelation.qualifier,
            ).where(AnalyticsObjectRelation.user_id.in_(user_ids))
        )
    ).all()

    # Keep only objects reachable from the filtered events (directly via E2O,
    # or one hop up the part_of hierarchy so exported groups/apps resolve).
    referenced = {oid for _, oid, _q in e2o}
    o2o: list[tuple[str, str, str]] = []
    for _ in range(6):  # hierarchy depth is tiny; expand until fixpoint
        added = False
        for src, tgt, _q in relation_rows:
            if str(src) in referenced and str(tgt) not in referenced:
                referenced.add(str(tgt))
                added = True
        if not added:
            break
    for src, tgt, q in relation_rows:
        if str(src) in referenced and str(tgt) in referenced:
            o2o.append((str(src), str(tgt), str(q)))

    objects: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for oid, otype, attrs in object_rows:
        key = str(oid)
        if key in referenced and key not in objects:
            objects[key] = (str(otype), attrs if isinstance(attrs, dict) else None)

    return UiLogFrame(events=events, e2o=e2o, objects=objects, o2o=sorted(set(o2o)))


def _activity(ev: AnalyticsEvent) -> str:
    """Paper 5.2.1: activity name = f(action type, target object identifier)."""
    props = ev.properties if isinstance(ev.properties, dict) else {}
    explicit = props.get("activity")
    if isinstance(explicit, str) and explicit:
        return explicit[:200]
    return ev.event_name


def _action_type(ev: AnalyticsEvent) -> str:
    props = ev.properties if isinstance(ev.properties, dict) else {}
    kind = props.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    return ev.event_type


def build_ocel(frame: UiLogFrame) -> Any:
    """Assemble the pm4py OCEL (sync + pandas-heavy - call in a threadpool)."""
    import pandas as pd
    from pm4py.objects.ocel.obj import OCEL

    ev_index: dict[int, AnalyticsEvent] = {ev.id: ev for ev in frame.events}

    def _ts(ev: AnalyticsEvent) -> datetime:
        return ev.occurred_at.replace(tzinfo=UTC)

    events_df = pd.DataFrame(
        [
            {
                "ocel:eid": str(ev.id),
                "ocel:activity": _activity(ev),
                "ocel:timestamp": _ts(ev),
                "action_type": _action_type(ev),
                "input_value": (ev.properties or {}).get("input_value")
                if isinstance(ev.properties, dict)
                else None,
                "event_type": ev.event_type,
                "source": ev.source,
                "path": ev.path,
                "session_id": ev.session_id,
                "duration_ms": ev.duration_ms,
                "props_json": json.dumps(ev.properties, default=str) if ev.properties else None,
            }
            for ev in frame.events
        ]
    )

    attr_keys: set[str] = set()
    for _otype, attrs in frame.objects.values():
        if attrs:
            attr_keys.update(k for k, v in attrs.items() if isinstance(v, (str, int, float)))
    objects_df = pd.DataFrame(
        [
            {
                "ocel:oid": oid,
                "ocel:type": otype,
                **{k: (attrs or {}).get(k) for k in sorted(attr_keys)},
            }
            for oid, (otype, attrs) in frame.objects.items()
        ]
    )

    relations_df = pd.DataFrame(
        [
            {
                "ocel:eid": str(eid),
                "ocel:activity": _activity(ev_index[eid]),
                "ocel:timestamp": _ts(ev_index[eid]),
                "ocel:oid": oid,
                "ocel:type": frame.objects[oid][0],
                "ocel:qualifier": qualifier,
            }
            for eid, oid, qualifier in frame.e2o
            if eid in ev_index and oid in frame.objects
        ]
    )

    o2o_df = pd.DataFrame(
        [{"ocel:oid": src, "ocel:oid_2": tgt, "ocel:qualifier": q} for src, tgt, q in frame.o2o]
    )

    return OCEL(
        events=events_df,
        objects=objects_df,
        relations=relations_df,
        o2o=o2o_df if not o2o_df.empty else None,
    )


def write_ocel_tmp(ocel: Any, fmt: OcelFormat) -> Path:
    """Write ``ocel`` to a private temp file in ``fmt``; caller deletes it."""
    import pm4py

    fd, tmp_name = tempfile.mkstemp(prefix="ui-log-ocel-", suffix=f".{fmt}")
    os.close(fd)
    path = Path(tmp_name)
    path.chmod(0o600)
    if fmt == "json":
        pm4py.write_ocel2_json(ocel, str(path))
    elif fmt == "sqlite":
        pm4py.write_ocel2_sqlite(ocel, str(path))
    else:
        pm4py.write_ocel2_xml(ocel, str(path))
    return path


def media_type(fmt: OcelFormat) -> str:
    return _MEDIA_TYPES[fmt]


def download_name(fmt: OcelFormat) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"ui-log-{ts}.ocel.{fmt}"
