"""ObjectCentricLogAccess - lazy view of an imported OCEL log's parquet tables.

The object-centric counterpart to `EventLogAccess`. Reads the four tables the
OCEL importer wrote under ``ocel/`` (events, objects, relations, o2o). Kept
entirely separate from the case-centric `EventLogAccess`: neither class ever
touches the other's parquet files, so the two log models stay fully isolated.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import duckdb

from mate.api.ingest.storage import log_paths
from mate.api.modules.event_filters import quote_ident as _quote_ident
from mate.api.storage import eviction
from mate.api.storage import sync as storage_sync

# DuckDB view name → the LogPaths attribute holding its parquet.
_VIEWS = {
    "ocel_events": "ocel_events",
    "ocel_objects": "ocel_objects",
    "ocel_relations": "ocel_relations",
    "ocel_o2o": "ocel_o2o",
}


class ObjectCentricLogAccess:
    """Async-context-manager view of a single object-centric (OCEL) log.

    Construction is cheap; readers materialise on demand and cache nothing.
    Mirrors `EventLogAccess`'s DuckDB pattern but registers four independent
    views (one per OCEL table) rather than the case-centric ``events``/``cases``.
    """

    def __init__(self, log_id: str, user_id: str) -> None:
        self.log_id = log_id
        self.user_id = user_id
        self._paths = log_paths(log_id, user_id)
        self._conn: duckdb.DuckDBPyConnection | None = None
        # Pins the log dir against the S3 cache reaper for the read's lifetime.
        self._lease: str | None = None

    async def __aenter__(self) -> ObjectCentricLogAccess:
        # Lease before hydrate so the reaper can't evict the tree mid-read.
        self._lease = await eviction.acquire_lease_async(self._paths.root)
        try:
            # In S3 mode, pull the log dir from the bucket if the cache is cold.
            await storage_sync.hydrate_log(self.user_id, self.log_id)
            if not self._paths.ocel_events.exists():
                raise FileNotFoundError(
                    f"OCEL log {self.log_id} has no ocel/events.parquet - import not finished?"
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
        return self._paths.ocel_events

    async def events_pandas(self) -> Any:
        return await self._read(self._paths.ocel_events)

    async def objects_pandas(self) -> Any:
        return await self._read(self._paths.ocel_objects)

    async def relations_pandas(self) -> Any:
        return await self._read(self._paths.ocel_relations)

    async def o2o_pandas(self) -> Any:
        return await self._read(self._paths.ocel_o2o)

    async def _read(self, path: Path) -> Any:
        import pandas as pd

        return await asyncio.to_thread(pd.read_parquet, path)

    async def ocel(self) -> Any:
        """Reconstruct a pm4py OCEL object from the persisted parquet tables -
        used by object-centric discovery (e.g. ``pm4py.discover_ocdfg``)."""
        try:
            import pm4py  # noqa: F401
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pm4py is not available - declare it in your manifest's "
                "dependencies.python.inherit or .packages."
            ) from exc

        def _build() -> Any:
            import pandas as pd
            from pm4py.objects.ocel.obj import OCEL

            return OCEL(
                events=pd.read_parquet(self._paths.ocel_events),
                objects=pd.read_parquet(self._paths.ocel_objects),
                relations=pd.read_parquet(self._paths.ocel_relations),
                o2o=pd.read_parquet(self._paths.ocel_o2o),
            )

        return await asyncio.to_thread(_build)

    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        def _run() -> list[tuple]:
            self._ensure_conn()
            assert self._conn is not None
            return self._conn.execute(sql, params or []).fetchall()

        return await asyncio.to_thread(_run)

    async def duckdb_fetch_with_columns(
        self, sql: str, params: list | tuple | None = None
    ) -> tuple[list[str], list[tuple]]:
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
        # Each OCEL table becomes its own independent view. Paths are derived
        # from validated log_ids (UUIDs); escape single quotes defensively as
        # DuckDB rejects parameter binding inside CREATE VIEW.
        for view, attr in _VIEWS.items():
            path = str(getattr(self._paths, attr)).replace("'", "''")
            self._conn.execute(
                f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet('{path}')"
            )


__all__ = ["ObjectCentricLogAccess", "_quote_ident"]
