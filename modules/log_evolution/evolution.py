"""Log-evolution aggregations - how the event log develops over time.

Pure functions over a normalised event table (``case_id, activity,
timestamp``). No ``ModuleContext`` is needed, so every function here is unit
tested directly against a synthetic DataFrame (mirrors the approach in
:mod:`modules.complexity_over_time.slicing`).

Two entry points:

* :func:`compute_evolution` - per-period volume series (case arrivals,
  completions, work-in-progress, total events, and the activity mix), all
  reindexed onto the full calendar range so gaps render as explicit zeros.
* :func:`compute_dotted` - the classic *dotted chart*: one point per event,
  ``x = time``, ``y = case rank`` (cases ordered by start time), coloured by
  activity. Deterministically down-sampled above ``max_points``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Calendar period codes accepted by ``pandas.Series.dt.to_period``.
_GRANULARITY_FREQ: dict[str, str] = {
    "daily": "D",
    "weekly": "W",
    "monthly": "M",
    "quarterly": "Q",
    "yearly": "Y",
}

# How many activities the activity-mix bundle keeps as their own series before
# folding the rest into "Other". The widget slices this further via its own
# ``top_n``; keeping a generous cap here means the widget can re-fold accurately.
_BUNDLE_TOP_ACTIVITIES = 12

# Legend cap for the dotted chart (one colour per activity, rest → "Other").
_DOTTED_TOP_ACTIVITIES = 12

# Absolute ceiling on returned dotted-chart points, regardless of ``max_points``,
# so a huge log can't produce an unbounded payload.
_DOTTED_HARD_CAP = 20_000


# ── timestamp helpers (mirrors complexity_over_time/slicing.py) ────────────────


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` with a datetime ``timestamp`` column and no NaT rows."""
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df.dropna(subset=["timestamp"])


def _auto_freq(timespan_days: float) -> str:
    if timespan_days <= 60:
        return "D"
    if timespan_days <= 540:
        return "W"
    if timespan_days <= 2200:
        return "M"
    return "Q"


def _resolve_freq(df: pd.DataFrame, granularity: str) -> str:
    if granularity != "auto":
        return _GRANULARITY_FREQ.get(granularity, "M")
    min_s, max_s = df["timestamp"].min(), df["timestamp"].max()
    try:
        timespan_days = (max_s - min_s).total_seconds() / 86400.0
    except (TypeError, ValueError):
        timespan_days = 0.0
    return _auto_freq(timespan_days)


def _empty_evolution(granularity: str) -> dict[str, Any]:
    return {
        "kind": "log_evolution",
        "granularity": granularity,
        "freq": "",
        "periods": [],
        "arrivals": [],
        "completions": [],
        "active": [],
        "events": [],
        "activity_mix": {"activities": [], "series": []},
    }


# ── per-period volume series ───────────────────────────────────────────────────


def compute_evolution(df: pd.DataFrame, granularity: str = "auto") -> dict[str, Any]:
    """Per-period arrivals / completions / WIP / events / activity-mix series.

    A case *arrives* in the period of its first event and *completes* in the
    period of its last event. Work-in-progress (``active``) at the end of each
    period is the running open-case count - ``cumsum(arrivals) -
    cumsum(completions)`` - which is non-negative and returns to zero once every
    case has completed.
    """
    df = _coerce(df)
    if df.empty:
        return _empty_evolution(granularity)

    freq = _resolve_freq(df, granularity)
    full = pd.period_range(start=df["timestamp"].min(), end=df["timestamp"].max(), freq=freq)

    case_ts = df.groupby("case_id")["timestamp"]
    starts = case_ts.min().dt.to_period(freq)
    ends = case_ts.max().dt.to_period(freq)
    event_periods = df["timestamp"].dt.to_period(freq)

    def _counts(periods: pd.Series | pd.PeriodIndex) -> list[int]:
        counts = pd.Series(1, index=pd.PeriodIndex(periods)).groupby(level=0).sum()
        return counts.reindex(full, fill_value=0).astype(int).tolist()

    arrivals = _counts(starts)
    completions = _counts(ends)
    events = _counts(event_periods)

    active: list[int] = []
    open_count = 0
    for arr, comp in zip(arrivals, completions, strict=True):
        open_count += arr - comp
        active.append(open_count)

    activity_mix = _activity_mix(df, event_periods, full)

    return {
        "kind": "log_evolution",
        "granularity": granularity,
        "freq": freq,
        "periods": [str(p) for p in full],
        "arrivals": arrivals,
        "completions": completions,
        "active": active,
        "events": events,
        "activity_mix": activity_mix,
    }


def _activity_mix(
    df: pd.DataFrame,
    event_periods: pd.Series,
    full: pd.PeriodIndex,
) -> dict[str, Any]:
    """Top activities as their own per-period series; the rest folded to "Other"."""
    freqs = df["activity"].value_counts()
    top = list(freqs.head(_BUNDLE_TOP_ACTIVITIES).index)
    has_other = len(freqs) > len(top)

    bucketed = df["activity"].where(df["activity"].isin(top), "Other")
    grouped = (
        pd.DataFrame({"period": event_periods.to_numpy(), "activity": bucketed.to_numpy()})
        .groupby(["activity", "period"])
        .size()
    )

    activities = [*top, *(["Other"] if has_other else [])]
    series: list[list[int]] = []
    for act in activities:
        if act in grouped.index.get_level_values(0):
            row = grouped.loc[act].reindex(full, fill_value=0).astype(int)
        else:
            row = pd.Series(0, index=full, dtype=int)
        series.append(row.tolist())

    return {"activities": activities, "series": series}


# ── dotted chart ───────────────────────────────────────────────────────────────


def compute_dotted(df: pd.DataFrame, max_points: int = 8000) -> dict[str, Any]:
    """One point per event for a dotted chart, ranked by case start time.

    ``y`` is the case's dense rank (0-based) when cases are ordered by their
    start time; ``t`` is the event time in epoch milliseconds; ``a`` indexes
    into the returned ``activities`` legend (top activities by frequency, the
    rest bucketed into a trailing "Other"). Above ``max_points`` the events are
    deterministically down-sampled and ``sampled`` is set.
    """
    df = _coerce(df)
    total = len(df)
    if total == 0:
        return {
            "kind": "log_dotted",
            "total_events": 0,
            "sampled": False,
            "max_points": int(max_points),
            "n_cases": 0,
            "activities": [],
            "points": [],
        }

    # Dense case rank by start time → the y-axis order.
    order = df.groupby("case_id")["timestamp"].min().sort_values()
    rank = {case_id: i for i, case_id in enumerate(order.index)}

    # Activity legend: top-N by frequency, everything else → trailing "Other".
    freqs = df["activity"].value_counts()
    top = list(freqs.head(_DOTTED_TOP_ACTIVITIES).index)
    act_index = {act: i for i, act in enumerate(top)}
    other_index = len(top)
    has_other = len(freqs) > len(top)
    activities = [*top, *(["Other"] if has_other else [])]

    cap = max(1, min(int(max_points), _DOTTED_HARD_CAP))
    sampled = total > cap
    work = df.sample(n=cap, random_state=0) if sampled else df

    points: list[dict[str, int]] = []
    for case_id, activity, ts in zip(
        work["case_id"], work["activity"], work["timestamp"], strict=True
    ):
        points.append(
            {
                "y": rank[case_id],
                "t": int(pd.Timestamp(ts).value // 1_000_000),
                "a": act_index.get(activity, other_index),
            }
        )

    return {
        "kind": "log_dotted",
        "total_events": total,
        "sampled": sampled,
        "max_points": cap,
        "n_cases": len(order),
        "activities": activities,
        "points": points,
    }
