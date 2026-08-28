"""Case- and variant-level aggregation shared between the import path and
the in-place edit path.

`compute_cases` is the single source of truth for `cases.parquet`: it is
called once at import time (`dispatch.py`) and again whenever the user
edits an event so the cached aggregates stay coherent with the parquet.
"""

from __future__ import annotations

import hashlib

import pandas as pd


def variant_id_for(activities: tuple[str, ...]) -> str:
    """Stable, short hash of an activity sequence - same scheme as v1 import.

    Kept as a module-level helper so other code (e.g. variant lookups) can
    derive the id from a sequence without re-loading cases.parquet.
    """
    return variant_id_for_str("→".join(activities))


def variant_id_for_str(activities_str: str) -> str:
    """`variant_id_for` over an already-joined ``a→b→c`` sequence string.

    Skips the split/re-join round-trip - identical output, since joining a
    split string with the same separator reproduces the input. Used on the
    variants hot path where DuckDB hands back the joined string per variant.
    """
    return hashlib.blake2b(activities_str.encode("utf-8"), digest_size=8).hexdigest()


def compute_cases(df: pd.DataFrame) -> pd.DataFrame:
    """Roll the events frame up to a per-case row.

    Expects a frame already sorted by (case_id, timestamp) - `dispatch.py`
    sorts before calling, and the editor re-sorts after every write.
    """
    grouped = df.groupby("case_id", sort=False)
    starts = grouped["timestamp"].min()
    ends = grouped["timestamp"].max()
    counts = grouped.size().rename("event_count")
    activity_seq = grouped["activity"].apply(lambda s: tuple(s.tolist()))

    durations = (ends - starts).dt.total_seconds()
    variant_id = activity_seq.apply(variant_id_for)
    first_activity = activity_seq.apply(lambda seq: seq[0] if seq else None)
    last_activity = activity_seq.apply(lambda seq: seq[-1] if seq else None)

    return pd.DataFrame(
        {
            "case_id": starts.index,
            "case_start": starts.values,
            "case_end": ends.values,
            "case_duration_seconds": durations.values,
            "event_count": counts.values,
            "variant_id": variant_id.values,
            "first_activity": first_activity.values,
            "last_activity": last_activity.values,
        }
    )


__all__ = ["compute_cases", "variant_id_for", "variant_id_for_str"]
