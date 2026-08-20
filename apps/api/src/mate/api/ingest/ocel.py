"""OCEL (Object-Centric Event Log) parsing via pm4py.

Reads a ``.jsonocel`` / ``.xmlocel`` / OCEL 2.0 ``.sqlite`` file into the four
canonical tables the platform persists under ``ocel/`` - events, objects,
relations (the flattened event↔object map), and o2o (object↔object). Kept
entirely separate from the case-centric XES/CSV/XML pipeline: nothing here
produces a ``case_id`` or touches the root ``events.parquet`` / ``cases.parquet``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OcelParseResult:
    events: pd.DataFrame
    objects: pd.DataFrame
    relations: pd.DataFrame
    o2o: pd.DataFrame
    # Canonical pm4py column names, read off the OCEL instance so a
    # non-default-named log still maps (e.g. event_id_column).
    columns: dict[str, str]
    detected_schema: dict[str, Any]
    stats: dict[str, Any]


def _read_ocel(path: Path, flavor: str) -> Any:
    """Load an OCEL from disk, picking the reader by ``flavor`` and falling back
    across pm4py's 2.0 / 1.0 readers (the formats are largely self-describing).

    ``flavor`` (``json`` / ``xml`` / ``sqlite``) is resolved by ``detect`` from
    the upload, so a content-detected OCEL stored under a plain ``original.json``
    / ``original.xml`` name still reads with the right reader.
    """
    import pm4py

    spath = str(path)
    if flavor == "sqlite":
        return pm4py.read_ocel2_sqlite(spath)
    if flavor == "xml":
        # XML can be OCEL 1.0 or 2.0 - try 2.0 first, fall back to 1.0.
        try:
            return pm4py.read_ocel2_xml(spath)
        except Exception:
            return pm4py.read_ocel_xml(spath)
    # json (and anything else) - prefer 2.0, fall back to 1.0.
    try:
        return pm4py.read_ocel2_json(spath)
    except Exception:
        return pm4py.read_ocel_json(spath)


def _ocel_version(flavor: str) -> str:
    if flavor == "sqlite":
        return "2.0"
    return "unknown"


def parse_ocel(path: Path, *, flavor: str = "json") -> OcelParseResult:
    """Parse an OCEL file into the four canonical DataFrames + stats.

    ``flavor`` selects the pm4py reader (``json`` / ``xml`` / ``sqlite``).
    Runs synchronously (callers wrap it in ``asyncio.to_thread``).
    """
    import pm4py

    ocel = _read_ocel(path, flavor)

    # Read canonical column names off the instance rather than hardcoding the
    # ocel:* strings, so a custom-named OCEL still maps cleanly.
    cols = {
        "event_id": ocel.event_id_column,
        "event_activity": ocel.event_activity,
        "event_timestamp": ocel.event_timestamp,
        "object_id": ocel.object_id_column,
        "object_type": ocel.object_type_column,
        "qualifier": ocel.qualifier,
    }

    events: pd.DataFrame = ocel.events.copy()
    objects: pd.DataFrame = ocel.objects.copy()
    relations: pd.DataFrame = ocel.relations.copy()
    o2o: pd.DataFrame = (
        ocel.o2o.copy()
        if getattr(ocel, "o2o", None) is not None
        else pd.DataFrame(columns=[cols["object_id"], f"{cols['object_id']}_2", cols["qualifier"]])
    )

    if events.empty:
        raise ValueError("OCEL file contained zero events.")

    object_types = pm4py.ocel_get_object_types(ocel)
    # Total objects per type, taken straight from the objects table (pm4py's
    # ocel_objects_ot_count is a per-event breakdown, not per-type totals).
    ot_counts = objects[cols["object_type"]].value_counts().to_dict()
    object_type_entries = [
        {"type": str(t), "count": int(ot_counts.get(t, 0))} for t in sorted(object_types)
    ]
    activities = sorted(str(a) for a in events[cols["event_activity"]].dropna().unique())

    # Event/object attribute columns = everything beyond the canonical keys.
    event_attributes = [
        c
        for c in events.columns
        if c not in {cols["event_id"], cols["event_activity"], cols["event_timestamp"]}
    ]
    object_attributes = [
        c for c in objects.columns if c not in {cols["object_id"], cols["object_type"]}
    ]

    stats = {
        "events_count": len(events),
        "objects_count": len(objects),
        "object_types_count": len(object_types),
        "relations_count": len(relations),
    }

    # OCEL detected_schema is intentionally shaped differently from the
    # case-centric one (columns under `ocel_columns`, top-level `columns` unset)
    # so nothing downstream confuses the two models.
    detected_schema: dict[str, Any] = {
        "log_model": "object_centric",
        "object_types": object_type_entries,
        "activities": activities,
        "event_attributes": event_attributes,
        "object_attributes": object_attributes,
        "ocel_columns": {
            "events": list(events.columns),
            "objects": list(objects.columns),
            "relations": list(relations.columns),
            "o2o": list(o2o.columns),
        },
        "canonical_columns": cols,
        "counts": stats,
        "ocel_version": _ocel_version(flavor),
        "ocel_flavor": flavor,
    }

    log.info(
        "ocel.parsed",
        path=str(path),
        events=stats["events_count"],
        objects=stats["objects_count"],
        object_types=stats["object_types_count"],
        relations=stats["relations_count"],
    )

    return OcelParseResult(
        events=events,
        objects=objects,
        relations=relations,
        o2o=o2o,
        columns=cols,
        detected_schema=detected_schema,
        stats=stats,
    )


__all__ = ["OcelParseResult", "parse_ocel"]
