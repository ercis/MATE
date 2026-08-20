"""Unit tests for the log-evolution aggregations.

Exercises the pure functions in :mod:`modules.log_evolution.evolution` against
a synthetic log spanning several months - no ``ModuleContext`` needed.
"""

from __future__ import annotations

import pandas as pd
from modules.log_evolution.evolution import compute_dotted, compute_evolution


def _synthetic_log(
    months: int = 6,
    cases_per_month: int = 8,
    activities: int = 4,
) -> pd.DataFrame:
    """Multi-month log: each case is a short trace; case start spread across the
    timeline so every period has something to bucket."""
    rows: list[dict[str, object]] = []
    pool = ["submit", "review", "approve", "pay", "close", "escalate", "reject"]
    case_counter = 0
    base = pd.Timestamp("2023-01-05")
    for m in range(months):
        month_start = base + pd.DateOffset(months=m)
        for c in range(cases_per_month):
            case_counter += 1
            case_id = f"c{case_counter}"
            start = month_start + pd.Timedelta(days=(c % 25), hours=c)
            trace = pool[: 2 + (c % (activities - 1))]
            for step, act in enumerate(trace):
                rows.append(
                    {
                        "case_id": case_id,
                        "activity": act,
                        "timestamp": start + pd.Timedelta(hours=step),
                    }
                )
    return pd.DataFrame(rows)


# ── compute_evolution ─────────────────────────────────────────────────────────


def test_evolution_shape_and_period_alignment():
    df = _synthetic_log()
    out = compute_evolution(df, "monthly")
    assert out["kind"] == "log_evolution"
    n = len(out["periods"])
    # Every series is aligned to the same period axis.
    for key in ("arrivals", "completions", "active", "events"):
        assert len(out[key]) == n
    for row in out["activity_mix"]["series"]:
        assert len(row) == n


def test_arrivals_and_completions_sum_to_case_count():
    df = _synthetic_log(months=4, cases_per_month=5)
    out = compute_evolution(df, "monthly")
    n_cases = df["case_id"].nunique()
    assert sum(out["arrivals"]) == n_cases
    assert sum(out["completions"]) == n_cases


def test_events_sum_to_total_events():
    df = _synthetic_log()
    out = compute_evolution(df, "monthly")
    assert sum(out["events"]) == len(df)


def test_active_is_non_negative_and_drains_to_zero():
    df = _synthetic_log()
    out = compute_evolution(df, "weekly")
    assert all(v >= 0 for v in out["active"])
    # Every case completes within range → no open cases at the end.
    assert out["active"][-1] == 0


def test_activity_mix_rows_sum_to_events_per_period():
    df = _synthetic_log()
    out = compute_evolution(df, "monthly")
    series = out["activity_mix"]["series"]
    per_period_totals = [sum(col) for col in zip(*series, strict=True)]
    assert per_period_totals == out["events"]


def test_activity_mix_buckets_into_other_when_over_cap():
    # Force >12 distinct activities so "Other" must appear.
    rows = [
        {
            "case_id": f"c{i}",
            "activity": f"act{i}",
            "timestamp": pd.Timestamp("2023-01-01") + pd.Timedelta(days=i),
        }
        for i in range(20)
    ]
    out = compute_evolution(pd.DataFrame(rows), "daily")
    assert "Other" in out["activity_mix"]["activities"]
    assert len(out["activity_mix"]["activities"]) == 13  # 12 named + Other


def test_evolution_empty_log():
    df = pd.DataFrame({"case_id": [], "activity": [], "timestamp": pd.to_datetime([])})
    out = compute_evolution(df, "auto")
    assert out["periods"] == []
    assert out["arrivals"] == []


# ── compute_dotted ────────────────────────────────────────────────────────────


def test_dotted_y_ranks_are_dense_by_start_time():
    df = _synthetic_log(months=2, cases_per_month=4)
    out = compute_dotted(df, max_points=10000)
    n_cases = df["case_id"].nunique()
    assert out["n_cases"] == n_cases
    ys = {p["y"] for p in out["points"]}
    assert ys == set(range(n_cases))
    assert not out["sampled"]


def test_dotted_respects_max_points_cap():
    df = _synthetic_log(months=6, cases_per_month=20)
    cap = 100
    out = compute_dotted(df, max_points=cap)
    assert out["sampled"] is True
    assert len(out["points"]) == cap
    assert out["total_events"] == len(df)


def test_dotted_is_deterministic():
    df = _synthetic_log(months=4, cases_per_month=20)
    a = compute_dotted(df, max_points=50)
    b = compute_dotted(df, max_points=50)
    assert a["points"] == b["points"]


def test_dotted_activity_indices_in_range():
    df = _synthetic_log()
    out = compute_dotted(df, max_points=10000)
    n_act = len(out["activities"])
    assert all(0 <= p["a"] < n_act for p in out["points"])


def test_dotted_empty_log():
    df = pd.DataFrame({"case_id": [], "activity": [], "timestamp": pd.to_datetime([])})
    out = compute_dotted(df)
    assert out["points"] == []
    assert out["total_events"] == 0
