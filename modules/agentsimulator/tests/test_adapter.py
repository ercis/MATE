"""Unit tests for the pure helpers in :mod:`modules.agentsimulator.adapter` and
:mod:`modules.agentsimulator.metrics`.

These exercise input preparation, the real-vs-simulated comparison summaries,
the numpy+scipy fidelity scoring, and column alignment against synthetic logs -
no simulator venv and no ``ModuleContext`` needed. They run against the
platform's pandas + scipy.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest
from modules.agentsimulator import adapter, metrics

# Importing the module-private guard is intentional: the plan factors the
# /results staleness check into this pure helper precisely so it's unit-testable.
from modules.agentsimulator.module import _result_is_current  # pyright: ignore[reportPrivateUsage]


def _canonical_log(n_cases: int = 25) -> pd.DataFrame:
    """A Mate-canonical event log: case_id / activity / timestamp / end_timestamp / resource."""
    base = pd.Timestamp("2021-03-01 08:00", tz="UTC")
    acts = ["receive", "review", "decide", "notify"]
    rows: list[dict[str, object]] = []
    for c in range(n_cases):
        start = base + pd.Timedelta(days=c % 14, hours=c % 6)
        for i, a in enumerate(acts):
            s = start + pd.Timedelta(hours=2 * i)
            rows.append(
                {
                    "case_id": f"req-{c:04d}",
                    "activity": a,
                    "timestamp": s,
                    "end_timestamp": s + pd.Timedelta(hours=1),
                    "resource": f"user_{(c + i) % 3}",
                }
            )
    return pd.DataFrame(rows)


def _agentsim_shapes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert a canonical log into the (test_preprocessed, simulated_log) shapes
    AgentSimulator writes - the inputs to the comparison helpers."""
    test_df = df.rename(columns={"activity": "activity_name", "timestamp": "start_timestamp"})[
        ["case_id", "activity_name", "resource", "start_timestamp", "end_timestamp"]
    ]
    sim = test_df.copy()
    sim["agent"] = sim["resource"]
    sim["TimeStep"] = range(len(sim))
    return test_df, sim


def test_mode_name():
    assert (
        adapter.mode_name(central_orchestration=False, determine_automatically=False)
        == "autonomous"
    )
    assert (
        adapter.mode_name(central_orchestration=True, determine_automatically=False)
        == "orchestrated"
    )
    assert (
        adapter.mode_name(central_orchestration=True, determine_automatically=True)
        == "main_results"
    )


def test_build_input_csv_factorizes_and_writes(tmp_path):
    df = _canonical_log()
    out = tmp_path / "input.csv"
    stats = adapter.build_input_csv(df, out)

    assert out.exists()
    written = pd.read_csv(out)
    assert list(written.columns) == list(adapter.INPUT_COLUMNS)
    # case_id is factorised to a dense 1..N integer range (any id format works).
    assert written["case_id"].min() == 1
    assert written["case_id"].max() == stats["cases"]
    assert stats["events"] == len(df)
    assert stats["cases"] == 25


def test_build_input_csv_missing_columns_raises(tmp_path):
    df = pd.DataFrame(
        {"case_id": [1, 1], "activity": ["a", "b"], "timestamp": ["2021-01-01", "2021-01-02"]}
    )
    with pytest.raises(ValueError, match="missing columns"):
        adapter.build_input_csv(df, tmp_path / "x.csv")


def test_build_input_csv_missing_start_dropped_missing_end_filled(tmp_path):
    df = _canonical_log(n_cases=3)
    df.loc[0, "timestamp"] = None  # no start → row dropped
    df.loc[1, "end_timestamp"] = None  # no end → filled from start, kept
    out = tmp_path / "input.csv"
    stats = adapter.build_input_csv(df, out)
    assert stats["events"] == len(df) - 1


def test_build_input_csv_without_end_column(tmp_path):
    # Most real logs (Helpdesk, BPIC, SEPSIS) have a single timestamp.
    df = _canonical_log(n_cases=5).drop(columns=["end_timestamp"])
    out = tmp_path / "input.csv"
    stats = adapter.build_input_csv(df, out)
    assert stats["events"] == len(df)
    written = pd.read_csv(out)
    assert (written["start_time"] == written["end_time"]).all()  # end fell back to start


def test_compute_summaries_shapes():
    test_df, sim = _agentsim_shapes(_canonical_log())
    summ = adapter.compute_summaries(test_df, [sim, sim])

    for key in (
        "cycle_time",
        "arrivals",
        "circadian",
        "activities",
        "handover",
        "preview",
        "simulation",
        "test",
    ):
        assert key in summ

    assert len(summ["circadian"]) == 24
    assert summ["simulation"]["num_logs"] == 2

    # Cycle-time bins carry both series.
    bins = summ["cycle_time"]["bins"]
    assert bins and {"label", "real", "sim"} <= set(bins[0])

    # Handover matrices are square and aligned to the resource list.
    k = len(summ["handover"]["resources"])
    assert k > 0
    assert all(len(row) == k for row in summ["handover"]["real"])
    assert all(len(row) == k for row in summ["handover"]["sim"])

    # Preview has the simulated-log columns.
    assert summ["preview"]["columns"] == ["case_id", "activity", "resource", "start", "end"]


def test_compute_summaries_drops_end_marker():
    test_df, sim = _agentsim_shapes(_canonical_log(n_cases=5))
    # AgentSimulator leaves a synthetic 'zzz_end' activity in simulated logs.
    extra = sim.iloc[:5].copy()
    extra["activity_name"] = "zzz_end"
    sim_with_marker = pd.concat([sim, extra], ignore_index=True)
    summ = adapter.compute_summaries(test_df, [sim_with_marker])
    activities = {a["activity"] for a in summ["activities"]}
    assert "zzz_end" not in activities


def test_align_for_metrics_prefers_agent_as_resource():
    _, sim = _agentsim_shapes(_canonical_log(n_cases=4))
    sim["agent"] = "agent_X"
    aligned = metrics.align_for_metrics(sim)
    for col in ("case_id", "activity", "start_time", "end_time", "resource"):
        assert col in aligned.columns
    # The notebook treats the simulated log's agent column as the resource.
    assert (aligned["resource"] == "agent_X").all()
    assert pd.api.types.is_datetime64_any_dtype(aligned["start_time"])


def test_to_download_csv_normalises():
    _, sim = _agentsim_shapes(_canonical_log(n_cases=3))
    csv = adapter.to_download_csv(sim)
    head = csv.splitlines()[0]
    assert head.split(",") == ["case_id", "activity", "resource", "start", "end"]


def test_compute_fidelity_identical_logs_are_zero():
    test_df, sim = _agentsim_shapes(_canonical_log())
    fid = metrics.compute_fidelity(test_df, [sim])
    assert set(fid) == {"NGD", "AEDD", "CEDD", "REDD", "CTDD"}
    for key, cell in fid.items():
        assert cell["mean"] == 0.0, key  # a log vs itself ⇒ zero distance
        assert cell["lower_better"] is True


def test_compute_fidelity_detects_difference():
    test_df, sim = _agentsim_shapes(_canonical_log())
    shifted = sim.copy()
    shifted["start_timestamp"] = pd.to_datetime(
        shifted["start_timestamp"], utc=True
    ) + pd.Timedelta(hours=12)
    shifted["end_timestamp"] = pd.to_datetime(shifted["end_timestamp"], utc=True) + pd.Timedelta(
        hours=12
    )
    shifted = shifted[shifted["activity_name"] != "decide"]  # change the control flow too
    fid = metrics.compute_fidelity(test_df, [shifted])
    assert fid["CEDD"]["mean"] > 0  # time-of-day shifted
    assert fid["NGD"]["mean"] > 0  # an activity removed


# ── regression: real simulated-log schema with degenerate timestamps ───────


def _real_sim_shapes(n_cases: int = 25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The *true* shapes AgentSimulator writes (verified against on-disk output):

    - ``test_preprocessed.csv``: case_id, resource, activity_name,
      start_timestamp, end_timestamp, agent
    - ``simulated_log_i.csv``: case_id, agent, activity_name, start_timestamp,
      end_timestamp, TimeStep, resource

    with **degenerate timestamps** - single-timestamp / zero-duration events, as
    real source logs carry - round-tripped through CSV so the timestamp columns
    arrive as tz-aware ISO strings (not pd.Timestamp), exactly like ``read_csv``
    of the simulator's output. The clean ``_agentsim_shapes`` fixture (distinct
    start/end, native datetimes) doesn't exercise this path.
    """
    base = pd.Timestamp("2023-04-20 08:00:00+00:00")
    acts = ["Receive", "Review", "Decide", "Notify"]
    rows: list[dict[str, object]] = []
    for c in range(n_cases):
        start = base + pd.Timedelta(days=c % 14, hours=c % 6)
        for i, a in enumerate(acts):
            s = start + pd.Timedelta(hours=2 * i)
            rows.append(
                {
                    "case_id": c + 1,
                    "resource": f"Clerk-{(c + i) % 3:06d}",
                    "activity_name": a,
                    "start_timestamp": s,
                    "end_timestamp": s,  # zero-duration: end == start
                    "agent": (c + i) % 3,
                }
            )
    raw = pd.DataFrame(rows)
    # Round-trip through CSV → timestamp columns become tz-aware strings.
    test = pd.read_csv(io.StringIO(raw.to_csv(index=False)))
    sim = test.copy()
    sim["TimeStep"] = range(len(sim))
    sim = sim[["case_id", "agent", "activity_name", "start_timestamp", "end_timestamp", "TimeStep"]]
    sim["resource"] = test["resource"]
    return test, sim


def test_compute_summaries_real_sim_shapes_all_five_populate():
    """All five distributions must populate on the real simulated-log schema with
    degenerate (zero-duration) timestamps - the bug was an old cache with only
    `handover` set, leaving the other four tabs blank."""
    test_df, sim = _real_sim_shapes()
    summ = adapter.compute_summaries(test_df, [sim, sim])

    bins = summ["cycle_time"]["bins"]
    assert bins, "cycle_time.bins empty"
    assert any(b["sim"] > 0 for b in bins), "no simulated cycle-time mass"

    series = summ["arrivals"]["series"]
    assert series, "arrivals.series empty"
    assert any(s["sim"] > 0 for s in series), "no simulated arrivals"

    assert len(summ["circadian"]) == 24
    assert any(c["sim"] > 0 for c in summ["circadian"]), "no simulated circadian mass"

    activities = summ["activities"]
    assert activities, "activities empty"
    assert any(a["sim"] > 0 for a in activities), "no simulated activity counts"

    assert summ["handover"]["resources"], "handover.resources empty"


def test_normalize_keeps_rows_with_unparseable_end():
    """A NaT/unparseable end must NOT drop the row (it falls back to start);
    only a missing start drops it. Previously a bad end silently emptied whole
    distributions. `_normalize` is internal but worth testing in isolation here.
    """
    normalize = getattr(adapter, "_normalize")  # noqa: B009  (avoid private-access lint)
    _, sim = _real_sim_shapes(n_cases=4)
    sim = sim.copy()
    sim["end_timestamp"] = "not-a-date"  # every end unparseable
    d = normalize(sim)
    assert len(d) == len(sim), "rows with unparseable end were wrongly dropped"
    assert (d["end"] == d["start"]).all(), "end did not fall back to start"

    # A missing *start* still drops the row.
    sim2 = sim.copy()
    sim2.loc[0, "start_timestamp"] = None
    d2 = normalize(sim2)
    assert len(d2) == len(sim2) - 1


# ── regression: the /results stale-cache guard (_result_is_current) ────────


def _good_result() -> dict[str, object]:
    from modules.agentsimulator.module import RESULT_SCHEMA

    return {
        "status": "ready",
        "schema": RESULT_SCHEMA,
        "cycle_time": {},
        "arrivals": {},
        "circadian": [],
        "activities": [],
        "handover": {},
    }


def test_result_is_current_accepts_complete_current_result():
    assert _result_is_current(_good_result()) is True


def test_result_is_current_rejects_missing_schema():
    r = _good_result()
    del r["schema"]
    assert _result_is_current(r) is False


def test_result_is_current_rejects_old_schema():
    r = _good_result()
    r["schema"] = 1  # an older-schema cache
    assert _result_is_current(r) is False


def test_result_is_current_rejects_partial_distribution_keys():
    # The reported symptom: a cache with only `handover` present.
    r = _good_result()
    for k in ("cycle_time", "arrivals", "circadian", "activities"):
        del r[k]
    assert _result_is_current(r) is False


def test_result_is_current_rejects_none_and_non_dict():
    assert _result_is_current(None) is False
    assert _result_is_current("nope") is False
    assert _result_is_current(["list"]) is False
