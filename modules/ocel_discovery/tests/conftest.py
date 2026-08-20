"""Test harness for the OCEL discovery module.

Puts the repo root on ``sys.path`` so ``modules.ocel_discovery`` resolves, and
provides a small in-memory DuckDB-backed fake of ``ctx.object_log`` so the
route handlers (which read through ``ctx.object_log``) can be exercised without
the full API stack.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pm4py.objects.ocel.obj import OCEL

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def build_sample_ocel() -> OCEL:
    """A small two-object-type OCEL (``order`` + ``item``) where orders and
    items share events, so interaction / matrix / lifecycle all have content."""
    rel_rows = [
        ("e1", "create order", "2024-01-01T00:00:00", "o1", "order"),
        ("e2", "add item", "2024-01-02T00:00:00", "o1", "order"),
        ("e2", "add item", "2024-01-02T00:00:00", "i1", "item"),
        ("e3", "add item", "2024-01-03T00:00:00", "o1", "order"),
        ("e3", "add item", "2024-01-03T00:00:00", "i2", "item"),
        ("e4", "pay", "2024-01-04T00:00:00", "o1", "order"),
        ("e5", "ship", "2024-01-05T00:00:00", "i1", "item"),
        ("e5", "ship", "2024-01-05T00:00:00", "i2", "item"),
    ]
    relations = pd.DataFrame(
        rel_rows, columns=["ocel:eid", "ocel:activity", "ts", "ocel:oid", "ocel:type"]
    )
    relations["ocel:timestamp"] = pd.to_datetime(relations["ts"], utc=True)
    relations = relations.drop(columns=["ts"])
    relations["ocel:qualifier"] = ""

    events = (
        relations[["ocel:eid", "ocel:activity", "ocel:timestamp"]]
        .drop_duplicates("ocel:eid")
        .reset_index(drop=True)
    )
    objects = pd.DataFrame({"ocel:oid": ["o1", "i1", "i2"], "ocel:type": ["order", "item", "item"]})
    return OCEL(events=events, objects=objects, relations=relations)


class FakeObjectLog:
    """Async-context-manager fake of ``ObjectCentricLogAccess`` backed by an
    in-memory DuckDB over the OCEL's four tables."""

    def __init__(self, ocel: OCEL) -> None:
        self._ocel = ocel
        self._con: Any | None = None

    async def __aenter__(self) -> FakeObjectLog:
        import duckdb

        self._con = duckdb.connect(":memory:")
        self._con.register("ocel_events", self._ocel.events)
        self._con.register("ocel_objects", self._ocel.objects)
        self._con.register("ocel_relations", self._ocel.relations)
        self._con.register("ocel_o2o", self._ocel.o2o)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        assert self._con is not None
        return self._con.execute(sql, params or []).fetchall()

    async def ocel(self) -> OCEL:
        return self._ocel


class FakeCtx:
    """Minimal ``ModuleContext`` stand-in exposing only ``object_log``."""

    def __init__(self, ocel: OCEL) -> None:
        self.object_log = FakeObjectLog(ocel)
        self.log_id = "test-log"
        self.module_id = "ocel_discovery"
        self.user_id = "test-user"


@pytest.fixture
def sample_ocel() -> OCEL:
    return build_sample_ocel()


@pytest.fixture
def ctx(sample_ocel: OCEL) -> FakeCtx:
    return FakeCtx(sample_ocel)
