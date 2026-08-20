"""Performance computation helpers - DuckDB-first, pm4py for the rest."""

from __future__ import annotations

import math
from typing import Any


KPI_SQL = """
WITH case_spans AS (
    SELECT
        case_id,
        MIN(timestamp) AS first_ts,
        MAX(timestamp) AS last_ts,
        epoch(MAX(timestamp)) - epoch(MIN(timestamp)) AS duration_s,
        COUNT(*) AS n_events
    FROM events
    GROUP BY case_id
)
SELECT
    COUNT(*) AS cases,
    SUM(n_events) AS events,
    AVG(duration_s) AS avg_cycle_time_s,
    quantile_cont(duration_s, 0.5) AS median_cycle_time_s,
    quantile_cont(duration_s, 0.9) AS p90_cycle_time_s,
    quantile_cont(duration_s, 0.95) AS p95_cycle_time_s,
    MIN(duration_s) AS min_cycle_time_s,
    MAX(duration_s) AS max_cycle_time_s,
    MIN(first_ts) AS earliest,
    MAX(last_ts) AS latest
FROM case_spans;
"""


VARIANTS_SQL = """
WITH ordered AS (
    SELECT case_id, activity, timestamp,
           ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY timestamp) AS rn
    FROM events
),
trace AS (
    SELECT case_id, string_agg(activity, '|' ORDER BY rn) AS variant
    FROM ordered
    GROUP BY case_id
)
SELECT COUNT(DISTINCT variant) AS variants FROM trace;
"""


PER_ACTIVITY_FREQ_SQL = """
SELECT activity, COUNT(*) AS frequency
FROM events
GROUP BY activity
ORDER BY frequency DESC;
"""


CYCLE_HISTOGRAM_SQL = """
WITH case_spans AS (
    SELECT case_id, epoch(MAX(timestamp)) - epoch(MIN(timestamp)) AS duration_s
    FROM events
    GROUP BY case_id
),
range_bounds AS (
    SELECT MIN(duration_s) AS lo, MAX(duration_s) AS hi
    FROM case_spans
)
SELECT
    bucket,
    COUNT(*) AS count,
    MIN(duration_s) AS bucket_min,
    MAX(duration_s) AS bucket_max
FROM (
    SELECT
        duration_s,
        CASE
            WHEN (SELECT hi FROM range_bounds) = (SELECT lo FROM range_bounds)
                THEN 0
            ELSE LEAST(
                CAST(50 * (duration_s - (SELECT lo FROM range_bounds))
                     / NULLIF((SELECT hi FROM range_bounds) - (SELECT lo FROM range_bounds), 0)
                     AS INTEGER),
                49
            )
        END AS bucket
    FROM case_spans
)
GROUP BY bucket
ORDER BY bucket;
"""


def compute_throughput_per_day(cases: int, earliest: Any, latest: Any) -> float:
    if cases <= 0 or earliest is None or latest is None:
        return 0.0
    span_seconds = (latest - earliest).total_seconds()
    if span_seconds <= 0:
        return float(cases)
    return cases * 86400.0 / span_seconds


def quantile_from_sorted(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = (len(sorted_values) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(sorted_values[lo])
    frac = idx - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def log_spaced_histogram(values: list[float], buckets: int = 24) -> list[dict[str, Any]]:
    if not values:
        return []
    positives = [v for v in values if v > 0]
    if not positives:
        return [{"bucket_min": 0.0, "bucket_max": 0.0, "count": len(values)}]
    lo = min(positives)
    hi = max(positives)
    if lo == hi:
        return [{"bucket_min": lo, "bucket_max": hi, "count": len(positives)}]
    log_lo = math.log(lo)
    log_hi = math.log(hi)
    edges = [math.exp(log_lo + (log_hi - log_lo) * i / buckets) for i in range(buckets + 1)]
    counts = [0] * buckets
    for v in positives:
        if v <= 0:
            continue
        # find bucket; clamp to last
        idx = min(int((math.log(v) - log_lo) / (log_hi - log_lo) * buckets), buckets - 1)
        counts[idx] += 1
    return [
        {"bucket_min": float(edges[i]), "bucket_max": float(edges[i + 1]), "count": int(counts[i])}
        for i in range(buckets)
    ]


def detect_bottlenecks(
    activity_stats: list[dict[str, Any]],
    threshold_factor: float = 1.5,
) -> list[dict[str, Any]]:
    """Return activities with avg sojourn above median + factor*IQR."""
    if not activity_stats:
        return []
    sorted_stats = sorted(activity_stats, key=lambda s: s.get("avg_sojourn_s") or 0.0)
    sojourns = [s.get("avg_sojourn_s") or 0.0 for s in sorted_stats]
    median = quantile_from_sorted(sojourns, 0.5)
    q1 = quantile_from_sorted(sojourns, 0.25)
    q3 = quantile_from_sorted(sojourns, 0.75)
    iqr = q3 - q1
    threshold = median + threshold_factor * iqr if iqr > 0 else median
    flagged = [s for s in activity_stats if (s.get("avg_sojourn_s") or 0.0) >= threshold and (s.get("avg_sojourn_s") or 0.0) > 0]
    return sorted(flagged, key=lambda s: s.get("avg_sojourn_s") or 0.0, reverse=True)
