"""Generic XML ingestion.

Two flavours of XML show up under the `.xml` extension:

1. *Generic* XML - a single repeating event element with a flat bag of
   attributes / leaf-text children::

       <log>
         <event case_id="c-1" activity="register" timestamp="2026-01-01T08:00Z"/>
         <event>
           <case_id>c-2</case_id>
           <activity>check stock</activity>
           <timestamp>2026-01-01T09:00Z</timestamp>
         </event>
       </log>

2. *XES dressed as XML* - IEEE-1849 with typed-attribute children
   (``<string key="…" value="…"/>``, ``<date …/>``, etc.) inside ``<trace>``
   and ``<event>`` wrappers, often with the XES namespace.

This module probes the file (`probe_xml`) and parses it (`parse_xml`). When
the content is XES-shaped we delegate to the dedicated XES parser instead of
trying to fit it into the generic flat-event model - the typed-attribute
children would otherwise out-vote the real event rows in our sibling-cardinality
heuristic, and case-level fields (the trace's ``concept:name``) wouldn't fan
out onto the per-event rows. Sniffing happens early via `is_xes_like`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lxml import etree

from mate.api.schemas.event_logs import XmlColumnMapping

ProgressCallback = Callable[[int], None]

# XES "typed-attribute" leaves carry data via key/value attributes rather than
# child elements or text content. We use them to distinguish XES-shaped XML.
_XES_TYPED_TAGS = frozenset({"string", "int", "float", "boolean", "date", "id"})
_XES_NAMESPACES = frozenset(
    {
        "http://www.xes-standard.org/",
        "http://www.xes-standard.org",
    }
)


def _localname(tag: object) -> str:
    """Return the local part of an element tag, stripping any '{namespace}' prefix."""
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname


def is_xes_like(path: Path, *, scan_limit: int = 20000) -> bool:
    """Quick streaming sniff: does the file look like XES regardless of extension?

    Any of these positive signals is sufficient:

    * The root element declares the XES namespace.
    * The root element carries an ``xes.*`` or ``openxes.*`` attribute (the
      OpenXES library emits these instead of a namespace declaration).
    * We see an ``<extension>`` element whose ``uri`` points at
      ``xes-standard.org`` - XES files declare these in the log header.
    * We see a XES typed-attribute leaf (``<string key="…" value="…"/>`` etc.)
      anywhere - including directly under ``<log>``, which is how XES carries
      log-level metadata.

    We scan up to ``scan_limit`` parse events and never bail to ``False`` on
    a single sparse ``<trace>``/``<event>`` - an empty trace appearing before
    populated ones used to misclassify the whole file.
    """
    try:
        context = etree.iterparse(str(path), events=("start", "end"))
        seen = 0
        for event, elem in context:
            seen += 1
            if seen > scan_limit:
                return False
            if not isinstance(elem, etree._Element):
                continue
            if event == "start":
                if seen == 1:
                    ns = etree.QName(elem.tag).namespace or ""
                    if ns in _XES_NAMESPACES:
                        return True
                    for attr_name in elem.attrib:
                        local = _localname(attr_name).lower()
                        if local.startswith("xes.") or local.startswith("openxes."):
                            return True
                local = _localname(elem.tag)
                if local == "extension":
                    uri = elem.get("uri") or ""
                    if "xes-standard.org" in uri:
                        return True
                elif local in _XES_TYPED_TAGS and elem.get("key") is not None:
                    return True
        return False
    except (etree.XMLSyntaxError, OSError):
        return False


def _event_fields(elem: etree._Element) -> dict[str, str]:
    """Flatten one event element into ``{field_name: value}``.

    Attributes contribute as ``field=attr-localname``; direct *leaf* child
    elements (i.e. children without their own element children) contribute as
    ``field=child-localname`` with the child's text content as the value. On a
    name collision the child element wins - it's almost always the more
    deliberate authoring choice.
    """
    out: dict[str, str] = {}
    for raw_attr, raw_val in elem.attrib.items():
        name = _localname(raw_attr)
        if name:
            out[name] = str(raw_val)
    for child in elem:
        if not isinstance(child, etree._Element):
            continue
        if len(child) > 0:
            continue
        text = (child.text or "").strip()
        if not text:
            continue
        name = _localname(child.tag)
        if name:
            out[name] = text
    return out


def _pick_event_element(root: etree._Element) -> str | None:
    """Choose the most likely event element by sibling cardinality.

    We count every element except the document root and pick whichever
    local-name has the largest population - almost always the repeating
    event row. Returns ``None`` if the document has no descendants.
    """
    counts: Counter[str] = Counter()
    for elem in root.iter():
        if elem is root:
            continue
        if not isinstance(elem, etree._Element):
            continue
        name = _localname(elem.tag)
        if name:
            counts[name] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def sample_xml_events(
    path: Path,
    *,
    max_events: int = 200,
) -> tuple[list[dict[str, str]], str | None]:
    """Flatten the first ``max_events`` event elements into records.

    Returns ``(records, event_element)``. Shared by :func:`probe_xml` (which
    reduces the records to per-field coverage) and the import wizard's probe
    (which needs the raw sample for the column-role heuristics).
    """
    tree = etree.parse(str(path))
    root = tree.getroot()
    target = _pick_event_element(root)
    if target is None:
        return [], None

    records: list[dict[str, str]] = []
    for elem in root.iter():
        if not isinstance(elem, etree._Element):
            continue
        if _localname(elem.tag) != target:
            continue
        records.append(_event_fields(elem))
        if len(records) >= max_events:
            break
    return records, target


def probe_xml(
    path: Path,
    *,
    max_events: int = 200,
) -> dict[str, Any]:
    """Return a description of the file driving the mapping wizard.

    Shape:

    ```
    {
      "format_hint": "generic" | "xes",
      "event_element": "event",
      "events_sampled": 42,
      "fields": [
        {"name": "case_id", "coverage": 1.0, "samples": ["c-1", "c-2"]},
        ...
      ]
    }
    ```

    ``coverage`` is the fraction of sampled events that contained the field.
    ``samples`` is a deduped, capped slice of values for the field - purely
    UI hints, not used by the parser. When ``format_hint`` is ``"xes"`` the
    frontend should skip the mapping wizard: the file will be parsed by the
    XES parser, which already knows its canonical schema.
    """
    # OCEL-shaped XML auto-routes to the object-centric path server-side; the
    # frontend skips the wizard, same as for XES.
    from mate.api.ingest.detect import looks_like_ocel_xml

    if looks_like_ocel_xml(path):
        return {"format_hint": "ocel", "event_element": None, "events_sampled": 0, "fields": []}
    if is_xes_like(path):
        return {"format_hint": "xes", "event_element": "event", "events_sampled": 0, "fields": []}
    records, target = sample_xml_events(path, max_events=max_events)
    if target is None:
        return {
            "format_hint": "generic",
            "event_element": None,
            "events_sampled": 0,
            "fields": [],
        }

    samples: dict[str, list[str]] = {}
    presence: Counter[str] = Counter()
    for fields in records:
        for name, value in fields.items():
            presence[name] += 1
            bucket = samples.setdefault(name, [])
            if len(bucket) < 3 and value not in bucket:
                bucket.append(value)
    seen = len(records)

    fields_summary = [
        {
            "name": name,
            "coverage": round(count / max(1, seen), 3),
            "samples": samples.get(name, []),
        }
        for name, count in presence.most_common()
    ]
    return {
        "event_element": target,
        "events_sampled": seen,
        "fields": fields_summary,
    }


def autodetect_mapping(probe: dict[str, Any]) -> XmlColumnMapping | None:
    """Best-effort: reuse CSV's canonical-name heuristics on the probed fields."""
    # Late import to avoid a cycle with csv_parser at module load time.
    from mate.api.ingest.csv_parser import AUTODETECT_CANDIDATES, normalise_ident

    event_element = probe.get("event_element")
    fields = [f.get("name") for f in probe.get("fields") or [] if isinstance(f, dict)]
    fields = [f for f in fields if isinstance(f, str)]
    if not event_element or not fields:
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
    return XmlColumnMapping(
        event_element=event_element,
        case_id=found["case_id"],
        activity=found["activity"],
        timestamp=found["timestamp"],
        end_timestamp=found.get("end_timestamp"),
        resource=found.get("resource"),
        cost=found.get("cost"),
    )


def _parse_as_xes(
    path: Path,
    *,
    on_progress: ProgressCallback | None,
    progress_every: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], XmlColumnMapping]:
    """Hand a XES-shaped .xml file to the dedicated XES parser.

    The XES parser already understands typed-attribute children, the trace/event
    hierarchy, and the canonical XES keys (`concept:name`, `time:timestamp`,
    `org:resource`, …) - so the result is what the user actually wants. The
    mapping we return is purely informational so the import job's `meta.json`
    is round-trippable.
    """
    from mate.api.ingest.xes import parse_xes

    rows, xes_detected = parse_xes(
        path,
        on_progress=on_progress,
        progress_every=progress_every,
    )
    detected = {
        **xes_detected,
        "format_hint": "xes",
        "canonical_columns": sorted(set(xes_detected.get("canonical_columns") or [])),
    }
    # Document the XES-derived field assignment in mapping form. case_id and
    # activity share the same XES key (`concept:name`) - one is taken from the
    # trace level, the other from the event level. We record the event-level
    # key for `activity` and a marker for case_id.
    informational = XmlColumnMapping(
        event_element="event",
        case_id="trace.concept:name",
        activity="concept:name",
        timestamp="time:timestamp",
        resource="org:resource",
    )
    return rows, detected, informational


def parse_xml(
    path: Path,
    mapping: XmlColumnMapping | None,
    *,
    on_progress: ProgressCallback | None = None,
    progress_every: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, Any], XmlColumnMapping]:
    """Stream-parse ``path`` into event rows.

    Returns ``(rows, detected_schema, effective_mapping)``. When ``mapping`` is
    omitted we run autodetect and raise ``ValueError`` if it couldn't find
    case_id / activity / timestamp on its own. XES-shaped XML bypasses the
    generic path entirely.
    """
    # If the user supplied a mapping explicitly, honour it - they're treating
    # the file as generic XML on purpose. Otherwise sniff for XES first; that's
    # the only way to handle the common "XES file with .xml extension" case
    # without having the typed-attribute children out-vote real events.
    if mapping is None and is_xes_like(path):
        return _parse_as_xes(path, on_progress=on_progress, progress_every=progress_every)

    effective = mapping
    if effective is None:
        probe = probe_xml(path)
        effective = autodetect_mapping(probe)
        if effective is None:
            # Best-effort: parse with just the probed event element and leave the
            # role columns raw - the central resolver in `dispatch` maps them and
            # flags the log for manual review. Empty role names never match a real
            # field, so nothing is renamed here.
            element = probe.get("event_element")
            if not element:
                raise ValueError(
                    "Could not find a repeating event element in the XML. Submit "
                    "an `xml_mapping` naming the event element and key columns."
                )
            effective = XmlColumnMapping(
                event_element=element, case_id="", activity="", timestamp=""
            )

    target = effective.event_element
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

    # iterparse + clear-as-we-go keeps memory flat on large logs. We can't
    # constrain by tag the way the XES parser does because the target tag can
    # live anywhere in the tree and may carry a namespace we don't know up
    # front - so we filter on local-name per event.
    context = etree.iterparse(str(path), events=("end",))
    for _evt, elem in context:
        if not isinstance(elem, etree._Element):
            continue
        if _localname(elem.tag) != target:
            continue
        fields = _event_fields(elem)
        seen_fields.update(fields)
        row: dict[str, Any] = {}
        for src, value in fields.items():
            canonical = rename.get(src, src)
            row[canonical] = value
        rows.append(row)
        if on_progress and len(rows) % progress_every == 0:
            on_progress(len(rows))
        # Drop this element + any preceding siblings so lxml releases memory.
        elem.clear()
        prev = elem.getprevious()
        parent = elem.getparent()
        while prev is not None and parent is not None:
            del parent[0]
            prev = elem.getprevious()

    if on_progress:
        on_progress(len(rows))

    detected = {
        "xml_event_element": target,
        "xml_fields": sorted(seen_fields),
        "canonical_columns": sorted(set(rename.values())),
        "mapping": effective.model_dump(),
    }
    return rows, detected, effective


__all__ = [
    "autodetect_mapping",
    "is_xes_like",
    "parse_xml",
    "probe_xml",
    "sample_xml_events",
]
