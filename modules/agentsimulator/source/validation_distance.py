"""Cycle-time distribution distance for the automatic-configuration trials.

Replaces ``log_distance_measures.cycle_time_distribution.cycle_time_distribution_distance``.
The ``log-distance-measures`` package was deliberately dropped from the module
venv (it hard-pins ``jellyfish==0.11.2``, which has no cp312 wheel - see
``manifest.yaml``), so importing it made ``determine_automatically`` crash with
``ModuleNotFoundError``. Auto mode only needs the distance to *rank* four
candidate configurations, so we compute the same quantity directly with
numpy + scipy: discretise each case's cycle time (case end - case start) into
``bin_size`` buckets and take the 1-Wasserstein (EMD) distance between the two
bucket distributions - which is how the reference package defines the measure.

Imports only numpy / pandas (+ scipy lazily), so it is unit-testable on the
platform environment without the simulator venv (no mesa / pm4py).
"""

import numpy as np
import pandas as pd

_REQUIRED = ("case_id", "start_timestamp", "end_timestamp")


def _case_cycle_times_seconds(df) -> np.ndarray:
    """Per-case duration in seconds (last event end - first event start).

    Tolerates the shapes the trials produce: ``df_val`` (datetimes, includes
    ``zzz_end`` rows - which never change a case's span) and trial simulated
    logs (``pd.DataFrame(list_of_event_dicts)``). An empty frame or one missing
    the timestamp columns yields an empty array.
    """
    if df is None or len(df) == 0 or any(c not in df.columns for c in _REQUIRED):
        return np.array([])
    start = pd.to_datetime(df["start_timestamp"], utc=True, format="mixed", errors="coerce")
    end = pd.to_datetime(df["end_timestamp"], utc=True, format="mixed", errors="coerce")
    end = end.fillna(start)
    d = pd.DataFrame({"case_id": df["case_id"], "start": start, "end": end})
    d = d.dropna(subset=["start"])
    if d.empty:
        return np.array([])
    g = d.groupby("case_id")
    return (g["end"].max() - g["start"].min()).dt.total_seconds().to_numpy()


def cycle_time_distribution_distance(real_df, sim_df, bin_size=None) -> float:
    """EMD between two logs' binned case cycle-time distributions.

    Units are number-of-bins (hours for the default 1h bin). Returns ``inf``
    when either side has no measurable cases, so a degenerate trial simulation
    can never win the configuration ranking.
    """
    from scipy.stats import wasserstein_distance

    if bin_size is None:
        bin_size = pd.Timedelta(hours=1)
    real = _case_cycle_times_seconds(real_df)
    sim = _case_cycle_times_seconds(sim_df)
    if real.size == 0 or sim.size == 0:
        return float("inf")
    width = max(float(bin_size.total_seconds()), 1.0)
    return float(wasserstein_distance(np.floor(real / width), np.floor(sim / width)))
