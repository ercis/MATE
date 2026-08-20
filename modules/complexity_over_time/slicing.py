"""Time slicing for the *Complexity over time* module.

Pure functions over a normalised event table (``case_id, activity,
timestamp``). The log is split along time into slices; the **whole case** is
assigned to a slice by its *start time* (the case's first event), so traces
stay intact and the EPA / entropy math in :mod:`complexity_core` remains
valid on each per-slice sub-log.

Three slicing modes:

* **absolute** - the timeline ``[min_start, max_start]`` is cut into ``N``
  equal-duration bins. Empty bins are kept (they become null points).
* **calendar** - cases bucketed by a calendar period (D/W/M/Q/Y). The full
  period range is reindexed so gaps render as nulls. ``granularity="auto"``
  targets ~20-60 points based on the timespan.
* **sliding** - overlapping windows ``[s, s+window)`` advancing by ``step``
  (both in days). A case is counted in every window its start falls in.

``compute_timeseries`` is the public entry point; it returns the result
schema consumed by the panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

# Vendored from ``modules/complexity`` so this module stays self-contained -
# see modules/complexity_over_time/manifest.yaml and the module plan.
from .complexity_core import compute_basic_metrics

# Hard cap so a tiny step / huge N can't spin up an unbounded number of
# slices (and an unbounded number of complexity runs).
_MAX_SLICES = 2000

_GRANULARITY_FREQ: dict[str, str] = {
    "daily": "D",
    "weekly": "W",
    "monthly": "M",
    "quarterly": "Q",
    "yearly": "Y",
}


@dataclass
class SliceSpec:
    """One time slice: its label, [start, end) bounds, and member case ids."""

    label: str
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    case_ids: list[Any]


# ── timestamp helpers ─────────────────────────────────────────────────────────

def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` with a datetime ``timestamp`` column (coerced if needed)."""
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def case_starts(df: pd.DataFrame) -> pd.Series:
    """Map each ``case_id`` to its first (minimum) timestamp."""
    df = _coerce(df)
    return df.groupby("case_id")["timestamp"].min()


def _iso(ts: pd.Timestamp | None) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).isoformat()


def _short(ts: pd.Timestamp | None) -> str:
    if ts is None or pd.isna(ts):
        return ""
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _single_slice(starts: pd.Series) -> list[SliceSpec]:
    """Degenerate case (no spread / one instant): everything in one slice."""
    if starts.empty:
        return []
    start = starts.min()
    return [
        SliceSpec(
            label=_short(start),
            start=start,
            end=starts.max(),
            case_ids=list(starts.index),
        )
    ]


# ── slice builders ────────────────────────────────────────────────────────────

def _build_absolute(starts: pd.Series, n: int) -> list[SliceSpec]:
    n = max(1, min(int(n), _MAX_SLICES))
    if starts.empty:
        return []
    min_s, max_s = starts.min(), starts.max()
    if pd.isna(min_s) or pd.isna(max_s) or min_s == max_s or n == 1:
        return _single_slice(starts)

    edges = pd.date_range(min_s, max_s, periods=n + 1)
    # date_range can collapse to fewer unique edges when the span is tiny.
    edges = edges.unique()
    if len(edges) < 2:
        return _single_slice(starts)

    cats = pd.cut(starts, bins=edges, include_lowest=True, right=True)
    codes = cats.cat.codes
    slices: list[SliceSpec] = []
    for i in range(len(edges) - 1):
        member = list(starts.index[codes == i])
        slices.append(
            SliceSpec(
                label=_short(edges[i]),
                start=edges[i],
                end=edges[i + 1],
                case_ids=member,
            )
        )
    return slices


def _auto_freq(timespan_days: float) -> str:
    if timespan_days <= 60:
        return "D"
    if timespan_days <= 540:
        return "W"
    if timespan_days <= 2200:
        return "M"
    return "Q"


def _build_calendar(starts: pd.Series, granularity: str) -> tuple[list[SliceSpec], str]:
    if starts.empty:
        return [], "D"
    min_s, max_s = starts.min(), starts.max()
    try:
        timespan_days = (max_s - min_s).total_seconds() / 86400.0
    except (TypeError, ValueError):
        timespan_days = 0.0

    if granularity == "auto":
        freq = _auto_freq(timespan_days)
    else:
        freq = _GRANULARITY_FREQ.get(granularity, "M")

    periods = starts.dt.to_period(freq)
    members: dict[Any, list[Any]] = {}
    for case_id, period in periods.items():
        members.setdefault(period, []).append(case_id)

    full = pd.period_range(start=min_s, end=max_s, freq=freq)
    slices: list[SliceSpec] = []
    for period in full:
        slices.append(
            SliceSpec(
                label=str(period),
                start=period.start_time,
                end=period.end_time,
                case_ids=members.get(period, []),
            )
        )
    return slices, freq


def _build_sliding(
    starts: pd.Series, window_days: float, step_days: float
) -> list[SliceSpec]:
    if starts.empty:
        return []
    window = pd.Timedelta(days=max(float(window_days), 1e-9))
    step = pd.Timedelta(days=max(float(step_days), 1e-9))
    min_s, max_s = starts.min(), starts.max()
    if pd.isna(min_s) or pd.isna(max_s) or min_s == max_s:
        return _single_slice(starts)

    slices: list[SliceSpec] = []
    cur = min_s
    while cur <= max_s and len(slices) < _MAX_SLICES:
        win_end = cur + window
        mask = (starts >= cur) & (starts < win_end)
        slices.append(
            SliceSpec(
                label=_short(cur),
                start=cur,
                end=win_end,
                case_ids=list(starts.index[mask]),
            )
        )
        cur = cur + step
    return slices


# ── metric-key extraction ─────────────────────────────────────────────────────

def _numeric_metric_keys(metrics: dict[str, Any]) -> list[str]:
    """Numeric (or nullable-numeric) metric keys, excluding ``exponential_k``.

    ``compute_basic_metrics`` always returns the full key set with consistent
    keys (some values may be ``None`` for tiny slices), so the keys from any
    one populated slice describe the whole series.
    """
    keys: list[str] = []
    for key, value in metrics.items():
        if key == "exponential_k":
            continue
        if isinstance(value, bool):
            continue
        if value is None or isinstance(value, (int, float)):
            keys.append(key)
    return keys


# ── public entry point ────────────────────────────────────────────────────────

def compute_timeseries(
    df: pd.DataFrame,
    mode: str,
    params: dict[str, Any],
    *,
    exponential_k: float = 1.0,
    min_cases: int = 1,
) -> dict[str, Any]:
    """Build time slices and run the complexity math on each sub-log.

    Returns the ``complexity_timeseries`` result schema: ``metric_keys`` (the
    dropdown source) plus one point per slice carrying all KPIs, so the panel
    can switch the Y-axis metric without refetching.
    """
    mode = mode if mode in ("absolute", "calendar", "sliding") else "calendar"
    min_cases = max(1, int(min_cases))

    df = _coerce(df)
    starts = case_starts(df)

    resolved_params: dict[str, Any] = {}
    if mode == "absolute":
        n = int(params.get("slices", 50) or 50)
        specs = _build_absolute(starts, n)
        resolved_params = {"slices": n}
    elif mode == "sliding":
        window = float(params.get("window", 30.0) or 30.0)
        step = float(params.get("step", 7.0) or 7.0)
        specs = _build_sliding(starts, window, step)
        resolved_params = {"window": window, "step": step}
    else:
        granularity = str(params.get("granularity", "auto") or "auto")
        specs, freq = _build_calendar(starts, granularity)
        resolved_params = {"granularity": granularity, "freq": freq}

    metric_keys: list[str] | None = None
    slices_out: list[dict[str, Any]] = []
    for i, spec in enumerate(specs):
        n_cases = len(spec.case_ids)
        metrics: dict[str, Any] | None = None
        n_events = 0
        if n_cases >= min_cases:
            sub_df = df[df["case_id"].isin(spec.case_ids)]
            metrics = compute_basic_metrics(sub_df, exponential_k=exponential_k) or None
            n_events = len(sub_df)
            if metrics is not None and metric_keys is None:
                metric_keys = _numeric_metric_keys(metrics)
        slices_out.append(
            {
                "index": i,
                "label": spec.label,
                "start": _iso(spec.start),
                "end": _iso(spec.end),
                "n_cases": n_cases,
                "n_events": n_events,
                "metrics": metrics,
            }
        )

    if metric_keys is None:
        # No slice cleared min_cases - fall back to the whole-log key set so
        # the dropdown is still populated.
        full = compute_basic_metrics(df, exponential_k=exponential_k)
        metric_keys = _numeric_metric_keys(full) if full else []

    return {
        "kind": "complexity_timeseries",
        "mode": mode,
        "params": resolved_params,
        "metric_keys": metric_keys,
        "slices": slices_out,
    }
