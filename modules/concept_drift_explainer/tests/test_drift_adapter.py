"""Unit tests for the cv4cdd → CDE drift adapter.

Exercises the pure-Python part of [agents/drift_agent.py](../agents/drift_agent.py)
that needs no LLM and no Pinecone - the activity-binning logic that maps a
cv4cdd drift's window indices to representative activity labels.
"""

from __future__ import annotations

import pandas as pd
import pytest

from modules.concept_drift_explainer.agents.drift_agent import (
    _canonical_drift_type,
    _dominant_activity_in_window,
    build_drift_record,
)


def _synthetic_events(n_per_activity: int = 40) -> pd.DataFrame:
    """Build a dataframe whose activity changes once across the timeline."""
    rows = []
    for i in range(n_per_activity):
        rows.append({"case_id": f"c{i}", "activity": "submit", "timestamp": i})
    for i in range(n_per_activity):
        rows.append(
            {
                "case_id": f"c{i + n_per_activity}",
                "activity": "approve",
                "timestamp": i + n_per_activity,
            }
        )
    return pd.DataFrame(rows)


def test_dominant_activity_is_first_half_first():
    df = _synthetic_events()
    # 4 windows of 20 events each - window 0 should be all 'submit', window 3 all 'approve'.
    assert _dominant_activity_in_window(df, n_windows=4, window_idx=0) == "submit"
    assert _dominant_activity_in_window(df, n_windows=4, window_idx=3) == "approve"


def test_dominant_activity_handles_empty_df():
    df = pd.DataFrame(columns=["case_id", "activity", "timestamp"])
    assert _dominant_activity_in_window(df, n_windows=4, window_idx=0) == ""


def test_build_drift_record_maps_windows_to_activities():
    df = _synthetic_events()
    cv4cdd_drift = {
        "type": "sudden",
        "start_window": 0,
        "end_window": 3,
        "start_timestamp": "2024-01-01T00:00:00",
        "end_timestamp": "2024-02-01T00:00:00",
        "confidence": 0.83,
        "bbox": [0, 0, 10, 10],
    }
    record = build_drift_record(cv4cdd_drift=cv4cdd_drift, df=df, n_windows=4)
    assert record["start_activity"] == "submit"
    assert record["end_activity"] == "approve"
    # Original keys are preserved so /drifts can return them verbatim.
    assert record["confidence"] == 0.83
    assert record["type"] == "sudden"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sudden", "SUDDEN_DRIFT"),
        ("Gradual", "GRADUAL_DRIFT"),
        ("incremental", "INCREMENTAL_DRIFT"),
        ("recurring", "RECURRING_DRIFT"),
        ("", "_DRIFT"),
        ("weird-type", "WEIRD-TYPE_DRIFT"),
    ],
)
def test_canonical_drift_type(raw, expected):
    assert _canonical_drift_type(raw) == expected
