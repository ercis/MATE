"""Aggregate raw df-instance rows into the cached result shape (promg-free, pure).

Input rows per transition: ``{start_ms, duration_ms, actor_behavior}``. Output mirrors
the reference implementation's aggregated CSV (count / percentage / mean per behavior
class and an ``all`` roll-up per transition) plus medians, p90s and log-wide totals the
panel charts. Durations are exact epoch-millisecond diffs converted to hours.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

BEHAVIOR_CLASSES = (
    "continuation",
    "interruption",
    "handover_idle",
    "handover_prioritized",
    "handover_deprioritized",
)
# Edges the classification queries never matched (shouldn't happen; kept honest).
UNCLASSIFIED = "unclassified"

_MS_PER_HOUR = 3_600_000.0


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile on pre-sorted values (numpy-free)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def _stats(durations_ms: list[float], denominator: int) -> dict[str, Any]:
    hours = sorted(d / _MS_PER_HOUR for d in durations_ms)
    n = len(hours)
    return {
        "count": n,
        "percentage": (n / denominator) if denominator else 0.0,
        "mean_hours": (sum(hours) / n) if n else 0.0,
        "median_hours": _percentile(hours, 0.5),
        "p90_hours": _percentile(hours, 0.9),
    }


def aggregate_edge(
    edge_key: Mapping[str, str], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """One transition's decomposition. ``edge_key`` carries activity1/lifecycle1/
    activity2/lifecycle2 as returned by the edge-listing query."""
    by_class: dict[str, list[float]] = {}
    for row in rows:
        behavior = row.get("actor_behavior") or UNCLASSIFIED
        by_class.setdefault(str(behavior), []).append(float(row.get("duration_ms") or 0.0))

    total = sum(len(v) for v in by_class.values())
    behaviors: dict[str, Any] = {"all": _stats([d for v in by_class.values() for d in v], total)}
    for name in (*BEHAVIOR_CLASSES, UNCLASSIFIED):
        if name in by_class:
            behaviors[name] = _stats(by_class[name], total)

    return {
        "source_activity": edge_key["activity1"],
        "source_lifecycle": edge_key.get("lifecycle1") or "",
        "sink_activity": edge_key["activity2"],
        "sink_lifecycle": edge_key.get("lifecycle2") or "",
        "count": total,
        "behaviors": behaviors,
    }


def behavior_totals(edges: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Log-wide roll-up across every extracted transition, weighted by instances."""
    totals: dict[str, dict[str, float]] = {}
    grand_total = 0
    for edge in edges:
        behaviors = edge.get("behaviors")
        if not isinstance(behaviors, Mapping):
            continue
        for name, stats in behaviors.items():
            if name == "all" or not isinstance(stats, Mapping):
                continue
            slot = totals.setdefault(str(name), {"count": 0.0, "duration_hours_sum": 0.0})
            count = float(stats.get("count") or 0)
            slot["count"] += count
            slot["duration_hours_sum"] += float(stats.get("mean_hours") or 0.0) * count
        all_stats = behaviors.get("all")
        if isinstance(all_stats, Mapping):
            grand_total += int(all_stats.get("count") or 0)

    out: dict[str, Any] = {}
    for name, slot in totals.items():
        count = int(slot["count"])
        out[name] = {
            "count": count,
            "percentage": (count / grand_total) if grand_total else 0.0,
            "mean_hours": (slot["duration_hours_sum"] / count) if count else 0.0,
        }
    return out
