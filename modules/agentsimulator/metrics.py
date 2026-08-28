"""Real-vs-simulated fidelity measures (NGD / AEDD / CEDD / REDD / CTDD).

Originally these came from the AgentSimulator paper's ``log_distance_measures``
package. That package hard-pins ``jellyfish==0.11.2``, which has no cp312 wheel
and won't build on the worker - and the platform SDK requires Python >=3.12, so
an older interpreter (where a jellyfish wheel exists) isn't an option. We
therefore compute equivalent distances directly with numpy + scipy:

* **AEDD / CEDD / REDD / CTDD** - the Earth-Mover (Wasserstein) distance between
  the relevant timestamp distributions, which is exactly how the paper defines
  them. Units are hours.
* **NGD** - the total-variation distance between the logs' activity n-gram
  (n=3) distributions, in [0, 1].

Values are faithful in spirit to the reference but not guaranteed identical to
it. All are distances: lower = closer to the real log.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

METRIC_LABELS: dict[str, str] = {
    "NGD": "N-Gram Distribution (TV)",
    "AEDD": "Absolute Event Distribution (h)",
    "CEDD": "Circadian Event Distribution (h)",
    "REDD": "Relative Event Distribution (h)",
    "CTDD": "Cycle Time Distribution (h)",
}

METRIC_ORDER: list[str] = ["NGD", "AEDD", "CEDD", "REDD", "CTDD"]

_BOS, _EOS = "<s>", "</s>"


def align_for_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Rename log columns to a common convention
    (``case_id`` / ``activity`` / ``start_time`` / ``end_time`` / ``resource``)
    and coerce timestamps to UTC. Mirrors the upstream notebook's
    ``align_column_names`` - notably it prefers the simulated log's ``agent``
    column as the resource.
    """
    df = df.copy()

    if "case:concept:name" in df.columns:
        df = df.rename(columns={"case:concept:name": "case_id"})
    elif "caseid" in df.columns:
        df = df.rename(columns={"caseid": "case_id"})

    if "activity_name" in df.columns:
        df = df.rename(columns={"activity_name": "activity"})
    elif "concept:name" in df.columns:
        df = df.rename(columns={"concept:name": "activity"})
    elif "task" in df.columns:
        df = df.rename(columns={"task": "activity"})

    if "agent" in df.columns:
        if "resource" in df.columns:
            df = df.drop(columns=["resource"])
        df = df.rename(columns={"agent": "resource"})
    elif "org:resource" in df.columns:
        df = df.rename(columns={"org:resource": "resource"})
    elif "user" in df.columns:
        df = df.rename(columns={"user": "resource"})

    if "start_timestamp" in df.columns:
        df = df.rename(columns={"start_timestamp": "start_time"})
    if "end_timestamp" in df.columns:
        df = df.rename(columns={"end_timestamp": "end_time"})
    if "time:timestamp" in df.columns and "end_time" not in df.columns:
        df = df.rename(columns={"time:timestamp": "end_time"})

    # Coerce defensively (`errors="coerce"`): an unparsable cell must degrade its
    # own row, never raise. `compute_fidelity` calls this *outside* its per-measure
    # guard, so a single bad timestamp would otherwise crash the whole scoring pass
    # and wipe all five measures at once.
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True, format="mixed", errors="coerce")
    # `end` falls back to `start` when the column is absent, or a value is
    # missing/unparsable. Single-timestamp logs - the *simulated output* included -
    # carry no usable end, and the gap-fill in build_input_csv only rewrites the
    # real log fed to the simulator. Mirroring adapter._normalize here means the
    # simulated durations are handled exactly like the real log's; without it a
    # NaT end NaNs out every case duration and silently drops CTDD/AEDD/REDD,
    # collapsing the time-based fidelity scores to nothing.
    if "end_time" in df.columns:
        df["end_time"] = pd.to_datetime(
            df["end_time"], utc=True, format="mixed", errors="coerce"
        ).fillna(df["start_time"])
    else:
        df["end_time"] = df["start_time"]
    if "resource" not in df.columns:
        df["resource"] = "undefined"
    df = df[df["activity"].astype(str) != "zzz_end"]
    # Drop only rows we genuinely can't place in time (missing start); a missing
    # end already fell back to start above. Mirrors adapter._normalize.
    df = df.dropna(subset=["start_time"])
    return df.reset_index(drop=True)


# ───────────────────────── individual measures ────────────────────────────


def _ngrams(seq: list[str], n: int) -> list[tuple[str, ...]]:
    padded = [_BOS] * (n - 1) + list(seq) + [_EOS] * (n - 1)
    return [tuple(padded[i : i + n]) for i in range(len(padded) - n + 1)]


def _ngram_distribution(d: pd.DataFrame, n: int) -> dict[tuple[str, ...], float]:
    counts: Counter[tuple[str, ...]] = Counter()
    for _, grp in d.sort_values(["case_id", "start_time"]).groupby("case_id"):
        counts.update(_ngrams(grp["activity"].astype(str).tolist(), n))
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}


def _ngram_tv(real: pd.DataFrame, sim: pd.DataFrame, n: int) -> float:
    pr = _ngram_distribution(real, n)
    ps = _ngram_distribution(sim, n)
    keys = set(pr) | set(ps)
    return 0.5 * sum(abs(pr.get(k, 0.0) - ps.get(k, 0.0)) for k in keys)


def _abs_hours(d: pd.DataFrame, ref: pd.Timestamp) -> np.ndarray:
    return (d["start_time"] - ref).dt.total_seconds().to_numpy() / 3600.0


def _hour_of_day(d: pd.DataFrame) -> np.ndarray:
    return d["start_time"].dt.hour.to_numpy().astype(float)


def _rel_hours(d: pd.DataFrame) -> np.ndarray:
    case_start = d.groupby("case_id")["start_time"].transform("min")
    return (d["start_time"] - case_start).dt.total_seconds().to_numpy() / 3600.0


def _case_durations_h(d: pd.DataFrame) -> np.ndarray:
    g = d.groupby("case_id")
    return ((g["end_time"].max() - g["start_time"].min()).dt.total_seconds() / 3600.0).to_numpy()


def _summarise(values: list[float], key: str) -> dict[str, Any]:
    if not values:
        return {
            "mean": None,
            "std": None,
            "values": [],
            "label": METRIC_LABELS[key],
            "lower_better": True,
        }
    n = len(values)
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    return {
        "mean": round(mean, 3),
        "std": round(std, 3),
        "values": [round(v, 3) for v in values],
        "label": METRIC_LABELS[key],
        "lower_better": True,
    }


def compute_fidelity(
    test_df: pd.DataFrame,
    sim_dfs: list[pd.DataFrame],
    *,
    ngram: int = 3,
) -> dict[str, dict[str, Any]]:
    """Compute the five distances of every simulated log against the test log,
    returning mean ± std (and the raw per-run values) per measure."""
    from scipy.stats import wasserstein_distance

    test = align_for_metrics(test_df)
    raw: dict[str, list[float]] = {k: [] for k in METRIC_ORDER}

    for sim_df in sim_dfs:
        sim = align_for_metrics(sim_df)
        if test.empty or sim.empty:
            continue
        ref = min(test["start_time"].min(), sim["start_time"].min())

        # Each measure is independent: a single degenerate run can't sink the set.
        with contextlib.suppress(Exception):
            raw["NGD"].append(float(_ngram_tv(test, sim, ngram)))
        with contextlib.suppress(Exception):
            raw["AEDD"].append(
                float(wasserstein_distance(_abs_hours(test, ref), _abs_hours(sim, ref)))
            )
        with contextlib.suppress(Exception):
            raw["CEDD"].append(float(wasserstein_distance(_hour_of_day(test), _hour_of_day(sim))))
        with contextlib.suppress(Exception):
            raw["REDD"].append(float(wasserstein_distance(_rel_hours(test), _rel_hours(sim))))
        with contextlib.suppress(Exception):
            raw["CTDD"].append(
                float(wasserstein_distance(_case_durations_h(test), _case_durations_h(sim)))
            )

    return {k: _summarise(raw[k], k) for k in METRIC_ORDER}
