"""CSV ingestion - applies a column mapping to a flat CSV.

The frontend column-mapping wizard (phase 7) supplies a CsvColumnMapping; in
v1 we accept the mapping as a JSON form-field on the upload. Without a mapping
we attempt a best-effort autodetect over common column-name conventions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from mate.api.schemas.event_logs import CsvColumnMapping

AUTODETECT_CANDIDATES: dict[str, list[str]] = {
    "case_id": ["case_id", "case", "case concept name", "trace_id", "id"],
    "activity": ["activity", "task", "concept name", "event"],
    "timestamp": [
        "timestamp",
        "time",
        "datetime",
        "date",
        "time timestamp",
        "start_timestamp",
        "start",
    ],
    "end_timestamp": ["end_timestamp", "complete_timestamp", "time complete", "completion", "end"],
    "resource": ["resource", "org resource", "user", "agent", "performer"],
    "cost": ["cost", "cost total", "amount", "price"],
}

_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def normalise_ident(value: str) -> str:
    """Lowercase + strip non-alphanumerics so 'Case ID', 'case-id',
    'Case:Concept:Name', 'caseConceptName' all collapse to a comparable form.
    """
    return _NORMALISE_RE.sub("", value.lower())


def autodetect_mapping(columns: list[str]) -> CsvColumnMapping | None:
    """Best-effort header → canonical mapping.

    Two passes: exact normalised match first, then substring containment for
    whatever's still unclaimed. Each header can only be claimed once, so an
    early exact match wins over a later substring one.
    """
    headers = [(c, normalise_ident(c)) for c in columns]
    claimed: set[str] = set()
    found: dict[str, str] = {}

    def _find(
        candidates: list[str],
        predicate,  # (header_norm, cand_norm) -> bool
    ) -> str | None:
        for cand in candidates:
            cand_norm = normalise_ident(cand)
            if not cand_norm:
                continue
            for raw, norm in headers:
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

    if {"case_id", "activity", "timestamp"}.issubset(found):
        return CsvColumnMapping(**found)
    return None


def parse_csv(
    path: Path,
    mapping: CsvColumnMapping | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], CsvColumnMapping]:
    """Return (rows, detected_schema, effective_mapping)."""
    delimiter = mapping.delimiter if mapping and mapping.delimiter else ","
    df = pd.read_csv(path, sep=delimiter, dtype=str, keep_default_na=False)
    columns = list(df.columns)

    effective = mapping or autodetect_mapping(columns)
    if effective is None:
        # Best-effort import: hand the raw (all-string) columns back so the
        # central role resolver in `dispatch` can map them - and flag the log
        # for manual review there. We still return a CsvColumnMapping shell so
        # callers relying on the tuple shape keep working.
        detected = {"csv_columns": columns, "canonical_columns": [], "mapping": None}
        shell = CsvColumnMapping(case_id="", activity="", timestamp="", delimiter=delimiter)
        return df.to_dict(orient="records"), detected, shell

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

    df = df.rename(columns=rename)

    if effective.timestamp_format:
        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            format=effective.timestamp_format,
            errors="coerce",
            utc=False,
        )
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=False)

    if "end_timestamp" in df.columns:
        df["end_timestamp"] = pd.to_datetime(df["end_timestamp"], errors="coerce", utc=False)

    if "cost" in df.columns:
        df["cost"] = pd.to_numeric(df["cost"], errors="coerce")

    rows = df.to_dict(orient="records")
    detected = {
        "csv_columns": columns,
        "canonical_columns": sorted(set(rename.values())),
        "mapping": effective.model_dump(),
    }
    return rows, detected, effective
