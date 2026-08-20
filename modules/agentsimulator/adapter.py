"""Glue between the Mate event log and the upstream AgentSimulator pipeline.

We import and run ``AgentSimulator`` **in-process** (in a worker thread) rather
than shelling out to ``simulate.py``: spawning that grandchild from inside the
platform's asyncio subprocess worker proved fatally fragile (see ``run_simulate``).
The simulator's output base dir is passed via ``params['output_dir']`` (a small
patch to the vendored ``source/agent_simulator.py``) so it writes under our
per-run scratch dir without an ``os.chdir``.

The input-building and comparison helpers are pure ``pandas`` so they can be
unit-tested without the heavy simulator venv.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd

# Input-CSV columns we write (we own these names → map them to the simulator's
# standard names via params['column_names'] in run_simulate).
INPUT_COLUMNS = ("case_id", "activity", "resource", "start_time", "end_time")

# Canonical Mate event columns we require (see apps/api/.../event_log_access.py).
# `end_timestamp` is optional - handled separately in build_input_csv.
_CANON_REQUIRED = ("case_id", "activity", "timestamp", "resource")

ProgressCb = Callable[[int, int, str], Awaitable[None]]


# ───────────────────────── input preparation ──────────────────────────────


def build_input_csv(df: pd.DataFrame, out_path: Path) -> dict[str, int]:
    """Write the Mate event log as an AgentSimulator-ready CSV.

    Maps canonical Mate columns → simulator columns, coerces timestamps, drops
    rows missing essentials, and **factorises ``case_id`` to dense integers** so
    any id format works (the simulator otherwise digit-extracts string ids).
    Returns ``{events, cases}`` actually written.
    """
    missing = [c for c in _CANON_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"Event log is missing columns AgentSimulator needs: {missing}. "
            "It requires case_id, activity, a start (timestamp), and resource."
        )

    start = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    # end_timestamp is optional: most real logs carry a single timestamp. When
    # it's absent (or null for a row) we fall back to the start - a zero-duration
    # activity. AgentSimulator still models arrivals, control flow and handovers;
    # only per-activity durations degrade.
    if "end_timestamp" in df.columns:
        end = pd.to_datetime(df["end_timestamp"], utc=True, errors="coerce").fillna(start)
    else:
        end = start

    out = pd.DataFrame(
        {
            "case_id": df["case_id"],
            "activity": df["activity"].astype(str),
            "resource": df["resource"],
            "start_time": start,
            "end_time": end,
        }
    )
    out = out.dropna(subset=["case_id", "activity", "start_time", "end_time"])
    out["resource"] = out["resource"].fillna("undefined").astype(str)

    codes, _ = pd.factorize(out["case_id"])
    out["case_id"] = codes + 1

    out = out.sort_values(["case_id", "start_time", "end_time"]).reset_index(drop=True)
    if out.empty:
        raise ValueError("No usable events after cleaning (need start + end timestamps).")

    out["start_time"] = out["start_time"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    out["end_time"] = out["end_time"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    out.to_csv(out_path, index=False)
    return {"events": len(out), "cases": int(out["case_id"].nunique())}


def mode_name(*, central_orchestration: bool, determine_automatically: bool) -> str:
    if determine_automatically:
        return "main_results"
    return "orchestrated" if central_orchestration else "autonomous"


# ───────────────────────── running the simulator ──────────────────────────


async def run_simulate(
    *,
    module_dir: Path,
    input_csv: Path,
    run_dir: Path,
    num_simulations: int,
    central_orchestration: bool,
    extr_delays: bool,
    determine_automatically: bool,
    progress_cb: ProgressCb | None = None,
) -> Path:
    """Run AgentSimulator **in-process** (in a worker thread); return the output dir.

    Earlier versions shelled out to ``simulate.py``. Spawning that grandchild
    from inside the platform's asyncio subprocess worker proved fatally fragile
    (the worker was SIGKILLed ~30s in, mid-run), so we import the pipeline and run
    it directly - no fork, no pipe, no os.system. ``output_dir`` is passed through
    ``params`` so the simulator writes under our scratch dir without an os.chdir.
    """
    module_dir = module_dir.resolve()
    input_csv = input_csv.resolve()
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    # params mirror what simulate.py builds from its CLI args. column_names maps
    # our input-CSV columns → the simulator's standard names.
    params: dict[str, Any] = {
        "PATH_LOG": str(input_csv),
        "PATH_LOG_test": None,
        "train_and_test": False,
        "column_names": {
            "case_id": "case_id",
            "activity": "activity_name",
            "resource": "resource",
            "start_time": "start_timestamp",
            "end_time": "end_timestamp",
        },
        "discover_extr_delays": extr_delays,
        "discover_parallel_work": False,
        "central_orchestration": central_orchestration,
        "determine_automatically": determine_automatically,
        "num_simulations": num_simulations,
        "output_dir": str(run_dir),
    }

    # No per-log progress hook inside execute_pipeline; report coarse stages.
    if progress_cb is not None:
        await progress_cb(0, num_simulations, "Discovering + simulating")
    log_path = run_dir / "_simulate.log"
    await asyncio.to_thread(_run_inprocess, module_dir, params, log_path)
    if progress_cb is not None:
        await progress_cb(num_simulations, num_simulations, "Simulated")

    out_dir = (
        run_dir
        / "simulated_data"
        / input_csv.stem
        / mode_name(
            central_orchestration=central_orchestration,
            determine_automatically=determine_automatically,
        )
    )
    if not out_dir.exists() or not (out_dir / "simulated_log_0.csv").exists():
        tail = ""
        with contextlib.suppress(Exception):
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-60:])
        raise RuntimeError(
            f"AgentSimulator produced no output at {out_dir}.\n--- last output ---\n{tail}"
        )
    return out_dir


def _run_inprocess(module_dir: Path, params: dict[str, Any], log_path: Path) -> None:
    """Import and run the AgentSimulator pipeline in this thread, redirecting its
    chatty stdout/stderr to ``log_path`` (also used for error reporting)."""
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    from source.agent_simulator import AgentSimulator

    with (
        open(log_path, "w") as logf,
        contextlib.redirect_stdout(logf),
        contextlib.redirect_stderr(logf),
    ):
        AgentSimulator(params).execute_pipeline()


def load_outputs(out_dir: Path, num_simulations: int) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    """Read ``test_preprocessed.csv`` and every ``simulated_log_i.csv``."""
    test = pd.read_csv(out_dir / "test_preprocessed.csv")
    sims: list[pd.DataFrame] = []
    for i in range(num_simulations):
        p = out_dir / f"simulated_log_{i}.csv"
        if p.exists():
            sims.append(pd.read_csv(p))
    if not sims:
        raise RuntimeError("No simulated logs were produced.")
    return test, sims


# ───────────────────────── comparison summaries ───────────────────────────


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Common frame: case_id, activity, resource, start, end (UTC), zzz_end dropped."""
    activity = df["activity_name"] if "activity_name" in df.columns else df["activity"]
    if "resource" in df.columns:
        resource = df["resource"]
    elif "agent" in df.columns:
        resource = df["agent"]
    else:
        resource = "undefined"
    sct = "start_timestamp" if "start_timestamp" in df.columns else "start_time"
    ect = "end_timestamp" if "end_timestamp" in df.columns else "end_time"
    start = pd.to_datetime(df[sct], utc=True, format="mixed", errors="coerce")
    # end falls back to start when missing/unparseable (mirrors build_input_csv):
    # real logs are often single-timestamp, and a NaT end must not delete the row
    # - that would silently empty whole distributions. We only drop on a missing
    # *start* (a row we genuinely can't place in time).
    end = pd.to_datetime(df[ect], utc=True, format="mixed", errors="coerce").fillna(start)
    d = pd.DataFrame(
        {
            "case_id": df["case_id"].astype(str),
            "activity": activity.astype(str),
            "resource": pd.Series(resource, index=df.index).astype(str),
            "start": start,
            "end": end,
        }
    )
    d = d.dropna(subset=["start"])
    d = d[d["activity"] != "zzz_end"]
    return d.reset_index(drop=True)


def _case_durations_h(d: pd.DataFrame) -> pd.Series:
    g = d.groupby("case_id")
    return (g["end"].max() - g["start"].min()).dt.total_seconds() / 3600.0


def _stats_h(dur: pd.Series) -> dict[str, float]:
    if dur.empty:
        return {"mean_h": 0.0, "median_h": 0.0, "p90_h": 0.0}
    return {
        "mean_h": round(float(dur.mean()), 2),
        "median_h": round(float(dur.median()), 2),
        "p90_h": round(float(dur.quantile(0.9)), 2),
    }


def _fmt_hours(h: float) -> str:
    if h < 1:
        return f"{round(h * 60)}m"
    if h < 48:
        return f"{round(h, 1)}h"
    return f"{round(h / 24, 1)}d"


def _hist(values: pd.Series, edges: list[float]) -> list[int]:
    if values.empty:
        return [0] * (len(edges) - 1)
    cut = pd.cut(values, bins=edges, include_lowest=True, right=False)
    counts = cut.value_counts(sort=False)
    return [int(c) for c in counts.tolist()]


def _cycle_time(test: pd.DataFrame, sims: list[pd.DataFrame]) -> dict[str, Any]:
    dt = _case_durations_h(test)
    sim_durs = [_case_durations_h(s) for s in sims]
    pooled = pd.concat([dt, *sim_durs]) if sim_durs else dt
    hi = float(pooled.quantile(0.97)) if not pooled.empty else 1.0
    hi = max(hi, 0.5)
    n_bins = 20
    step = hi / n_bins
    edges = [round(i * step, 4) for i in range(n_bins)] + [float(pooled.max()) + step]

    real_counts = _hist(dt, edges)
    n = len(sims) or 1
    sim_acc = [0.0] * (len(edges) - 1)
    for s in sim_durs:
        for i, c in enumerate(_hist(s, edges)):
            sim_acc[i] += c
    sim_counts = [round(c / n, 2) for c in sim_acc]

    bins = []
    for i in range(len(edges) - 1):
        bins.append(
            {
                "label": f"{_fmt_hours(edges[i])}-{_fmt_hours(edges[i + 1])}",
                "real": real_counts[i],
                "sim": sim_counts[i],
            }
        )
    return {
        "unit": "hours",
        "bins": bins,
        "real_stats": _stats_h(dt),
        "sim_stats": _stats_h(pd.concat(sim_durs)) if sim_durs else _stats_h(dt),
    }


def _arrivals(test: pd.DataFrame, sims: list[pd.DataFrame]) -> dict[str, Any]:
    """Cases started per elapsed bucket, each log measured from its own first
    case so absolute-date offsets don't make a faithful run look bad."""

    def rel_days(d: pd.DataFrame) -> pd.Series:
        starts = d.groupby("case_id")["start"].min()
        if starts.empty:
            return pd.Series(dtype="int64")
        ref = starts.min()
        return ((starts - ref).dt.total_seconds() // 86400).astype("int64")

    test_days = rel_days(test)
    sim_days = [rel_days(s) for s in sims]
    max_day = max(
        [int(test_days.max()) if not test_days.empty else 0]
        + [int(s.max()) if not s.empty else 0 for s in sim_days]
    )
    width = 7 if max_day > 90 else 1  # weeks for long spans, else days
    unit = "week" if width == 7 else "day"

    def counts(days: pd.Series) -> dict[int, int]:
        if days.empty:
            return {}
        return (days // width).value_counts().to_dict()

    test_c = counts(test_days)
    n = len(sims) or 1
    sim_acc: dict[int, float] = {}
    for s in sim_days:
        for k, v in counts(s).items():
            sim_acc[k] = sim_acc.get(k, 0.0) + v

    n_buckets = (max_day // width) + 1
    series = [
        {
            "t": int(b),
            "real": int(test_c.get(b, 0)),
            "sim": round(sim_acc.get(b, 0.0) / n, 2),
        }
        for b in range(n_buckets)
    ]
    return {"unit": unit, "series": series}


def _circadian(test: pd.DataFrame, sims: list[pd.DataFrame]) -> list[dict[str, Any]]:
    def by_hour(d: pd.DataFrame) -> dict[int, int]:
        if d.empty:
            return {}
        return d["start"].dt.hour.value_counts().to_dict()

    test_h = by_hour(test)
    n = len(sims) or 1
    sim_acc: dict[int, float] = {}
    for s in sims:
        for k, v in by_hour(s).items():
            sim_acc[k] = sim_acc.get(k, 0.0) + v
    return [
        {"hour": h, "real": int(test_h.get(h, 0)), "sim": round(sim_acc.get(h, 0.0) / n, 2)}
        for h in range(24)
    ]


def _activities(
    test: pd.DataFrame, sims: list[pd.DataFrame], top_k: int = 12
) -> list[dict[str, Any]]:
    test_counts = test["activity"].value_counts()
    top = list(test_counts.head(top_k).index)
    n = len(sims) or 1
    sim_acc: dict[str, float] = {a: 0.0 for a in top}
    for s in sims:
        sc = s["activity"].value_counts()
        for a in top:
            sim_acc[a] += float(sc.get(a, 0))
    return [
        {"activity": a, "real": int(test_counts.get(a, 0)), "sim": round(sim_acc[a] / n, 1)}
        for a in top
    ]


def _handover(test: pd.DataFrame, sims: list[pd.DataFrame], top_k: int = 8) -> dict[str, Any]:
    """Resource→resource handover probability matrices (top-K resources)."""
    resources = list(test["resource"].value_counts().head(top_k).index)
    idx = {r: i for i, r in enumerate(resources)}
    k = len(resources)

    def counts(d: pd.DataFrame, acc: list[list[float]]) -> None:
        d = d.sort_values(["case_id", "start"])
        for _, grp in d.groupby("case_id"):
            res = grp["resource"].tolist()
            for a, b in pairwise(res):
                if a in idx and b in idx:
                    acc[idx[a]][idx[b]] += 1.0

    def normalise(acc: list[list[float]]) -> list[list[float]]:
        out = []
        for row in acc:
            tot = sum(row)
            out.append([round(v / tot, 3) if tot else 0.0 for v in row])
        return out

    real_acc = [[0.0] * k for _ in range(k)]
    counts(test, real_acc)
    sim_acc = [[0.0] * k for _ in range(k)]
    for s in sims:
        counts(s, sim_acc)

    return {
        "resources": resources,
        "real": normalise(real_acc),
        "sim": normalise(sim_acc),
    }


def preview_rows(sim_df: pd.DataFrame, limit: int = 50) -> dict[str, Any]:
    d = _normalize(sim_df)
    cols = ["case_id", "activity", "resource", "start", "end"]
    head = d.head(limit).copy()
    head["start"] = head["start"].dt.strftime("%Y-%m-%d %H:%M")
    head["end"] = head["end"].dt.strftime("%Y-%m-%d %H:%M")
    return {
        "columns": cols,
        "rows": head[cols].astype(str).values.tolist(),
        "total": len(d),
    }


def to_download_csv(sim_df: pd.DataFrame, max_rows: int = 20000) -> str:
    """A trimmed, normalised CSV of one simulated log for the download button."""
    d = _normalize(sim_df).head(max_rows).copy()
    d["start"] = d["start"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    d["end"] = d["end"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    return d.to_csv(index=False)


def compute_summaries(test_df: pd.DataFrame, sim_dfs: list[pd.DataFrame]) -> dict[str, Any]:
    """All the real-vs-simulated distributions the panel renders."""
    test = _normalize(test_df)
    sims = [_normalize(s) for s in sim_dfs]
    return {
        "simulation": {
            "num_logs": len(sims),
            "avg_cases": round(sum(s["case_id"].nunique() for s in sims) / (len(sims) or 1), 1),
            "avg_events": round(sum(len(s) for s in sims) / (len(sims) or 1), 1),
        },
        "test": {
            "cases": int(test["case_id"].nunique()),
            "events": len(test),
            "activities": int(test["activity"].nunique()),
            "resources": int(test["resource"].nunique()),
        },
        "cycle_time": _cycle_time(test, sims),
        "arrivals": _arrivals(test, sims),
        "circadian": _circadian(test, sims),
        "activities": _activities(test, sims),
        "handover": _handover(test, sims),
        "preview": preview_rows(sim_dfs[0]) if sim_dfs else {"columns": [], "rows": [], "total": 0},
    }
