"""Tests for the read-only BPMN surface of the Discovery module.

The platform's loader injects a real ``ModuleContext`` at runtime; here we
stand up minimal fakes for the *only* Protocol surface the BPMN route touches
(``ctx.event_log`` as an async context manager exposing ``.pandas()`` and
``ctx.cache`` with ``get``/``set``/``exists``/``delete``). The fakes mirror the
contracts in ``mate.sdk.context`` (``EventLogAccessProtocol`` /
``ResultCacheProtocol``) - nothing reaches past them.

Covered:
- ``GET /bpmn`` returns a payload carrying non-empty BPMN XML.
- A second ``GET /bpmn`` is served from cache without recomputing.
- The former editing routes (upload / save / reset) no longer exist on the
  module class - the view is read-only.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pandas as pd
from modules.discovery.module import DiscoveryModule

# --------------------------------------------------------------------------
# Minimal Protocol-faithful fakes
# --------------------------------------------------------------------------


class _FakeEventLog:
    """Stands in for ``EventLogAccessProtocol`` - async-CM yielding a DataFrame."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    async def __aenter__(self) -> _FakeEventLog:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pandas(self) -> pd.DataFrame:
        return self._df

    async def materialize_parquet(self) -> tuple[str, bool]:
        # Mirrors the real `EventLogAccess.materialize_parquet`: hand the worker a
        # Parquet path (temp here) so it reads via `pandas.read_parquet`.
        fd, tmp = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        self._df.to_parquet(tmp, index=False)
        return tmp, True


class _FakeCache:
    """In-memory ``ResultCacheProtocol`` with a per-key ``set`` counter so a
    test can prove the second request served from cache (no recompute)."""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}
        self.set_calls: dict[str, int] = {}

    async def get(self, key: str) -> object:
        return self._store.get(key)

    async def set(self, key: str, value: object) -> None:
        self._store[key] = value
        self.set_calls[key] = self.set_calls.get(key, 0) + 1

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeCtx:
    """Just enough of ``ModuleContext`` for the BPMN route."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.log_id = "log-test"
        self.module_id = "discovery"
        self.event_log = _FakeEventLog(df)
        self.cache = _FakeCache()

    async def run_in_process(self, fn: object, *args: object, **kwargs: object) -> object:
        # The real ctx ships `fn` to a process pool; tests run the worker
        # in-process so it still exercises the parquet-read + compute path.
        return fn(*args, **kwargs)  # type: ignore[operator]


def _sample_log() -> pd.DataFrame:
    """A small but structurally rich case-centric log.

    Three variants over five activities so the inductive miner produces a
    non-trivial BPMN (tasks + gateways), exercising the real serializer path.
    """
    variants = [
        ["receive", "check", "approve", "pay", "close"],
        ["receive", "check", "reject", "close"],
        ["receive", "check", "approve", "close"],
    ]
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2024-01-01")
    case_no = 0
    for v_idx, trace in enumerate(variants):
        for _rep in range(8):  # >= min_cases / min_events for the module
            case_no += 1
            case_id = f"c{case_no}"
            start = base + pd.Timedelta(days=case_no, hours=v_idx)
            for step, act in enumerate(trace):
                rows.append(
                    {
                        "case_id": case_id,
                        "activity": act,
                        "timestamp": start + pd.Timedelta(hours=step),
                    }
                )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_get_bpmn_returns_non_empty_xml() -> None:
    ctx = _FakeCtx(_sample_log())
    out = asyncio.run(DiscoveryModule().bpmn(ctx))  # type: ignore[arg-type]

    assert isinstance(out, dict)
    assert out.get("kind") == "bpmn"
    xml = out.get("xml")
    assert isinstance(xml, str)
    assert xml.strip(), "BPMN XML must be non-empty"
    # It is genuine BPMN, not an empty stub.
    assert "bpmn" in xml.lower()
    assert "definitions" in xml.lower()


def test_second_get_bpmn_is_served_from_cache() -> None:
    module = DiscoveryModule()
    ctx = _FakeCtx(_sample_log())

    first = asyncio.run(module.bpmn(ctx))  # type: ignore[arg-type]
    # The default (no-noise) path caches under "bpmn_inductive".
    assert ctx.cache.set_calls.get("bpmn_inductive") == 1
    assert asyncio.run(ctx.cache.exists("bpmn_inductive")) is True

    second = asyncio.run(module.bpmn(ctx))  # type: ignore[arg-type]
    # No additional compute → no second set() for that key.
    assert ctx.cache.set_calls.get("bpmn_inductive") == 1
    assert second == first


def test_editing_routes_are_removed() -> None:
    # The BPMN view is strictly read-only: the upload / save / reset endpoints
    # must no longer exist on the module.
    for removed in ("bpmn_upload", "bpmn_save", "bpmn_reset"):
        assert not hasattr(DiscoveryModule, removed), f"{removed} should be gone"
    # The read-only surface is still present.
    assert hasattr(DiscoveryModule, "bpmn")
    assert hasattr(DiscoveryModule, "bpmn_download")
