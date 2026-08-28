"""EventLogAccess - lazy view of an imported log's Parquet (§5.5).

Also hosts the read-only helpers behind the Events tab: `column_specs()`
infers column types from a small parquet sample and `data_quality()`
returns null/distinct counts via DuckDB. Both run on `to_thread` so they
don't block the event loop on large logs.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import duckdb

from mate.api.config import get_settings
from mate.api.ingest.storage import log_paths
from mate.api.modules.event_filters import quote_ident as _quote_ident
from mate.api.modules.event_filters import render_filter_sql
from mate.api.schemas.event_log_data import (
    ColumnQuality,
    ColumnRole,
    ColumnSpec,
    ColumnType,
    DataQuality,
)
from mate.api.storage import eviction
from mate.api.storage import sync as storage_sync

CANONICAL_ROLES: dict[str, ColumnRole] = {
    "case_id": "case_id",
    "activity": "activity",
    "timestamp": "timestamp",
    "end_timestamp": "end_timestamp",
    "resource": "resource",
    "cost": "cost",
    "role": "role",
    "lifecycle": "lifecycle",
}
REQUIRED_COLUMNS: frozenset[str] = frozenset({"case_id", "activity", "timestamp"})
ENUM_DETECT_THRESHOLD = 25  # cardinality below which a string column is treated as enum

# Shared, mtime-guarded read cache (§5.5/§8.3). Many dashboard widgets on one log
# each call `pandas()`/`polars()`, re-reading the same Parquet. We cache the
# *immutable* Arrow table per `(path, mtime)` and serve fresh frames from it, so
# N widgets collapse to one disk read + decode without any mutation-sharing risk.
# Bounded by `event_log_cache_entries` (0 disables) and a per-table byte cap.
_READ_CACHE: collections.OrderedDict[tuple[str, float], Any] = collections.OrderedDict()
_READ_CACHE_LOCK = threading.Lock()
_READ_CACHE_MAX_BYTES = 512 * 1024 * 1024


def _read_arrow_cached(path: Path) -> Any:
    """Read `path` to an Arrow table, caching it by `(path, mtime)`. Only the
    unfiltered full-log read goes through here; filtered views vary per request."""
    import pyarrow.parquet as pq

    max_entries = get_settings().event_log_cache_entries
    if max_entries <= 0:
        return pq.read_table(path)
    try:
        key = (str(path), os.path.getmtime(path))
    except OSError:
        return pq.read_table(path)
    with _READ_CACHE_LOCK:
        hit = _READ_CACHE.get(key)
        if hit is not None:
            _READ_CACHE.move_to_end(key)
            return hit
    table = pq.read_table(path)
    if table.nbytes <= _READ_CACHE_MAX_BYTES:
        with _READ_CACHE_LOCK:
            _READ_CACHE[key] = table
            _READ_CACHE.move_to_end(key)
            while len(_READ_CACHE) > max_entries:
                _READ_CACHE.popitem(last=False)
    return table


# Column-spec inference cache. `column_specs()` is on the hot path of every
# Events-tab request (list/patch/filter-values/time-bounds/data-quality) and
# used to re-read the *whole* Parquet file just to sample 2000 rows. The base
# (override-free) specs only change when the file changes, so they are cached
# by `(path, mtime_ns, size)`; per-request override labels/order are applied
# to copies. Rewrites go through tmp+os.replace, which changes the key.
_SPEC_CACHE: collections.OrderedDict[tuple[str, int, int], list[ColumnSpec]] = (
    collections.OrderedDict()
)
_SPEC_CACHE_LOCK = threading.Lock()
_SPEC_CACHE_MAX = 64
_SPEC_SAMPLE_ROWS = 2000


def file_identity(path: Path) -> tuple[str, int, int] | None:
    """Stable identity of a Parquet file for mtime-guarded caches (None if unstat-able)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size)


def _read_head_pandas(path: Path, n: int) -> Any:
    """First `n` rows of a Parquet file as pandas, without decoding the full file."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    batches = []
    got = 0
    for batch in pf.iter_batches(batch_size=min(n, 2048)):
        batches.append(batch)
        got += batch.num_rows
        if got >= n:
            break
    if batches:
        table = pa.Table.from_batches(batches, schema=pf.schema_arrow).slice(0, n)
    else:
        table = pf.schema_arrow.empty_table()
    return table.to_pandas()


def _infer_base_specs(path: Path) -> list[ColumnSpec]:
    """Inference behind `column_specs()` (no overrides applied), cached per file."""
    key = file_identity(path)
    if key is not None:
        with _SPEC_CACHE_LOCK:
            hit = _SPEC_CACHE.get(key)
            if hit is not None:
                _SPEC_CACHE.move_to_end(key)
                return hit

    head = _read_head_pandas(path, _SPEC_SAMPLE_ROWS)
    specs: list[ColumnSpec] = []
    for col in head.columns:
        series = head[col]
        role: ColumnRole = CANONICAL_ROLES.get(col, "custom")
        col_type = _infer_column_type(series)
        enum_values: list[str] | None = None
        if col_type == "string":
            distinct = series.dropna().astype(str).unique().tolist()
            if 0 < len(distinct) <= ENUM_DETECT_THRESHOLD:
                # Lifecycle is the only column we treat as a closed
                # vocabulary. For other low-cardinality strings we
                # still surface the common values for UX suggestions
                # but accept arbitrary input.
                enum_values = sorted(distinct)
                if role == "lifecycle":
                    col_type = "enum"
        specs.append(
            ColumnSpec(
                name=col,
                label=_label_for(col, None),
                role=role,
                type=col_type,
                nullable=True,
                required=col in REQUIRED_COLUMNS,
                enum_values=enum_values,
            )
        )

    if key is not None:
        with _SPEC_CACHE_LOCK:
            _SPEC_CACHE[key] = specs
            _SPEC_CACHE.move_to_end(key)
            while len(_SPEC_CACHE) > _SPEC_CACHE_MAX:
                _SPEC_CACHE.popitem(last=False)
    return specs


class EventLogAccess:
    """Async-context-manager view of a single log.

    Construction is cheap; readers (``pandas()`` / ``polars()`` / ``pm4py()`` /
    ``duckdb_fetch``) materialise on demand and cache nothing - keeping memory
    usage explicit. polars/pm4py are best-effort: if the module didn't
    inherit them, the call raises a clear error.
    """

    def __init__(
        self,
        log_id: str,
        user_id: str,
        active_filter: list[dict[str, Any]] | None = None,
    ) -> None:
        self.log_id = log_id
        self.user_id = user_id
        # The applied Events-tab filter (``EventLog.active_filter``). When set,
        # every read - the DuckDB ``events`` view and the pandas/polars/pm4py
        # frames - is transparently restricted to the matching rows, so modules
        # and analytics see exactly the dataset the user committed. The events
        # *editor* and cell-edit paths construct with ``None`` to stay on the
        # raw rows.
        self._active_filter = active_filter or None
        self._paths = log_paths(log_id, user_id)
        self._conn: duckdb.DuckDBPyConnection | None = None
        # Pins the log dir against the S3 cache reaper for the read's lifetime.
        self._lease: str | None = None

    async def __aenter__(self) -> EventLogAccess:
        # Lease before hydrate so the reaper can't evict the tree out from under
        # the read (or mid-download). Released in __aexit__ / on the error path.
        self._lease = await eviction.acquire_lease_async(self._paths.root)
        try:
            # In S3 mode, pull the log dir from the bucket if the local cache is
            # cold (fresh VM / evicted). No-op in local mode and on a warm cache.
            await storage_sync.hydrate_log(self.user_id, self.log_id)
            if not self._paths.events.exists():
                raise FileNotFoundError(
                    f"Event log {self.log_id} has no events.parquet - import not finished?"
                )
        except BaseException:
            # Lease was just acquired above; release it on any entry failure so a
            # __aexit__ that never runs can't strand it.
            eviction.release_lease(self._lease)
            self._lease = None
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        if self._lease is not None:
            eviction.release_lease(self._lease)
            self._lease = None

    @property
    def events_path(self) -> Path:
        return self._paths.events

    @property
    def cases_path(self) -> Path:
        return self._paths.cases

    @property
    def active_filter(self) -> list[dict[str, Any]] | None:
        """The applied Events-tab filter (or None). Exposed alongside
        `materialize_parquet` so offloaded compute can reason about the view (§8.3)."""
        return self._active_filter

    async def pandas(self) -> Any:
        if not self._active_filter:
            return await asyncio.to_thread(
                lambda: _read_arrow_cached(self._paths.events).to_pandas()
            )

        def _run() -> Any:
            self._ensure_conn()
            assert self._conn is not None
            return self._conn.execute("SELECT * FROM events").df()

        return await asyncio.to_thread(_run)

    async def materialize_parquet(self) -> tuple[str, bool]:
        """Hand the *current view* to a `ctx.run_in_process` worker as a Parquet
        path (§8.3). Returns `(path, is_temp)`: the raw events Parquet when no
        filter is applied (zero-copy - the common precompute case), else a temp
        Parquet of the filtered rows that the caller must delete. Lets offloaded
        compute read the exact rows with a plain `pandas.read_parquet` - no event
        loop, no `EventLogAccess`, and no platform import inside the worker.
        """
        if not self._active_filter:
            return str(self._paths.events), False
        df = await self.pandas()
        fd, tmp = tempfile.mkstemp(prefix="ff_offload_", suffix=".parquet")
        os.close(fd)
        await asyncio.to_thread(df.to_parquet, tmp, index=False)
        return tmp, True

    async def polars(self) -> Any:
        try:
            import polars as pl
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "polars is not available - declare it in your manifest's "
                "dependencies.python.inherit or .packages."
            ) from exc
        if not self._active_filter:
            return await asyncio.to_thread(
                lambda: pl.from_arrow(_read_arrow_cached(self._paths.events))
            )

        def _run() -> Any:
            self._ensure_conn()
            assert self._conn is not None
            return self._conn.execute("SELECT * FROM events").pl()

        return await asyncio.to_thread(_run)

    async def pm4py(self) -> Any:
        try:
            import pm4py  # noqa: F401
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pm4py is not available - declare it in your manifest's "
                "dependencies.python.inherit or .packages."
            ) from exc

        df = await self.pandas()

        def _to_event_log() -> Any:
            import pm4py.utils as pmu

            renamed = df.rename(
                columns={
                    "case_id": "case:concept:name",
                    "activity": "concept:name",
                    "timestamp": "time:timestamp",
                }
            )
            return pmu.format_dataframe(renamed)

        return await asyncio.to_thread(_to_event_log)

    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        def _run() -> list[tuple]:
            self._ensure_conn()
            assert self._conn is not None
            return self._conn.execute(sql, params or []).fetchall()

        return await asyncio.to_thread(_run)

    async def duckdb_fetch_with_columns(
        self, sql: str, params: list | tuple | None = None
    ) -> tuple[list[str], list[tuple]]:
        """Like `duckdb_fetch` but also returns column names - used by the
        events route to map rows to dicts without a second metadata call.
        """

        def _run() -> tuple[list[str], list[tuple]]:
            self._ensure_conn()
            assert self._conn is not None
            cur = self._conn.execute(sql, params or [])
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, cur.fetchall()

        return await asyncio.to_thread(_run)

    def _ensure_conn(self) -> None:
        if self._conn is not None:
            return
        self._conn = duckdb.connect(":memory:")
        # DuckDB rejects parameter binding in CREATE VIEW - interpolate
        # the path. Safe: paths are derived from validated log_ids (UUIDs).
        events_path = str(self._paths.events).replace("'", "''")
        self._conn.execute(
            f"CREATE OR REPLACE VIEW events_src AS SELECT * FROM read_parquet('{events_path}')"
        )
        # `events` is the filter-aware view everything reads. With no applied
        # filter it's a passthrough; otherwise the predicate is baked in with
        # escaped literals (DuckDB can't bind `?` inside a view definition).
        where = ""
        if self._active_filter:
            col_names = {
                d[0] for d in self._conn.execute("SELECT * FROM events_src LIMIT 0").description
            }
            where = render_filter_sql(self._active_filter, col_names)
        self._conn.execute(f"CREATE OR REPLACE VIEW events AS SELECT * FROM events_src{where}")
        if self._paths.cases.exists():
            cases_path = str(self._paths.cases).replace("'", "''")
            self._conn.execute(
                f"CREATE OR REPLACE VIEW cases AS SELECT * FROM read_parquet('{cases_path}')"
            )

    async def column_specs(self, overrides: dict[str, Any] | None = None) -> list[ColumnSpec]:
        """Inspect events.parquet's schema (and a small distinct sample) to
        emit `ColumnSpec`s for every column in the events table. The
        per-column override JSON from `EventLog.column_overrides` can rename
        labels and reorder columns - the editor still treats the canonical
        `name` as authoritative.

        Inference reads only the first `_SPEC_SAMPLE_ROWS` rows and is cached
        per file identity; overrides are applied to copies per call.
        """

        def _inspect() -> list[ColumnSpec]:
            base = _infer_base_specs(self._paths.events)
            specs = [s.model_copy(deep=True) for s in base]
            if overrides:
                for spec in specs:
                    spec.label = _label_for(spec.name, overrides)
                specs = _apply_column_order(specs, overrides)
            return specs

        return await asyncio.to_thread(_inspect)

    async def data_quality(self, specs: list[ColumnSpec] | None = None) -> DataQuality:
        """Per-column null + distinct counts for Settings → Data quality."""
        if specs is None:
            specs = await self.column_specs()

        def _quality() -> DataQuality:
            self._ensure_conn()
            assert self._conn is not None
            (total,) = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
            cols: list[ColumnQuality] = []
            for spec in specs:
                quoted = _quote_ident(spec.name)
                null_count, distinct_count = self._conn.execute(  # type: ignore[union-attr]
                    f"SELECT COUNT_IF({quoted} IS NULL), COUNT(DISTINCT {quoted}) FROM events"
                ).fetchone()
                cols.append(
                    ColumnQuality(
                        column=spec.name,
                        label=spec.label,
                        type=spec.type,
                        role=spec.role,
                        null_count=int(null_count or 0),
                        null_pct=(float(null_count or 0) / float(total)) if total else 0.0,
                        distinct_count=int(distinct_count or 0),
                    )
                )
            return DataQuality(total_events=int(total or 0), columns=cols)

        return await asyncio.to_thread(_quality)


def _infer_column_type(series: Any) -> ColumnType:
    import pandas as pd

    dtype = series.dtype
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_numeric_dtype(dtype):
        return "number"
    if pd.api.types.is_timedelta64_dtype(dtype):
        return "duration"
    return "string"


def _label_for(name: str, overrides: dict[str, Any] | None) -> str:
    if overrides and isinstance(overrides.get("labels"), dict):
        label = overrides["labels"].get(name)
        if isinstance(label, str) and label.strip():
            return label
    return name.replace("_", " ").strip().capitalize()


def _apply_column_order(
    specs: list[ColumnSpec], overrides: dict[str, Any] | None
) -> list[ColumnSpec]:
    if not overrides:
        return specs
    order = overrides.get("order")
    if not isinstance(order, list):
        return specs
    by_name = {s.name: s for s in specs}
    ordered = [by_name.pop(name) for name in order if name in by_name]
    ordered.extend(by_name.values())
    return ordered


# `_quote_ident` is re-exported from event_filters (single source of truth);
# event_log_data still imports it from here.
__all__ = ["EventLogAccess", "_quote_ident"]
