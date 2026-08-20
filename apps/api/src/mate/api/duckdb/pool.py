"""Per-thread DuckDB connection pool.

DuckDB is single-threaded per connection (§9). We hand out one connection per
worker thread via `contextvars` and re-attach the SQLite metadata DB on demand
so DuckDB queries can join Parquet event logs against SQLite tables (§3.3).

For phase 3, the pool is initialised but only consumed by ad-hoc DuckDB
queries inside the ingest path. Module-author access via `EventLogAccess`
arrives in phase 5.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import duckdb

from mate.api.config import get_settings

T = TypeVar("T")

_thread_conn: contextvars.ContextVar[duckdb.DuckDBPyConnection | None] = contextvars.ContextVar(
    "_duckdb_conn", default=None
)


def _sqlite_path_from_url(url: str) -> Path | None:
    if "sqlite" not in url:
        return None
    if "///" not in url:
        return None
    raw = url.split("///", 1)[1]
    return Path(raw)


class DuckDBPool:
    """Lazy thread-local DuckDB connections, all attached to the same SQLite metadata DB."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: list[duckdb.DuckDBPyConnection] = []

    def _new_connection(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(database=":memory:")
        settings = get_settings()

        # Tuning (§9). Each statement is best-effort - older/newer DuckDB builds
        # differ on which PRAGMAs exist, and a missing one must never break the
        # connection. Object cache keeps Parquet footers between queries (big win
        # when many widgets scan the same file); unordered scans cut memory/time
        # for aggregations; the optional thread cap curbs core oversubscription
        # when many widget queries run at once.
        def _try(sql: str) -> None:
            try:
                conn.execute(sql)
            except duckdb.Error:
                pass

        if settings.duckdb_threads > 0:
            _try(f"PRAGMA threads={int(settings.duckdb_threads)}")
        if settings.duckdb_memory_limit:
            _try(f"PRAGMA memory_limit='{settings.duckdb_memory_limit}'")
        _try("PRAGMA enable_object_cache=true")
        _try("SET preserve_insertion_order=false")

        sqlite_path = _sqlite_path_from_url(settings.database_url)
        if sqlite_path is not None and sqlite_path.exists():
            try:
                conn.execute("INSTALL sqlite_scanner")
                conn.execute("LOAD sqlite_scanner")
                conn.execute(f"ATTACH '{sqlite_path}' AS meta (TYPE sqlite)")
            except duckdb.Error:
                # SQLite extension may not be available in some builds; queries that
                # need the metadata join will surface an explicit error instead.
                pass
        with self._lock:
            self._conns.append(conn)
        return conn

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = _thread_conn.get()
        if conn is None:
            conn = self._new_connection()
            _thread_conn.set(conn)
        return conn

    def execute(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        cur = self._conn().execute(sql, params or [])
        return cur.fetchall()

    def run_in_thread(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> asyncio.Future[T]:
        async def _await() -> T:
            return await asyncio.to_thread(lambda: fn(self._conn()))

        return asyncio.ensure_future(_await())

    def close_all(self) -> None:
        with self._lock:
            for conn in self._conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._conns.clear()


_pool: DuckDBPool | None = None


def get_duckdb_pool() -> DuckDBPool:
    global _pool
    if _pool is None:
        _pool = DuckDBPool()
    return _pool
