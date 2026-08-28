"""Regression guard: the WINSIM encoding must window traces chronologically.

CV4CDD slices the log into windows of equal *trace count*, so the trace order is
the x-axis of the similarity image. The platform stores ``events.parquet`` sorted
by ``(case_id, timestamp)``, so reading it in row order yields *alphabetical*
trace order - which silently destroys every detection. That regression has
happened before; these tests fail loudly if it comes back.

Only the encoding is covered (no TensorFlow): the model runs on the image, and
if the image is wrong nothing downstream can be right.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from modules.cv4cdd.cv4cdd_core import log_to_windowed_dfg_count, similarity_calculation

N_CASES = 400
N_WINDOWS = 200
DRIFT_AT = 200


def _log(*, tied_starts: bool = False) -> pd.DataFrame:
    """A log with a sudden drift at case ``DRIFT_AT``, in chronological order.

    Case ids are ``c1..c400`` so lexicographic order (``c1, c10, c100, …``) is
    nothing like chronological order - any accidental alphabetical sort shows up
    immediately.
    """
    base = pd.Timestamp("2024-01-01")
    rows: list[dict[str, object]] = []
    for i in range(1, N_CASES + 1):
        acts = ["A", "B", "C", "D"] if i <= DRIFT_AT else ["A", "X", "Y", "D"]
        # tied_starts: every case starts at the same instant (a placeholder date
        # in the source), so only the tie-break decides the trace order.
        start = base if tied_starts else base + pd.Timedelta(hours=i)
        offset = pd.Timedelta(seconds=i) if tied_starts else pd.Timedelta(0)
        for k, activity in enumerate(acts):
            rows.append(
                {
                    "case_id": f"c{i}",
                    "activity": activity,
                    "timestamp": start + pd.Timedelta(minutes=k) + offset,
                }
            )
    return pd.DataFrame(rows)


def _as_stored(df: pd.DataFrame) -> pd.DataFrame:
    """Exactly how the platform persists a log (`ingest/dispatch.py`)."""
    return df.sort_values(["case_id", "timestamp"], kind="mergesort").reset_index(drop=True)


def _block_contrast(sim: np.ndarray) -> float:
    """Mean similarity inside a regime minus mean similarity across the drift.

    High for a correctly ordered log (two clean blocks), near zero once the
    windows are scrambled.
    """
    half = N_WINDOWS // 2
    within = (sim[:half, :half].mean() + sim[half:, half:].mean()) / 2
    return float(within - sim[:half, half:].mean())


def test_stored_row_order_is_alphabetical() -> None:
    """Guards the premise: the stored order really is the dangerous one."""
    stored = _as_stored(_log())
    assert list(pd.unique(stored["case_id"]))[:3] == ["c1", "c10", "c100"]


@pytest.mark.parametrize("stored", [False, True], ids=["chronological", "parquet-order"])
def test_windows_follow_chronological_trace_order(stored: bool) -> None:
    df = _as_stored(_log()) if stored else _log()
    _, window_info = log_to_windowed_dfg_count(df, N_WINDOWS)

    # window_size = 400 // 200 = 2, so window i starts at trace 2*(i-1).
    assert window_info[1][0] == "c1"
    assert window_info[2][0] == "c3"
    assert window_info[N_WINDOWS][0] == f"c{N_CASES - 1}"


def test_encoding_is_independent_of_incoming_row_order() -> None:
    """The module must not care how its caller ordered the frame."""
    chronological = _log()
    reference = log_to_windowed_dfg_count(chronological, N_WINDOWS)

    for variant in (
        _as_stored(chronological),
        chronological.sample(frac=1.0, random_state=7).reset_index(drop=True),
        chronological.iloc[::-1].reset_index(drop=True),
    ):
        dfg, window_info = log_to_windowed_dfg_count(variant, N_WINDOWS)
        assert np.array_equal(dfg, reference[0])
        assert window_info == reference[1]


def test_alphabetical_trace_order_destroys_the_drift_signal() -> None:
    """Shows what the regression costs, so the guard above has teeth."""
    stored = _as_stored(_log())
    correct, _ = log_to_windowed_dfg_count(stored, N_WINDOWS)

    # Reproduce the bug: window by the stored frame's own row order.
    traces = list(pd.unique(stored["case_id"]))
    size = len(traces) // N_WINDOWS
    activities = sorted(stored["activity"].astype(str).unique())
    index = {a: i for i, a in enumerate(activities)}
    from pm4py import discover_dfg_typed

    broken = []
    for i in range(N_WINDOWS):
        left = i * size
        right = (i + 1) * size if i < N_WINDOWS - 1 else len(traces)
        window = stored[stored["case_id"].isin(traces[left:right])].rename(
            columns={
                "case_id": "case:concept:name",
                "activity": "concept:name",
                "timestamp": "time:timestamp",
            }
        )
        graph, _, _ = discover_dfg_typed(window)
        matrix = np.zeros((len(activities), len(activities)), dtype=np.float32)
        for (a, b), freq in graph.items():
            matrix[index[str(a)], index[str(b)]] = float(freq)
        broken.append(matrix)

    assert _block_contrast(similarity_calculation(correct)) > 200
    assert _block_contrast(similarity_calculation(np.array(broken))) < 30


def test_tied_start_timestamps_break_deterministically() -> None:
    """Traces sharing a start timestamp must still land in a stable order."""
    tied = _log(tied_starts=True)
    reference = log_to_windowed_dfg_count(tied, N_WINDOWS)

    for variant in (
        _as_stored(tied),
        tied.sample(frac=1.0, random_state=99).reset_index(drop=True),
    ):
        dfg, window_info = log_to_windowed_dfg_count(variant, N_WINDOWS)
        assert np.array_equal(dfg, reference[0])
        assert window_info == reference[1]


def test_fewer_cases_than_windows_pads_instead_of_crashing() -> None:
    """The reference implementation raises here; the port must degrade."""
    small = _log().query("case_id in @_ids", local_dict={"_ids": [f"c{i}" for i in range(1, 21)]})
    dfg, window_info = log_to_windowed_dfg_count(small, N_WINDOWS)

    assert dfg.shape[0] == N_WINDOWS
    assert window_info[N_WINDOWS] == window_info[20]  # padded from the last real window
    assert not dfg[N_WINDOWS - 1].any()  # trailing windows are empty
