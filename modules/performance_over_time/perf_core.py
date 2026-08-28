"""Per-slice performance KPIs, faithful to the ``performance`` module.

Vendored from ``modules/performance/compute.py`` so this module stays
self-contained. ``performance`` computes its KPIs with DuckDB SQL over the
whole log; here the log has already been sliced by :mod:`slicing`, so the same
definitions are re-expressed in pandas over a slice sub-frame
(``case_id``/``activity``/``timestamp``). The numbers match ``performance``'s
``summary`` block metric-for-metric:

* cycle time = per-case span (last minus first event) in seconds; avg / median
  / p90 / p95 / min / max over those spans (``quantile_from_sorted``, vendored).
* throughput = cases per day over the slice's event timespan
  (``compute_throughput_per_day``, vendored - keeps ``performance``'s
  single-instant div-by-zero handling).
* lead time = average cycle time (``performance``'s definition).
"""

from __future__ import annotations

import math
from typing import Any, cast

import pandas as pd


def compute_throughput_per_day(
    cases: int, earliest: pd.Timestamp | None, latest: pd.Timestamp | None
) -> float:
    """Cases per day over ``[earliest, latest]`` (vendored from performance)."""
    if cases <= 0 or earliest is None or latest is None:
        return 0.0
    span_seconds = (latest - earliest).total_seconds()
    if span_seconds <= 0:
        return float(cases)
    return cases * 86400.0 / span_seconds


def quantile_from_sorted(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile of an ascending list (vendored)."""
    if not sorted_values:
        return 0.0
    idx = (len(sorted_values) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(sorted_values[lo])
    frac = idx - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _case_spans_seconds(df: pd.DataFrame) -> tuple[list[float], pd.Timestamp, pd.Timestamp]:
    """Per-case span in seconds (ascending) plus the earliest/latest event ts.

    ``df`` already has a datetime ``timestamp`` column (coerced upstream in
    :mod:`slicing`); coerce again defensively so the arithmetic below is on
    ``datetime64`` and pyright sees concrete ``Timestamp`` scalars.
    """
    ts = pd.to_datetime(df["timestamp"])
    grouped = ts.groupby(df["case_id"])
    first = cast("pd.Series", grouped.min())
    last = cast("pd.Series", grouped.max())
    deltas = cast("pd.Series", last - first)
    spans = sorted(float(v) for v in deltas.dt.total_seconds().to_list())
    earliest = cast("pd.Timestamp", ts.min())
    latest = cast("pd.Timestamp", ts.max())
    return spans, earliest, latest


def _activity_sequence(s: pd.Series) -> tuple[str, ...]:
    return tuple(str(a) for a in s.to_list())


def _distinct_variants(df: pd.DataFrame) -> int:
    """Number of distinct activity sequences (variants), ordered by timestamp."""
    ordered = df.sort_values("timestamp", kind="mergesort")
    seqs = cast(
        "pd.Series",
        ordered.groupby("case_id", sort=False)["activity"].apply(_activity_sequence),
    )
    return int(seqs.nunique())


def compute_perf_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Compute the performance KPIs for a (already-sliced) event log.

    Mirrors ``performance``'s ``summary`` block. The slice gate in
    :mod:`slicing` never calls this on an empty frame, but it stays defensive
    and returns zeroed metrics for one so callers never crash.
    """
    if df.empty or df["case_id"].nunique() == 0:
        return {
            "cases": 0,
            "events": 0,
            "variants": 0,
            "avg_cycle_time_s": 0.0,
            "median_cycle_time_s": 0.0,
            "p90_cycle_time_s": 0.0,
            "p95_cycle_time_s": 0.0,
            "min_cycle_time_s": 0.0,
            "max_cycle_time_s": 0.0,
            "throughput_cases_per_day": 0.0,
            "lead_time_s": 0.0,
        }

    spans, earliest, latest = _case_spans_seconds(df)

    cases = len(spans)
    avg_cycle = float(sum(spans) / cases) if cases else 0.0
    throughput = compute_throughput_per_day(cases, earliest, latest)

    return {
        "cases": cases,
        "events": len(df),
        "variants": _distinct_variants(df),
        "avg_cycle_time_s": avg_cycle,
        "median_cycle_time_s": quantile_from_sorted(spans, 0.5),
        "p90_cycle_time_s": quantile_from_sorted(spans, 0.9),
        "p95_cycle_time_s": quantile_from_sorted(spans, 0.95),
        "min_cycle_time_s": float(spans[0]) if spans else 0.0,
        "max_cycle_time_s": float(spans[-1]) if spans else 0.0,
        "throughput_cases_per_day": float(throughput),
        "lead_time_s": avg_cycle,
    }
