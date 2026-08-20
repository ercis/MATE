"""Generic (case-centric) JSON ingestion.

A plain ``.json`` upload that is *not* OCEL is treated as an ordinary event log:
a list of event records, each a flat bag of scalar fields. Two common shapes:

1. A top-level array::

       [{"case_id": "c-1", "activity": "register", "timestamp": "..."}, ...]

2. An object wrapping the array under a key (``events`` / ``cases`` / ``log`` /
   ``data`` / ``records``)::

       {"events": [{"case_id": "c-1", ...}, ...]}

This module probes the file (`probe_json`) and parses it (`parse_json`), mirroring
the generic-XML pipeline so the result slots into the shared normalise →
role-resolve path in ``dispatch``. OCEL-shaped JSON is detected upstream in
``detect`` and never reaches here; `probe_json` still reports it (``format_hint:
"ocel"``) so the frontend can skip the mapping wizard.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mate.api.ingest.detect import looks_like_ocel_json
from mate.api.schemas.event_logs import JsonColumnMapping

# Object keys that, when present, are the most likely home of the event array.
_ARRAY_KEY_PREFERENCE = ("events", "event_log", "cases", "log", "records", "data", "items")

# bool is a subclass of int, so it's covered by this tuple.
_SCALAR = (str, int, float, bool)


def _load(path: Path) -> Any:
    with path.open("rb") as fh:
        return json.load(fh)


def _locate_events(doc: Any, event_path: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Find the array of event records in a parsed JSON document.

    Returns ``(records, resolved_path)`` where ``resolved_path`` is the dict key
    the array was found under (``None`` for a top-level array). When
    ``event_path`` is given it's honoured outright.
    """
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)], None
    if not isinstance(doc, dict):
        return [], None

    if event_path:
        value = doc.get(event_path)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)], event_path
        return [], event_path

    # Prefer conventionally-named keys, then fall back to the largest
    # list-of-dicts anywhere at the top level.
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    for key, value in doc.items():
        if isinstance(value, list):
            records = [r for r in value if isinstance(r, dict)]
            if records:
                candidates.append((key, records))
    if not candidates:
        return [], None
    for preferred in _ARRAY_KEY_PREFERENCE:
        for key, records in candidates:
            if key.lower() == preferred:
                return records, key
    best = max(candidates, key=lambda kv: len(kv[1]))
    return best[1], best[0]


def _record_fields(record: dict[str, Any]) -> dict[str, str]:
    """Flatten one event record into ``{field: value}``, keeping scalar fields
    only (nested objects / arrays are dropped, mirroring the XML leaf rule)."""
    out: dict[str, str] = {}
    for key, value in record.items():
        if value is None:
            continue
        if isinstance(value, _SCALAR):
            out[str(key)] = str(value)
    return out


def probe_json(path: Path, *, max_events: int = 200) -> dict[str, Any]:
    """Describe a JSON file for the mapping wizard.

    Shape mirrors `xml_parser.probe_xml`: ``format_hint`` is ``"ocel"`` (skip the
    wizard) or ``"generic"``; for generic logs ``event_path`` / ``fields`` drive
    the field picker.
    """
    if looks_like_ocel_json(path):
        return {"format_hint": "ocel", "event_path": None, "events_sampled": 0, "fields": []}

    doc = _load(path)
    records, resolved = _locate_events(doc, None)
    if not records:
        return {
            "format_hint": "generic",
            "event_path": resolved,
            "events_sampled": 0,
            "fields": [],
        }

    samples: dict[str, list[str]] = {}
    presence: dict[str, int] = {}
    seen = 0
    for record in records[:max_events]:
        fields = _record_fields(record)
        for name, value in fields.items():
            presence[name] = presence.get(name, 0) + 1
            bucket = samples.setdefault(name, [])
            if len(bucket) < 3 and value not in bucket:
                bucket.append(value)
        seen += 1

    fields_summary = [
        {"name": name, "coverage": round(count / max(1, seen), 3), "samples": samples.get(name, [])}
        for name, count in sorted(presence.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "format_hint": "generic",
        "event_path": resolved,
        "events_sampled": seen,
        "fields": fields_summary,
    }


def autodetect_mapping(probe: dict[str, Any]) -> JsonColumnMapping | None:
    """Best-effort: reuse CSV's canonical-name heuristics on the probed fields."""
    from mate.api.ingest.csv_parser import AUTODETECT_CANDIDATES, normalise_ident

    fields = [f.get("name") for f in probe.get("fields") or [] if isinstance(f, dict)]
    fields = [f for f in fields if isinstance(f, str)]
    if not fields:
        return None

    norm_pairs = [(name, normalise_ident(name)) for name in fields]
    claimed: set[str] = set()
    found: dict[str, str] = {}

    def _find(candidates: list[str], predicate: Callable[[str, str], bool]) -> str | None:
        for cand in candidates:
            cand_norm = normalise_ident(cand)
            if not cand_norm:
                continue
            for raw, norm in norm_pairs:
                if raw in claimed:
                    continue
                if predicate(norm, cand_norm):
                    return raw
        return None

    for canonical, candidates in AUTODETECT_CANDIDATES.items():
        match = _find(candidates, lambda h, c: h == c)
        if match is not None:
            found[canonical] = match
            claimed.add(match)
    for canonical, candidates in AUTODETECT_CANDIDATES.items():
        if canonical in found:
            continue
        match = _find(candidates, lambda h, c: c in h or h in c)
        if match is not None:
            found[canonical] = match
            claimed.add(match)

    if not {"case_id", "activity", "timestamp"}.issubset(found):
        return None
    return JsonColumnMapping(
        event_path=probe.get("event_path"),
        case_id=found["case_id"],
        activity=found["activity"],
        timestamp=found["timestamp"],
        end_timestamp=found.get("end_timestamp"),
        resource=found.get("resource"),
        cost=found.get("cost"),
    )


def parse_json(
    path: Path,
    mapping: JsonColumnMapping | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], JsonColumnMapping]:
    """Parse ``path`` into event rows.

    Returns ``(rows, detected_schema, effective_mapping)``. When ``mapping`` is
    omitted we autodetect; if that can't resolve the three mandatory roles we
    still return the raw fields so the central resolver in ``dispatch`` can map
    them (and flag the log for review).
    """
    doc = _load(path)
    event_path = mapping.event_path if mapping else None
    records, resolved = _locate_events(doc, event_path)
    if not records:
        raise ValueError(
            "Could not find an array of event records in the JSON file. Submit a "
            "`json_mapping` naming the array key and canonical columns."
        )

    effective = mapping
    if effective is None:
        probe = probe_json(path)
        effective = autodetect_mapping(probe)
        if effective is None:
            # Leave the role columns raw - empty role names never match a real
            # field, so nothing is renamed and the resolver maps them centrally.
            effective = JsonColumnMapping(
                event_path=resolved, case_id="", activity="", timestamp=""
            )

    rename: dict[str, str] = {
        effective.case_id: "case_id",
        effective.activity: "activity",
        effective.timestamp: "timestamp",
    }
    if effective.end_timestamp:
        rename[effective.end_timestamp] = "end_timestamp"
    if effective.resource:
        rename[effective.resource] = "resource"
    if effective.cost:
        rename[effective.cost] = "cost"
    for src, canonical in effective.extra.items():
        rename[src] = canonical

    rows: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for record in records:
        fields = _record_fields(record)
        seen_fields.update(fields)
        row: dict[str, Any] = {}
        for src, value in fields.items():
            row[rename.get(src, src)] = value
        rows.append(row)

    detected = {
        "json_event_path": resolved,
        "json_fields": sorted(seen_fields),
        "canonical_columns": sorted(set(rename.values())),
        "mapping": effective.model_dump(),
    }
    return rows, detected, effective


__all__ = ["autodetect_mapping", "parse_json", "probe_json"]
