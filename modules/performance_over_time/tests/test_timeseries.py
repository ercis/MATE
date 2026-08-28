"""Unit tests for the time-slicing + per-slice performance series.

Exercises the pure functions in :mod:`modules.performance_over_time.slicing`
against a synthetic log spanning several months - no ``ModuleContext`` needed.
"""

from __future__ import annotations

import pandas as pd
from modules.performance_over_time.perf_core import compute_perf_metrics
from modules.performance_over_time.slicing import case_starts, compute_timeseries


def _synthetic_log(
    months: int = 6,
    cases_per_month: int = 8,
    variants: int = 3,
) -> pd.DataFrame:
    """Build a multi-month log: each case is a short trace; case start time is
    spread across ``months`` so every slicing mode has something to bucket.

    Within a variant every case shares the same trace length, so a case's span
    is a deterministic ``(len - 1)`` hours - handy for the cycle-time checks.
    """
    rows: list[dict[str, object]] = []
    activity_pool = ["submit", "review", "approve", "pay", "close", "escalate"]
    case_counter = 0
    base = pd.Timestamp("2023-01-05")
    for m in range(months):
        month_start = base + pd.DateOffset(months=m)
        for c in range(cases_per_month):
            case_counter += 1
            case_id = f"c{case_counter}"
            # Stagger case start across the month; pick a variant by rotation.
            start = month_start + pd.Timedelta(days=(c % 25), hours=c)
            variant = c % variants
            trace = activity_pool[: 2 + variant]  # length 2..(1+variants)
            for step, act in enumerate(trace):
                rows.append(
                    {
                        "case_id": case_id,
                        "activity": act,
                        "timestamp": start + pd.Timedelta(hours=step),
                    }
                )
    return pd.DataFrame(rows)


# ── case_starts ───────────────────────────────────────────────────────────────


def test_case_starts_is_first_event_per_case():
    df = _synthetic_log(months=2, cases_per_month=3)
    starts = case_starts(df)
    assert set(starts.index) == set(df["case_id"].unique())
    for case_id, start in starts.items():
        assert start == df[df["case_id"] == case_id]["timestamp"].min()


# ── absolute mode ─────────────────────────────────────────────────────────────


def test_absolute_returns_requested_slice_count():
    df = _synthetic_log(months=6)
    out = compute_timeseries(df, "absolute", {"slices": 12}, min_cases=1)
    assert out["mode"] == "absolute"
    assert out["params"]["slices"] == 12
    assert len(out["slices"]) == 12
    # Every case lands in exactly one absolute bin.
    total_cases = sum(p["n_cases"] for p in out["slices"])
    assert total_cases == df["case_id"].nunique()


def test_absolute_metric_keys_match_compute_perf_metrics():
    df = _synthetic_log(months=6)
    out = compute_timeseries(df, "absolute", {"slices": 6}, min_cases=1)
    assert out["metric_keys"], "metric_keys must be non-empty"
    assert "avg_cycle_time_s" in out["metric_keys"]
    assert "throughput_cases_per_day" in out["metric_keys"]

    expected = set(compute_perf_metrics(df))
    assert set(out["metric_keys"]) == expected

    populated = next(p for p in out["slices"] if p["metrics"] is not None)
    assert set(out["metric_keys"]).issubset(set(populated["metrics"]))


# ── calendar mode ─────────────────────────────────────────────────────────────


def test_calendar_monthly_covers_every_month_no_gaps():
    df = _synthetic_log(months=6)
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    assert out["mode"] == "calendar"
    assert out["params"]["freq"] == "M"
    # Jan..Jun 2023 inclusive → 6 contiguous month periods.
    labels = [p["label"] for p in out["slices"]]
    assert labels == ["2023-01", "2023-02", "2023-03", "2023-04", "2023-05", "2023-06"]
    assert all(p["n_cases"] > 0 for p in out["slices"])


def test_calendar_auto_picks_a_frequency():
    df = _synthetic_log(months=6)
    out = compute_timeseries(df, "calendar", {"granularity": "auto"}, min_cases=1)
    assert out["params"]["granularity"] == "auto"
    assert out["params"]["freq"] in {"D", "W", "M", "Q", "Y"}
    assert len(out["slices"]) >= 1


def test_calendar_reindex_yields_null_gap_for_empty_period():
    # Two clusters of cases with a multi-month hole between them.
    early = _synthetic_log(months=1, cases_per_month=5)
    late = _synthetic_log(months=1, cases_per_month=5)
    late = late.copy()
    late["timestamp"] = late["timestamp"] + pd.DateOffset(months=4)
    late["case_id"] = late["case_id"] + "_late"
    df = pd.concat([early, late], ignore_index=True)

    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    empty = [p for p in out["slices"] if p["n_cases"] == 0]
    assert empty, "the gap months should appear as slices"
    assert all(p["metrics"] is None for p in empty)


# ── sliding mode ──────────────────────────────────────────────────────────────


def test_sliding_windows_overlap_and_cover_span():
    df = _synthetic_log(months=4)
    out = compute_timeseries(df, "sliding", {"window": 30.0, "step": 15.0}, min_cases=1)
    assert out["mode"] == "sliding"
    assert out["params"] == {"window": 30.0, "step": 15.0}
    assert len(out["slices"]) >= 2
    # Overlapping windows (step < window) ⇒ a case can be counted more than once,
    # so the summed n_cases exceeds the distinct case count.
    total = sum(p["n_cases"] for p in out["slices"])
    assert total >= df["case_id"].nunique()


# ── min_cases gating / null points ────────────────────────────────────────────


def test_min_cases_blanks_thin_slices():
    df = _synthetic_log(months=6, cases_per_month=8)
    high = compute_timeseries(df, "absolute", {"slices": 40}, min_cases=5)
    thin = [p for p in high["slices"] if 0 < p["n_cases"] < 5]
    assert thin, "expected some slices below the threshold with many bins"
    assert all(p["metrics"] is None for p in thin)
    # metric_keys still populated from a slice that cleared the threshold.
    assert high["metric_keys"]


def test_metric_keys_populated_even_when_all_slices_thin():
    df = _synthetic_log(months=2, cases_per_month=4)
    out = compute_timeseries(df, "absolute", {"slices": 2}, min_cases=10_000)
    assert all(p["metrics"] is None for p in out["slices"])
    assert out["metric_keys"], "fallback to whole-log key set"


# ── edge cases ────────────────────────────────────────────────────────────────


def test_non_datetime_timestamps_are_coerced():
    df = _synthetic_log(months=3)
    df = df.copy()
    df["timestamp"] = df["timestamp"].astype(str)
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    assert len(out["slices"]) == 3
    assert any(p["metrics"] is not None for p in out["slices"])


def test_degenerate_single_instant_collapses_to_one_slice():
    rows = [
        {"case_id": f"c{i}", "activity": a, "timestamp": pd.Timestamp("2023-03-01")}
        for i in range(4)
        for a in ("submit", "approve")
    ]
    df = pd.DataFrame(rows)
    out = compute_timeseries(df, "absolute", {"slices": 10}, min_cases=1)
    assert len(out["slices"]) == 1
    assert out["slices"][0]["n_cases"] == 4


# ── cycle-time / KPI correctness ──────────────────────────────────────────────


def test_compute_perf_metrics_known_cycle_times():
    # Four cases, each two events exactly 1 hour (3600 s) apart → every span is
    # 3600 s, so avg/median/min/max cycle time all equal 3600 s.
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2023-05-01")
    for i in range(4):
        start = base + pd.Timedelta(days=i)
        rows.append({"case_id": f"c{i}", "activity": "submit", "timestamp": start})
        rows.append(
            {"case_id": f"c{i}", "activity": "approve", "timestamp": start + pd.Timedelta(hours=1)}
        )
    df = pd.DataFrame(rows)

    m = compute_perf_metrics(df)
    assert m["cases"] == 4
    assert m["events"] == 8
    assert m["variants"] == 1  # every case is submit → approve
    for key in (
        "avg_cycle_time_s",
        "median_cycle_time_s",
        "p90_cycle_time_s",
        "p95_cycle_time_s",
        "min_cycle_time_s",
        "max_cycle_time_s",
    ):
        assert abs(m[key] - 3600.0) < 1e-6, key
    assert m["lead_time_s"] == m["avg_cycle_time_s"]
    # Throughput = cases per day over the earliest→latest *event* span (matches
    # the performance module): first event day0 00:00, last event day3 01:00.
    span_s = (df["timestamp"].max() - df["timestamp"].min()).total_seconds()
    assert abs(m["throughput_cases_per_day"] - 4 * 86400.0 / span_s) < 1e-6


def test_cycle_time_series_matches_expected_average_within_tolerance():
    df = _synthetic_log(months=6, cases_per_month=8, variants=3)
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    # Recompute the avg cycle time for one populated month directly from the log
    # and confirm the series carries the same number.
    point = next(p for p in out["slices"] if p["metrics"] is not None)
    label = point["label"]
    period = pd.Period(label, freq="M")
    starts = case_starts(df)
    member_cases = [cid for cid, s in starts.items() if s.to_period("M") == period]
    sub = pd.DataFrame(df[df["case_id"].isin(member_cases)])
    expected = compute_perf_metrics(sub)
    assert abs(point["metrics"]["avg_cycle_time_s"] - expected["avg_cycle_time_s"]) < 1e-6
    assert point["metrics"]["cases"] == len(member_cases)


def test_empty_dataframe_is_defensive():
    empty = pd.DataFrame({"case_id": [], "activity": [], "timestamp": []})
    m = compute_perf_metrics(empty)
    assert m["cases"] == 0
    assert m["avg_cycle_time_s"] == 0.0
    assert m["throughput_cases_per_day"] == 0.0
