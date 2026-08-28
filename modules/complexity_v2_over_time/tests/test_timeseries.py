"""Unit tests for the v2 time-slicing + per-slice metric series.

Exercises the pure functions in :mod:`modules.complexity_v2_over_time.slicing`
against synthetic logs - the windowing behaviour mirrors complexity_over_time,
but every slice is scored with the full Langer Table 3.3 suite
(:func:`modules.complexity_v2_over_time.metrics_core.compute_all`). No
``ModuleContext`` needed.
"""

from __future__ import annotations

import math

import pandas as pd
from modules.complexity_v2_over_time.metrics_core import METRIC_DEFS, compute_all
from modules.complexity_v2_over_time.slicing import case_starts, compute_timeseries

_ALL_METRIC_KEYS = [d["key"] for d in METRIC_DEFS]
# compute_all run-metadata that must never leak into the Y-axis dropdown source.
_META_KEYS = {
    "df_edges",
    "n_acyclic_paths_log10",
    "enriched_supported",
    "downsampled",
    "distance_variants_used",
    "max_variants",
    "empty",
}


def _synthetic_log(
    months: int = 6,
    cases_per_month: int = 8,
    variants: int = 3,
) -> pd.DataFrame:
    """Build a multi-month log: each case is a short trace; case start time is
    spread across ``months`` so every slicing mode has something to bucket."""
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
    assert out["kind"] == "complexity_v2_timeseries"
    assert out["mode"] == "absolute"
    assert out["params"]["slices"] == 12
    assert len(out["slices"]) == 12
    # Every case lands in exactly one absolute bin.
    total_cases = sum(p["n_cases"] for p in out["slices"])
    assert total_cases == df["case_id"].nunique()


def test_absolute_metric_keys_are_the_full_paper_suite():
    df = _synthetic_log(months=6)
    out = compute_timeseries(df, "absolute", {"slices": 6}, min_cases=1)
    assert out["metric_keys"], "metric_keys must be non-empty"
    # Every Table 3.3 metric, in paper order; no compute_all metadata leaks.
    assert out["metric_keys"] == _ALL_METRIC_KEYS
    assert _META_KEYS.isdisjoint(out["metric_keys"])
    assert "var_e" in out["metric_keys"]
    assert "avg_edit_distance" in out["metric_keys"]

    # metric_keys == the numeric/nullable keys compute_all reports on the log.
    full = compute_all(df)
    expected = [
        k
        for k in _ALL_METRIC_KEYS
        if not isinstance(full.get(k), bool)
        and (full.get(k) is None or isinstance(full.get(k), (int, float)))
    ]
    assert out["metric_keys"] == expected

    populated = next(p for p in out["slices"] if p["metrics"] is not None)
    assert set(out["metric_keys"]).issubset(set(populated["metrics"]))


def test_per_slice_metrics_carry_distance_measures():
    # A slice with several variants must score the (expensive) distance metrics.
    df = _synthetic_log(months=6, cases_per_month=8, variants=3)
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=2)
    populated = [p for p in out["slices"] if p["metrics"] is not None]
    assert populated
    m = populated[0]["metrics"]
    assert m["avg_edit_distance"] is not None and m["avg_edit_distance"] >= 0
    assert m["structural_var"] is not None and m["structural_var"] >= 0
    assert 0.0 <= m["structure"] <= 1.0


def test_max_variants_downsamples_per_slice():
    # 20 distinct variants per slice, cap at 5 → the per-slice suite downsamples.
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2023-01-01")
    for i, cid in enumerate(range(1, 21)):
        start = base + pd.Timedelta(days=cid)
        for step, act in enumerate(["start", f"branch{i}", "end"]):
            rows.append(
                {
                    "case_id": f"c{cid}",
                    "activity": act,
                    "timestamp": start + pd.Timedelta(hours=step),
                }
            )
    df = pd.DataFrame(rows)
    out = compute_timeseries(df, "absolute", {"slices": 1}, max_variants=5, min_cases=1)
    m = next(p["metrics"] for p in out["slices"] if p["metrics"] is not None)
    assert m["avg_edit_distance"] is not None


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
    assert out["metric_keys"] == _ALL_METRIC_KEYS, "fallback to whole-log key set"


# ── enriched entropy passthrough ──────────────────────────────────────────────


def test_enriched_schema_populates_enriched_metrics_per_slice():
    df = _synthetic_log(months=3, cases_per_month=6)
    schema = {
        "trace_attributes": ["variant", "concept:name", "creator", "variant-index"],
        "event_attributes": [
            "time:timestamp",
            "Resource",
            "lifecycle:transition",
            "concept:name",
            "Activity",
            "org:resource",
        ],
    }
    out = compute_timeseries(
        df, "calendar", {"granularity": "monthly"}, detected_schema=schema, min_cases=1
    )
    populated = [p for p in out["slices"] if p["metrics"] is not None]
    assert populated
    assert any(p["metrics"]["en_var_e"] is not None for p in populated)


def test_enriched_metrics_none_without_schema():
    df = _synthetic_log(months=3, cases_per_month=6)
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    populated = [p for p in out["slices"] if p["metrics"] is not None]
    assert populated
    for key in ("en_var_e", "en_seq_e", "en_nvar_e", "en_nseq_e"):
        assert all(p["metrics"][key] is None for p in populated)
        # ...but the keys are still offered in the dropdown.
        assert key in out["metric_keys"]


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


def test_acyclic_paths_overflow_is_guarded_in_pipeline():
    """A slice whose DF-edge count dwarfs its activity count makes
    10**(0.08*(1+e-v)) overflow inside compute_all. It must degrade to a None
    metric (with a finite log10) rather than failing the whole series."""
    acts = [f"a{i}" for i in range(70)]
    seq: list[str] = []
    for a in acts:
        for b in acts:
            seq.extend((a, b))
    t0 = pd.Timestamp("2023-01-01")
    df = pd.DataFrame(
        {
            "case_id": "c1",
            "activity": seq,
            "timestamp": [t0 + pd.Timedelta(seconds=i) for i in range(len(seq))],
        }
    )
    out = compute_timeseries(df, "calendar", {"granularity": "yearly"}, min_cases=1)
    populated = [p for p in out["slices"] if p["metrics"] is not None]
    assert populated
    m = populated[0]["metrics"]
    assert m["n_acyclic_paths"] is None
    assert math.isfinite(m["n_acyclic_paths_log10"])


def test_nat_timestamps_survive_slicing():
    df = _synthetic_log(months=3)
    df = df.copy()
    df.loc[df.index[::7], "timestamp"] = pd.NaT
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    assert any(p["metrics"] is not None for p in out["slices"])


def test_slice_metrics_are_json_safe():
    import json

    df = _synthetic_log(months=4)
    out = compute_timeseries(df, "calendar", {"granularity": "monthly"}, min_cases=1)
    json.dumps(out, allow_nan=False)


def test_progress_callback_ticks_once_per_computed_slice():
    df = _synthetic_log(months=6)
    ticks: list[tuple[float, str]] = []
    out = compute_timeseries(
        df,
        "calendar",
        {"granularity": "monthly"},
        min_cases=1,
        progress=lambda f, m: ticks.append((f, m)),
    )
    computed = [p for p in out["slices"] if p["n_cases"] >= 1]
    assert len(ticks) == len(computed)
    fractions = [f for f, _ in ticks]
    assert fractions == sorted(fractions)
    assert all(0.0 <= f <= 1.0 for f in fractions)
