"""Event-log DataFrame → the 5-column CSV promg imports.

Output columns are fixed: ``case, activity, timestamp, lifecycle, resource`` — the
generic semantic header (see ``header_gen``) references exactly these. Timestamps are
written as ``y/M/d H:m:s.SSS`` strings with an explicit UTC offset appended by promg's
datetime conversion (same format the reference BPIC17 pipeline used, so the in-graph
conversion path is byte-identical to the validated one).

Rows without a resource are dropped (the analysis is about who did consecutive steps);
the count is surfaced in the run stats. Row order is preserved — promg's ``index``
column, derived from row position, is the tie-breaker for directly-follows ordering
on equal timestamps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

# Column names promg + the semantic header expect in the prepared CSV.
CSV_COLUMNS = ["case", "activity", "timestamp", "lifecycle", "resource"]

# Log columns that may carry a lifecycle transition, in preference order.
_LIFECYCLE_CANDIDATES = ("lifecycle", "lifecycle:transition", "lifecycle_transition")

# The timestamp string format written to the CSV; must stay in sync with
# header_gen.TIMESTAMP_FORMAT (the promg-side parse pattern).
_STRFTIME = "%Y/%m/%d %H:%M:%S.%f"


@dataclass(frozen=True)
class PrepStats:
    events: int
    cases: int
    resources: int
    dropped_null_resource: int
    has_lifecycle: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _format_timestamps(ts: pd.Series) -> pd.Series:
    """Normalize to UTC and render millisecond strings.

    Naive timestamps are assumed UTC. The written string carries no offset; the
    dataset description appends "+00" before parsing (see header_gen), so the
    graph's datetime values are unambiguous.
    """
    ts = pd.to_datetime(ts)
    tz = getattr(ts.dt, "tz", None)
    ts = ts.dt.tz_localize("UTC") if tz is None else ts.dt.tz_convert("UTC")
    # %f is microseconds (6 digits); the parse pattern wants millis - trim to 3.
    return ts.dt.strftime(_STRFTIME).str.slice(0, -3)


def build_input_csv(df: pd.DataFrame, out_path: Path) -> PrepStats:
    """Write the prepared CSV; returns the stats surfaced in the run result."""
    missing = [c for c in ("case_id", "activity", "timestamp", "resource") if c not in df.columns]
    if missing:
        raise ValueError(f"event log is missing required column(s): {', '.join(missing)}")

    lifecycle_col = next((c for c in _LIFECYCLE_CANDIDATES if c in df.columns), None)

    out = pd.DataFrame(
        {
            "case": df["case_id"].astype(str),
            "activity": df["activity"].astype(str),
            "timestamp": _format_timestamps(df["timestamp"]),
            # promg's task variants concatenate activity+'+'+lifecycle; an empty
            # string keeps every query shape valid on logs without transitions.
            "lifecycle": (df[lifecycle_col].fillna("").astype(str) if lifecycle_col else ""),
            # fillna BEFORE astype: pandas-3 string dtype keeps None as <NA>
            # through astype(str), which string-comparisons would miss.
            "resource": df["resource"].fillna("").astype(str),
        }
    )

    total = len(out)
    resource_missing = out["resource"].str.strip().isin(["", "nan", "None", "<NA>"])
    out = out[~resource_missing]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)

    return PrepStats(
        events=len(out),
        cases=int(out["case"].nunique()),
        resources=int(out["resource"].nunique()),
        dropped_null_resource=int(total - len(out)),
        has_lifecycle=lifecycle_col is not None,
    )
