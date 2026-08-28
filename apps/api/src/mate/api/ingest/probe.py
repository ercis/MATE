"""Schema probe for a staged upload - the input of the import wizard.

The import flow stages the upload once (``POST /event-logs/stage``), probes it
here, and only submits the actual import job after the user has confirmed the
column mapping. The probe therefore has to answer, for *every* supported
format, the same three questions:

1. which source columns does this file have,
2. what do they look like (coverage + a few real values), and
3. which canonical role does each column most likely play - and how confident
   is that guess.

(3) deliberately runs through :func:`mate.api.ingest.mapping.resolve_roles`,
the exact resolver the import job uses, so what the user confirms in the wizard
is what the import actually does. ``quality`` (``exact`` / ``fuzzy`` /
``fallback``) is what the UI renders as its confidence chip.

Sampling is bounded (a few hundred events) and always runs on a decompressed
copy, so a multi-GB gzipped XES probes in the same time as a small CSV.
"""

from __future__ import annotations

import csv as csv_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from mate.api.ingest.compression import decompressed
from mate.api.ingest.mapping import resolve_roles

log = structlog.get_logger(__name__)

# How much of the source we look at. Deliberately small: the probe runs inline
# in the upload request, and role resolution converges long before this.
MAX_SAMPLE_EVENTS = 200
MAX_SAMPLE_TRACES = 200
# Delimiter sniffing reads a text prefix only.
_CSV_SNIFF_BYTES = 64 * 1024
_CSV_DELIMITERS = ",;\t|"
# Sample values are UI hints - long free-text cells get cut.
_SAMPLE_VALUE_CHARS = 60
_SAMPLES_PER_COLUMN = 3


@dataclass(frozen=True)
class ProbeColumnStat:
    """One source column as the mapping wizard sees it."""

    name: str
    coverage: float
    samples: list[str]


@dataclass
class ProbeResult:
    columns: list[ProbeColumnStat] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)
    quality: dict[str, str] = field(default_factory=dict)
    events_sampled: int = 0
    log_model: str = "case_centric"
    needs_mapping: bool = True
    delimiter: str | None = None
    event_element: str | None = None
    event_path: str | None = None


def probe_staged(path: Path, source_format: str) -> ProbeResult:
    """Describe a staged upload for the mapping wizard.

    ``source_format`` is the *sniffed* format (`sniff_format`), so ``.xml`` /
    ``.json`` that turned out to be OCEL already arrive as ``"ocel"``.
    """
    # Object-centric logs have no case_id/activity/timestamp roles to map - the
    # OCEL parser owns their schema entirely.
    if source_format == "ocel":
        return ProbeResult(log_model="object_centric", needs_mapping=False)

    with decompressed(path) as plain:
        frame, extras = _sample_frame(plain, source_format)

    if frame.empty and not list(frame.columns):
        return ProbeResult(
            needs_mapping=True,
            delimiter=extras.get("delimiter"),
            event_element=extras.get("event_element"),
            event_path=extras.get("event_path"),
        )

    columns = [str(c) for c in frame.columns]
    resolution = resolve_roles(columns, sample=frame)
    resolved = resolution.as_dict()

    return ProbeResult(
        columns=[_column_stat(frame, col) for col in columns],
        roles=resolved["roles"],
        quality=resolved["quality"],
        events_sampled=len(frame),
        delimiter=extras.get("delimiter"),
        event_element=extras.get("event_element"),
        event_path=extras.get("event_path"),
    )


def _sample_frame(path: Path, source_format: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a bounded sample of `path` into a frame + per-format extras."""
    if source_format in {"xes", "xes.gz"}:
        from mate.api.ingest.xes import sample_xes

        rows = sample_xes(path, max_traces=MAX_SAMPLE_TRACES, max_events=MAX_SAMPLE_EVENTS)
        return pd.DataFrame(rows), {}

    if source_format == "csv":
        delimiter = sniff_delimiter(path)
        frame = pd.read_csv(
            path,
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
            nrows=MAX_SAMPLE_EVENTS,
        )
        return frame, {"delimiter": delimiter}

    if source_format == "xml":
        # A XES-shaped .xml is parsed by the XES parser at import time, so probe
        # it the same way - otherwise the wizard would offer raw XES tag names.
        from mate.api.ingest.xml_parser import is_xes_like, sample_xml_events

        if is_xes_like(path):
            from mate.api.ingest.xes import sample_xes

            rows = sample_xes(path, max_traces=MAX_SAMPLE_TRACES, max_events=MAX_SAMPLE_EVENTS)
            return pd.DataFrame(rows), {}
        records, element = sample_xml_events(path, max_events=MAX_SAMPLE_EVENTS)
        return pd.DataFrame(records), {"event_element": element}

    if source_format == "json":
        from mate.api.ingest.json_parser import sample_json_events

        records, event_path = sample_json_events(path, max_events=MAX_SAMPLE_EVENTS)
        return pd.DataFrame(records), {"event_path": event_path}

    raise ValueError(f"Source format {source_format!r} cannot be probed.")


def sniff_delimiter(path: Path) -> str:
    """Best-effort CSV delimiter from a text prefix; falls back to a comma.

    The client can't do this itself for compressed uploads, and a wrong
    delimiter collapses the whole file into a single column - which the wizard
    would then happily offer as the case_id.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
            head = fh.read(_CSV_SNIFF_BYTES)
    except OSError:
        return ","
    if not head.strip():
        return ","
    try:
        return csv_module.Sniffer().sniff(head, delimiters=_CSV_DELIMITERS).delimiter
    except csv_module.Error:
        # Sniffer gives up on single-column files and other degenerate inputs;
        # pick whichever candidate appears most often on the header line.
        header = head.splitlines()[0] if head.splitlines() else ""
        best = max(_CSV_DELIMITERS, key=header.count)
        return best if header.count(best) else ","


def _column_stat(frame: pd.DataFrame, column: str) -> ProbeColumnStat:
    series = frame[column]
    total = max(len(series), 1)
    filled = series.dropna()
    # Empty strings are "missing" as far as the user is concerned: an all-blank
    # column must not read as 100% covered in the wizard.
    text = filled.map(_as_text)
    present = text[text != ""]

    samples: list[str] = []
    for value in present:
        if value in samples:
            continue
        samples.append(value)
        if len(samples) >= _SAMPLES_PER_COLUMN:
            break

    return ProbeColumnStat(
        name=str(column),
        coverage=round(len(present) / total, 3),
        samples=samples,
    )


def _as_text(value: Any) -> str:
    text = str(value).strip()
    if len(text) > _SAMPLE_VALUE_CHARS:
        return text[: _SAMPLE_VALUE_CHARS - 1] + "…"
    return text


__all__ = [
    "MAX_SAMPLE_EVENTS",
    "ProbeColumnStat",
    "ProbeResult",
    "probe_staged",
    "sniff_delimiter",
]
