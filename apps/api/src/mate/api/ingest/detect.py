"""Format detection for uploaded event logs.

Two stages:

1. ``detect_format(filename)`` - a *coarse* guess from the extension alone. Used
   before the upload is staged, to reject obviously-unsupported files (415) and
   to name the retained ``original.<ext>`` on disk.
2. ``sniff_format(path, coarse)`` - refines the coarse guess by looking at the
   file's actual *content*. This is where ``.json`` / ``.xml`` are auto-routed
   to either the case-centric pipeline or the object-centric (OCEL) one: real
   OCEL 2.0 files are routinely named plain ``.json`` / ``.xml``, and a plain
   ``.json`` may equally be an ordinary case-centric event array.

The refined ``source_format`` is what gets persisted on the ``process_logs`` row
and threaded through the import job. For OCEL, ``sniff_format`` also returns the
reader *flavor* (``json`` / ``xml`` / ``sqlite``) so ``parse_ocel`` can pick the
right pm4py reader independently of the on-disk filename.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from lxml import etree

from mate.api.ingest.compression import (
    decompressed,
    detect_compression,
    resolve_zip_member,
    split_compression,
)

log = structlog.get_logger(__name__)

# OcelFlavor is the OCEL sub-format that selects the pm4py reader.
OcelFlavor = str  # "json" | "xml" | "sqlite"

# How much of a JSON file to read when sniffing for OCEL markers. The marker
# keys (ocel:global-log / objectTypes / eventTypes) always live in the document
# header, so a generous prefix is more than enough and keeps large generic logs
# from being slurped whole just to classify them.
_JSON_SNIFF_BYTES = 256 * 1024
_XML_SNIFF_LIMIT = 20000


def _detect_inner(lower: str) -> str | None:
    """Classify an (already compression-stripped) lowercase filename."""
    if lower.endswith(".xes"):
        return "xes"
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".jsonocel") or lower.endswith(".xmlocel"):
        return "ocel"
    # OCEL 2.0 relational format. `.sqlite` is mapped to OCEL unconditionally:
    # this is a local OCEL-only tool, so the only SQLite uploads are OCEL2.
    if lower.endswith(".sqlite") or lower.endswith(".ocelsqlite"):
        return "ocel"
    if lower.endswith(".xml"):
        return "xml"
    if lower.endswith(".json"):
        return "json"
    return None


def detect_format(filename: str) -> str:
    """Map a filename to a *coarse* source format.

    ``.json`` / ``.xml`` are ambiguous (case-centric vs OCEL) and resolved later
    by :func:`sniff_format`; this only narrows to the family. Every format may
    additionally be compressed (``.gz`` / ``.bz2`` / ``.xz`` / ``.zip``) - the
    compression suffix is stripped before classifying. A bare ``data.zip`` maps
    to the transient coarse format ``"zip"``: its inner format is resolved from
    the archive's single member by :func:`sniff_format` after staging.
    """
    lower = filename.lower()
    # Legacy special case: `.xes.gz` stays its own source_format (persisted DB
    # rows and re-import payloads reference it).
    if lower.endswith(".xes.gz"):
        return "xes.gz"
    inner, algo = split_compression(lower)
    if algo is None:
        fmt = _detect_inner(lower)
        if fmt is None:
            raise ValueError(f"Unsupported file extension: {filename!r}")
        return fmt
    fmt = _detect_inner(inner)
    if fmt is not None:
        return fmt
    if algo == "zip":
        return "zip"
    raise ValueError(
        f"Compressed upload {filename!r} hides its file type - include the inner "
        "extension in the name (e.g. events.csv.gz)."
    )


def original_extension(filename: str, source_format: str) -> str:
    """The extension to store the retained upload under.

    We keep the upload under its real suffix (compression suffix included) so
    the file stays byte-faithful and meaningful on disk and ``find_original``
    can locate it. OCEL reader selection no longer depends on this -
    :func:`sniff_format` carries the flavor explicitly.
    """
    lower = filename.lower()
    inner, algo = split_compression(lower)
    comp_suffix = lower[len(inner) + 1 :] if algo is not None else ""
    base = inner if algo is not None else lower
    for ext in (
        "xes.gz",
        "xes",
        "csv",
        "jsonocel",
        "xmlocel",
        "ocelsqlite",
        "sqlite",
        "xml",
        "json",
    ):
        if base.endswith(f".{ext}"):
            return f"{ext}.{comp_suffix}" if comp_suffix else ext
    if algo == "zip":
        return "zip"
    # Fall back to the coarse format for anything without a recognised suffix.
    return "ocel" if source_format == "ocel" else source_format


def _ocel_flavor_from_extension(filename: str) -> OcelFlavor:
    # Strip a compression suffix first so `log.xmlocel.gz` still reads as xml.
    lower = split_compression(filename.lower())[0]
    if lower.endswith(".sqlite") or lower.endswith(".ocelsqlite"):
        return "sqlite"
    if lower.endswith(".xmlocel"):
        return "xml"
    return "json"


def looks_like_ocel_json(path: Path) -> bool:
    """Bounded prefix scan: does this JSON file carry OCEL marker keys?

    OCEL 1.0 declares ``ocel:events`` / ``ocel:objects`` / ``ocel:global-log``;
    OCEL 2.0 declares ``objectTypes`` + ``eventTypes`` (alongside ``events`` /
    ``objects``). A plain case-centric event array has none of these.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_JSON_SNIFF_BYTES)
    except OSError:
        return False
    text = head.decode("utf-8", errors="ignore")
    if any(marker in text for marker in ('"ocel:events"', '"ocel:objects"', '"ocel:global-log"')):
        return True
    return '"objectTypes"' in text and '"eventTypes"' in text


def looks_like_ocel_xml(path: Path) -> bool:
    """Bounded streaming scan: does this XML file look like an OCEL log?

    OCEL 2.0 XML nests ``<object-types>`` / ``<event-types>`` / ``<objects>`` /
    ``<events>`` under ``<log>``; OCEL 1.0 XML uses an ``ocel`` namespace or
    ``ocel:*`` element names. XES (which also uses ``<log>``) has none of these.
    """
    try:
        context = etree.iterparse(str(path), events=("start",))
        seen = 0
        saw_object_types = False
        saw_event_types = False
        for _event, elem in context:
            seen += 1
            if seen > _XML_SNIFF_LIMIT:
                break
            if not isinstance(elem, etree._Element):
                continue
            qname = etree.QName(elem.tag)
            ns = (qname.namespace or "").lower()
            local = qname.localname.lower()
            if "ocel" in ns:
                return True
            if local in {"object-types", "objecttypes"}:
                saw_object_types = True
            elif local in {"event-types", "eventtypes"}:
                saw_event_types = True
            if saw_object_types and saw_event_types:
                return True
        return False
    except (etree.XMLSyntaxError, OSError):
        return False


def sniff_format(
    path: Path, coarse: str, *, filename: str | None = None
) -> tuple[str, OcelFlavor | None]:
    """Refine a coarse format by inspecting the staged file's content.

    Returns ``(source_format, ocel_flavor)`` where ``ocel_flavor`` is set only
    for OCEL logs. Unambiguous families (xes / xes.gz / csv) pass through
    untouched. Compressed uploads are content-sniffed on a decompressed temp
    copy; a bare ``"zip"`` coarse format resolves to its single member's format
    first (raising ``ValueError`` for empty/ambiguous archives).
    """
    name = filename or path.name
    if coarse == "zip":
        member = resolve_zip_member(path)
        name = member.rsplit("/", 1)[-1]
        coarse = detect_format(name)
        if coarse == "zip":  # zip-in-zip: unwrapped by `decompressed` below
            raise ValueError("Nested zip archives are not supported.")
    # The content probes below need plain bytes; everything else passes through
    # on the extension alone.
    if coarse == "ocel":
        return "ocel", _ocel_flavor_from_extension(name)
    if coarse in {"xml", "json"} and detect_compression(path) is not None:
        with decompressed(path) as plain:
            return _sniff_content(plain, coarse)
    return _sniff_content(path, coarse)


def _sniff_content(path: Path, coarse: str) -> tuple[str, OcelFlavor | None]:
    if coarse == "xml":
        if looks_like_ocel_xml(path):
            return "ocel", "xml"
        return "xml", None
    if coarse == "json":
        if looks_like_ocel_json(path):
            return "ocel", "json"
        return "json", None
    return coarse, None


__all__ = [
    "OcelFlavor",
    "detect_format",
    "looks_like_ocel_json",
    "looks_like_ocel_xml",
    "original_extension",
    "sniff_format",
]
