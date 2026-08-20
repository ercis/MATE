"""Enriched-attribute complexity measures (EnrichedComplexity.py port).

When trace and event attribute sets match the IEEE XES standard set the
user requested, we build the EPA with edges keyed by (activity,
attributes) instead of activity alone. The downstream entropy measures
are then computed against this enriched EPA, while every measure that
depends solely on the activity sequence (Lempel-Ziv, Pentland process,
structure, affinity, deviation-from-random, magnitude, ...) is identical
to the plain bundle and is reused from :mod:`complexity_core`.

The expected attribute sets are::

    trace: {'variant', 'concept:name', 'creator', 'variant-index'}
    event: {'time:timestamp', 'Resource', 'lifecycle:transition',
            'concept:name', 'Activity', 'org:resource'}

XES traces always carry ``concept:name`` (case id) and events always
carry ``concept:name`` (activity) + ``time:timestamp``, so the columns
mapped to case_id / activity / timestamp also count toward the check.
"""

from __future__ import annotations

from typing import Any, Hashable

import pandas as pd

from .complexity_core import (
    affinity,
    build_epa,
    compute_basic_metrics,
    deviation_from_random,
    lempel_ziv_complexity,
    level_of_detail,
    magnitude,
    pct_distinct_traces,
    pentland_process,
    pentland_task,
    sequence_entropy,
    sequence_entropy_forgetting,
    structure,
    support,
    time_granularity,
    trace_length_stats,
    variant_entropy,
    variety,
)


REQUIRED_TRACE_ATTRS: frozenset[str] = frozenset(
    {"variant", "concept:name", "creator", "variant-index"}
)
REQUIRED_EVENT_ATTRS: frozenset[str] = frozenset(
    {
        "time:timestamp",
        "Resource",
        "lifecycle:transition",
        "concept:name",
        "Activity",
        "org:resource",
    }
)

# Canonical-column → original XES key (the platform renames these on
# import; the detected_schema still records the original name).
_CANONICAL_TO_XES = {
    "case_id": "concept:name",
    "activity": "concept:name",
    "timestamp": "time:timestamp",
    "resource": "org:resource",
    "lifecycle": "lifecycle:transition",
}


def is_enriched_supported(detected_schema: dict[str, Any] | None) -> bool:
    """Return True iff the log's detected XES attributes contain the full
    set the enriched EPA needs.
    """
    if not detected_schema:
        return False
    trace = set(detected_schema.get("trace_attributes") or [])
    event = set(detected_schema.get("event_attributes") or [])
    return REQUIRED_TRACE_ATTRS.issubset(trace) and REQUIRED_EVENT_ATTRS.issubset(event)


def _attr_columns(df: pd.DataFrame, required: set[str]) -> list[str]:
    """Resolve XES keys to actual parquet columns. Three lookup paths:
    direct match, identifier-safe rename (``variant-index`` →
    ``variant_index``), or canonical ingest rename (``time:timestamp`` →
    ``timestamp``).
    """
    cols = set(df.columns)
    resolved: list[str] = []
    for xes_key in sorted(required):
        if xes_key in cols:
            resolved.append(xes_key)
            continue
        field_name = _attr_to_field(xes_key)
        if field_name != xes_key and field_name in cols:
            resolved.append(field_name)
            continue
        for canonical, xes in _CANONICAL_TO_XES.items():
            if xes == xes_key and canonical in cols:
                resolved.append(canonical)
                break
    return resolved


def _build_enriched_key_fn(df: pd.DataFrame) -> Any:
    """Return a row→Hashable key that distinguishes events by (activity,
    selected event attributes, selected trace attributes).

    Trace attributes are duplicated onto every row during XES ingest, so
    they're available on the same row as the event attributes.
    """
    event_cols = _attr_columns(df, set(REQUIRED_EVENT_ATTRS))
    trace_cols = _attr_columns(df, set(REQUIRED_TRACE_ATTRS))
    # Avoid re-using case_id / activity / timestamp inside the key - they
    # already determine the prefix or the edge label.
    skip = {"case_id", "activity", "timestamp"}
    attr_cols = [c for c in (event_cols + trace_cols) if c not in skip]
    attr_cols = sorted(set(attr_cols))

    if not attr_cols:
        def key_only_activity(row: Any) -> Hashable:
            return row.activity
        return key_only_activity

    field_names = ["activity", *attr_cols]

    def key_with_attrs(row: Any) -> Hashable:
        return tuple(
            (name, _to_hashable(getattr(row, _attr_to_field(name), None)))
            for name in field_names
        )
    return key_with_attrs


def _attr_to_field(attr: str) -> str:
    """itertuples replaces non-identifier chars with underscores."""
    safe = []
    for ch in attr:
        safe.append(ch if (ch.isalnum() or ch == "_") else "_")
    out = "".join(safe)
    if out and out[0].isdigit():
        out = "_" + out
    return out


def _to_hashable(v: Any) -> Hashable:
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    try:
        return str(v)
    except Exception:
        return repr(v)


def compute_enriched_metrics(
    df: pd.DataFrame,
    *,
    exponential_k: float = 1.0,
) -> dict[str, Any]:
    """Compute the user-requested set of measures against an EPA built
    with attribute-aware edges (EnrichedComplexity-style).
    """
    if df.empty or df["case_id"].nunique() == 0:
        return {}

    # Use cleaned itertuples-friendly column names.
    df_renamed = df.rename(
        columns={c: _attr_to_field(c) for c in df.columns if c != _attr_to_field(c)}
    )
    key_fn = _build_enriched_key_fn(df_renamed)
    states, c_index = build_epa(df_renamed, key_fn=key_fn)

    h_var, h_var_norm = variant_entropy(states, c_index)
    h_seq, h_seq_norm = sequence_entropy(states, c_index)
    h_lin, h_lin_norm = sequence_entropy_forgetting(states, c_index, "linear")
    h_exp, h_exp_norm = sequence_entropy_forgetting(
        states, c_index, "exp", k=exponential_k
    )

    tl = trace_length_stats(df)

    return {
        "magnitude": magnitude(df),
        "support": support(df),
        "variety": variety(df),
        "level_of_detail": level_of_detail(df),
        "time_granularity_s": time_granularity(df),
        "structure": structure(df),
        "affinity": affinity(df),
        "trace_length_min": tl["min"],
        "trace_length_avg": tl["avg"],
        "trace_length_max": tl["max"],
        "distinct_traces_pct": pct_distinct_traces(df),
        "deviation_from_random": deviation_from_random(df),
        "lempel_ziv": lempel_ziv_complexity(df),
        "pentland_task": pentland_task(states),
        "pentland_process": pentland_process(df),
        "variant_entropy": h_var,
        "normalized_variant_entropy": h_var_norm,
        "sequence_entropy": h_seq,
        "normalized_sequence_entropy": h_seq_norm,
        "sequence_entropy_linear": h_lin,
        "normalized_sequence_entropy_linear": h_lin_norm,
        "sequence_entropy_exponential": h_exp,
        "normalized_sequence_entropy_exponential": h_exp_norm,
        "exponential_k": exponential_k,
    }


def compute_metrics_bundle(
    df: pd.DataFrame,
    detected_schema: dict[str, Any] | None,
    *,
    exponential_k: float = 1.0,
) -> dict[str, Any]:
    """Return ``{"basic": {...}, "enriched": {...} | None, "enriched_supported": bool}``."""
    enriched_supported = is_enriched_supported(detected_schema)
    basic = compute_basic_metrics(df, exponential_k=exponential_k)
    enriched: dict[str, Any] | None = None
    if enriched_supported:
        enriched = compute_enriched_metrics(df, exponential_k=exponential_k)
    return {
        "basic": basic,
        "enriched": enriched,
        "enriched_supported": enriched_supported,
    }
