"""Shared parquet-safe dtype coercion for ingested frames.

Object-dtype columns can hold a messy mix of strings, Python floats, and NaN -
e.g. an XES attribute like ``RequestedAmount`` that is a number in some events
and a string ("EUR") in others, or an OCEL attribute that is absent for some
objects. PyArrow then fails with "Expected bytes, got a 'float' object".

Strategy per object column: if every non-null value parses as a number, use a
numeric dtype; otherwise coerce everything to a clean string column with real
nulls. Columns named in ``string_only`` are forced to string regardless - they
are contractual identifiers (case_id / ocel:oid / …) and an all-digit value
would otherwise get silently re-typed to int.
"""

from __future__ import annotations

from typing import Any


def coerce_object_columns(df: Any, string_only: set[str]) -> list[str]:
    """Coerce object-dtype columns of ``df`` in place to parquet-safe dtypes.

    Returns the names of columns that contained nulls (the case-centric path
    surfaces these as ``fixed_columns`` on the import event).
    """
    import pandas as pd

    object_cols = list(df.select_dtypes(include="object").columns)
    fixed_columns = [col for col in object_cols if df[col].isna().any()]
    for col in object_cols:
        if col in string_only:
            df[col] = df[col].map(lambda v: None if pd.isna(v) else str(v))
            continue
        non_null = df[col].dropna()
        if len(non_null) > 0:
            coerced = pd.to_numeric(non_null, errors="coerce")
            if coerced.notna().all():
                df[col] = pd.to_numeric(df[col], errors="coerce")
                continue
        df[col] = df[col].map(lambda v: None if pd.isna(v) else str(v))
    return fixed_columns


def normalize_timestamps(df: Any, column: str) -> Any:
    """Coerce ``df[column]`` to a tz-naive UTC datetime column, dropping rows
    that fail to parse. ``utc=True`` collapses mixed offsets to a single dtype;
    the tz is then dropped to keep the parquet / SQLite DateTime column shape.
    """
    import pandas as pd

    df[column] = pd.to_datetime(df[column], errors="coerce", utc=True)
    df = df.dropna(subset=[column])
    df[column] = df[column].dt.tz_localize(None)
    return df


__all__ = ["coerce_object_columns", "normalize_timestamps"]
