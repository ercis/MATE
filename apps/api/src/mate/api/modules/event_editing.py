"""Cell-level edits to events.parquet.

Each PATCH/bulk-fill rewrites events.parquet (atomic via tmp+rename) and
recomputes cases.parquet so derived counts (events_count, cases_count,
variants_count, date_min/max) stay coherent. Concurrent edits to the same
log are serialised behind a per-log asyncio lock; different logs run in
parallel.

The user picked "rewrite parquet on every edit" over an overrides table -
simpler, no stale-state UX, but every edit pays the cost of rewriting
(usually sub-second; large logs may grow up to a few seconds).
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import EventEdit, EventLog
from mate.api.ingest.aggregation import compute_cases
from mate.api.ingest.storage import log_paths
from mate.api.schemas.common import utc_isoformat
from mate.api.schemas.event_log_data import ColumnSpec, EventsHeader
from mate.api.storage import sync as storage_sync

_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()

_RESORT_FIELDS = frozenset({"case_id", "activity", "timestamp"})


async def _lock_for(log_id: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(log_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[log_id] = lock
        return lock


@dataclass
class CellEditOutcome:
    row: dict[str, Any]
    old_row_index: int
    new_row_index: int
    header: EventsHeader
    audit: list[tuple[int, str, Any, Any]]  # (row_index, field, old, new)


@dataclass
class BulkFillOutcome:
    updated: int
    header: EventsHeader
    audit: list[tuple[int, str, Any, Any]]


def coerce_value(field: str, value: Any, specs: list[ColumnSpec]) -> Any:
    """Validate + cast an incoming JSON value to the column's expected type.
    Raises `ValueError` with a user-facing message on bad input.
    """
    if value is None:
        if field in {"case_id", "activity", "timestamp"}:
            raise ValueError(f"{field} cannot be empty.")
        return None

    spec = next((s for s in specs if s.name == field), None)
    if spec is None:
        raise ValueError(f"Unknown column: {field!r}.")

    if spec.type == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} expects a number.") from exc
    if spec.type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in {"true", "1", "yes"}:
                return True
            if value.lower() in {"false", "0", "no"}:
                return False
        raise ValueError(f"{field} expects true/false.")
    if spec.type == "datetime":
        try:
            ts = pd.to_datetime(value, errors="raise", utc=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} expects a datetime.") from exc
        return ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts
    if spec.type == "duration":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} expects a duration in seconds.") from exc
    if spec.type == "enum":
        s = str(value)
        if spec.enum_values and s not in spec.enum_values:
            raise ValueError(f"{field} must be one of: {', '.join(spec.enum_values)}.")
        return s
    return str(value)


async def apply_cell_edit(
    log_id: str,
    row_index: int,
    field: str,
    raw_value: Any,
    specs: list[ColumnSpec],
    session: AsyncSession,
    user_id: str,
) -> CellEditOutcome:
    """Apply a single-cell edit, rewrite parquet, recompute cases, persist
    audit + header counters. Returns the resulting row + new index.
    """
    coerced = coerce_value(field, raw_value, specs)
    lock = await _lock_for(log_id)
    async with lock:
        outcome = await asyncio.to_thread(
            _rewrite_with_edits, log_id, user_id, [(row_index, field, coerced)]
        )
        await _persist_after_write(log_id, outcome, session, user_id)
    return outcome  # type: ignore[return-value]


async def apply_bulk_fill(
    log_id: str,
    row_indices: list[int],
    field: str,
    raw_value: Any,
    specs: list[ColumnSpec],
    session: AsyncSession,
    user_id: str,
) -> BulkFillOutcome:
    coerced = coerce_value(field, raw_value, specs)
    edits = [(idx, field, coerced) for idx in row_indices]
    lock = await _lock_for(log_id)
    async with lock:
        outcome = await asyncio.to_thread(_rewrite_with_edits, log_id, user_id, edits)
        await _persist_after_write(log_id, outcome, session, user_id)
    return BulkFillOutcome(
        updated=len(edits),
        header=outcome.header,
        audit=outcome.audit,
    )


def _rewrite_with_edits(
    log_id: str,
    user_id: str,
    edits: list[tuple[int, str, Any]],
) -> CellEditOutcome:
    """Synchronous parquet rewrite - runs on a worker thread."""
    paths = log_paths(log_id, user_id)
    # Pull the log dir from S3 if the local cache is cold (no-op in local mode).
    storage_sync.hydrate_dir_sync(paths.root)
    if not paths.events.exists():
        raise FileNotFoundError(f"events.parquet missing for log {log_id!r}.")

    df = pd.read_parquet(paths.events)
    if df.empty:
        raise ValueError("Cannot edit an empty events table.")

    # Apply edits, capturing old/new pairs for the audit log.
    audit: list[tuple[int, str, Any, Any]] = []
    for row_index, field, value in edits:
        if not (0 <= row_index < len(df)):
            raise IndexError(f"row_index {row_index} out of range (0..{len(df) - 1}).")
        if field not in df.columns:
            raise KeyError(f"column {field!r} does not exist on this log.")
        old = df.at[row_index, field]
        df.at[row_index, field] = _align_to_column(df[field], value)
        audit.append((row_index, field, _to_jsonable(old), _to_jsonable(value)))

    # Type discipline on the canonical columns before re-sorting / aggregating.
    df["case_id"] = df["case_id"].astype(str)
    df["activity"] = df["activity"].astype(str)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=False)
    if df["timestamp"].isna().any():
        raise ValueError("timestamp cannot be empty after editing.")

    edited_fields = {f for _, f, _ in edits}
    last_pre_sort_index = edits[-1][0]
    if edited_fields & _RESORT_FIELDS:
        # Track pre-sort positions so we can return the new index of the most
        # recently edited row to the client (UX: keep selection stable).
        df["__orig_idx__"] = range(len(df))
        df = df.sort_values(["case_id", "timestamp"], kind="mergesort").reset_index(drop=True)
        new_idx_arr = df.index[df["__orig_idx__"] == last_pre_sort_index].tolist()
        new_row_index = int(new_idx_arr[0]) if new_idx_arr else last_pre_sort_index
        df = df.drop(columns=["__orig_idx__"])
    else:
        new_row_index = last_pre_sort_index

    # Atomic write: temp file in the same directory, then os.replace().
    tmp = paths.events.with_suffix(paths.events.suffix + ".tmp")
    df.to_parquet(tmp, index=False, engine="pyarrow", compression="zstd")
    os.replace(tmp, paths.events)

    cases_df = compute_cases(df)
    cases_tmp = paths.cases.with_suffix(paths.cases.suffix + ".tmp")
    cases_df.to_parquet(cases_tmp, index=False, engine="pyarrow", compression="zstd")
    os.replace(cases_tmp, paths.cases)

    # Build the response row + refreshed header.
    row = _row_to_jsonable(df.iloc[new_row_index].to_dict())
    header = EventsHeader(
        events_count=len(df),
        cases_count=int(cases_df.shape[0]),
        variants_count=int(cases_df["variant_id"].nunique()) if not cases_df.empty else 0,
        date_min=_min_datetime(df["timestamp"]),
        date_max=_max_datetime(df["timestamp"]),
    )

    return CellEditOutcome(
        row=row,
        old_row_index=last_pre_sort_index,
        new_row_index=new_row_index,
        header=header,
        audit=audit,
    )


async def _persist_after_write(
    log_id: str,
    outcome: CellEditOutcome,
    session: AsyncSession,
    user_id: str,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    await session.execute(
        update(EventLog)
        .where(EventLog.id == log_id)
        .values(
            events_count=outcome.header.events_count,
            cases_count=outcome.header.cases_count,
            variants_count=outcome.header.variants_count,
            date_min=outcome.header.date_min,
            date_max=outcome.header.date_max,
            last_edited_at=now,
        )
    )
    for row_index, field, old, new in outcome.audit:
        session.add(
            EventEdit(
                user_id=user_id,
                log_id=log_id,
                row_index=row_index,
                field=field,
                old_value_json=old,
                new_value_json=new,
                edited_at=now,
            )
        )
    await session.commit()
    # Re-mirror the edited parquet to the S3 primary store (no-op in local mode).
    await storage_sync.persist_log(log_id=log_id, user_id=user_id)


def _align_to_column(series: pd.Series, value: Any) -> Any:
    """Match the value's tz-awareness to the column's so pandas accepts the
    assignment. The CSV importer leaves ISO-with-offset timestamps tz-aware,
    so the editor must round-trip the same way.
    """
    if value is None:
        return value
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        ts = pd.to_datetime(value, errors="coerce", utc=False)
        if pd.isna(ts):
            return None
        col_tz = getattr(series.dtype, "tz", None)
        if col_tz is not None:
            ts = ts.tz_localize(col_tz) if ts.tzinfo is None else ts.tz_convert(col_tz)
        elif ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        return ts
    return value


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        # Naive-UTC storage - serialize with the offset (matches `_row_dict`
        # in the events route, so the post-edit row echo parses identically).
        return None if pd.isna(value) else utc_isoformat(value)
    if isinstance(value, datetime):
        return utc_isoformat(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def _row_to_jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _to_jsonable(v) for k, v in row.items()}


def _min_datetime(series: pd.Series) -> datetime | None:
    if series.empty:
        return None
    value = series.min()
    if pd.isna(value):
        return None
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


def _max_datetime(series: pd.Series) -> datetime | None:
    if series.empty:
        return None
    value = series.max()
    if pd.isna(value):
        return None
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


__all__: Iterable[str] = (
    "BulkFillOutcome",
    "CellEditOutcome",
    "apply_bulk_fill",
    "apply_cell_edit",
    "coerce_value",
)
