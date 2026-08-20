"""Streaming XES (IEEE-1849) parser.

We use lxml's `iterparse` so we never hold the full XML tree in memory and so
we can report per-event progress to the job runtime. XES extension keys are
mapped to canonical column names:

    concept:name      → activity         (or case_id at the trace level)
    time:timestamp    → timestamp
    org:resource      → resource
    org:role          → role
    lifecycle:transition → lifecycle
    cost:total        → cost

Anything else is preserved as a string column with the original key.
"""

from __future__ import annotations

import gzip
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

from lxml import etree

XES_NS = "{http://www.xes-standard.org/}"
# Fluxicon Disco (and others) declare xmlns without the trailing slash, so the
# resulting element tag is `{http://www.xes-standard.org}trace`. Accept both.
XES_NS_NOSLASH = "{http://www.xes-standard.org}"

# Map XES standard keys to our canonical column names (events).
_EVENT_KEY_MAP: dict[str, str] = {
    "concept:name": "activity",
    "time:timestamp": "timestamp",
    "org:resource": "resource",
    "org:role": "role",
    "lifecycle:transition": "lifecycle",
    "cost:total": "cost",
}

# Trace-level keys we care about → these become per-event columns once flattened.
_TRACE_KEY_MAP: dict[str, str] = {
    "concept:name": "case_id",
}

ProgressCallback = Callable[[int], None]


def _iter_attribute_pairs(elem: etree._Element) -> Iterator[tuple[str, Any]]:
    """Yield (key, value) for each `<string|date|int|float|boolean>` attribute child."""
    for child in elem:
        tag = etree.QName(child).localname
        key = child.get("key")
        if key is None:
            continue
        raw = child.get("value")
        if raw is None:
            continue
        if tag == "int":
            try:
                yield key, int(raw)
            except ValueError:
                yield key, raw
        elif tag == "float":
            try:
                yield key, float(raw)
            except ValueError:
                yield key, raw
        elif tag == "boolean":
            yield key, raw.lower() == "true"
        elif tag == "date":
            yield key, _parse_xes_datetime(raw)
        else:
            yield key, raw


def _parse_xes_datetime(raw: str) -> datetime | str:
    """XES dates are ISO-8601. Accept both 'Z' and '+HH:MM' suffixes."""
    text = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return raw


def _open(path: Path) -> BinaryIO:
    if path.suffix == ".gz" or str(path).endswith(".xes.gz"):
        return cast(BinaryIO, gzip.open(path, "rb"))
    return cast(BinaryIO, path.open("rb"))


def parse_xes(
    path: Path,
    *,
    on_progress: ProgressCallback | None = None,
    progress_every: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse `path` to a list of event dicts and a detected-schema summary.

    The dict shape is denormalised: case_id + activity + timestamp + (any other
    XES key) per event. Order is preserved from the source file.
    """
    rows: list[dict[str, Any]] = []
    seen_event_keys: set[str] = set()
    seen_trace_keys: set[str] = set()

    with _open(path) as fh:
        ctx = etree.iterparse(
            fh,
            events=("end",),
            tag=(f"{XES_NS}trace", f"{XES_NS_NOSLASH}trace", "trace"),
        )
        for _evt, trace in ctx:
            trace_attrs: dict[str, Any] = {}
            for key, value in _iter_attribute_pairs(trace):
                seen_trace_keys.add(key)
                canonical = _TRACE_KEY_MAP.get(key, key)
                trace_attrs[canonical] = value

            for event_elem in trace.iterchildren(
                f"{XES_NS}event", f"{XES_NS_NOSLASH}event", "event"
            ):
                row: dict[str, Any] = dict(trace_attrs)
                for key, value in _iter_attribute_pairs(event_elem):
                    seen_event_keys.add(key)
                    canonical = _EVENT_KEY_MAP.get(key, key)
                    row[canonical] = value
                rows.append(row)
                if on_progress and len(rows) % progress_every == 0:
                    on_progress(len(rows))

            trace.clear(keep_tail=True)
            # Free the parent so lxml releases the memory.
            while trace.getprevious() is not None:
                parent = trace.getparent()
                if parent is None:
                    break
                del parent[0]

    if on_progress:
        on_progress(len(rows))

    detected = {
        "trace_attributes": sorted(seen_trace_keys),
        "event_attributes": sorted(seen_event_keys),
        "canonical_columns": sorted(
            {col for col in (_EVENT_KEY_MAP[k] for k in seen_event_keys if k in _EVENT_KEY_MAP)}
            | {_TRACE_KEY_MAP[k] for k in seen_trace_keys if k in _TRACE_KEY_MAP}
        ),
    }
    return rows, detected
