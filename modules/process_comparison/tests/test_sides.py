"""The ``sides`` wire: two logs, or two filtered cohorts of the same log.

A side is ``{log, filter}``, and the *pair* (log, filter) is what identifies it
- which is what makes A and B legally point at the same log as long as their
filters differ. These tests pin that contract plus the guards around it: the
duplicate-side refusal, the filter's presence in the cache key, and the routes
actually opening each side with its own filter.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi import HTTPException
from modules.process_comparison.module import (
    ProcessComparisonModule,
    _cache_key,
    _decode_sides,
)

NORTH = [{"field": "region", "op": "equals", "value": "north"}]
SOUTH = [{"field": "region", "op": "equals", "value": "south"}]


def _encode(sides: list[dict[str, Any]]) -> str:
    return base64.b64encode(json.dumps(sides).encode()).decode()


# -- decoding ---------------------------------------------------------------


def test_decodes_two_logs() -> None:
    sides = _decode_sides(_encode([{"log": "a"}, {"log": "b", "filter": NORTH}]))
    assert [s["log"] for s in sides] == ["a", "b"]
    # An absent filter means "use that log's committed Events-tab filter".
    assert sides[0]["filter"] is None
    assert sides[1]["filter"] == [{"field": "region", "op": "equals", "value": "north"}]


def test_same_log_two_filters_is_a_valid_pair() -> None:
    sides = _decode_sides(_encode([{"log": "a", "filter": NORTH}, {"log": "a", "filter": SOUTH}]))
    assert [s["log"] for s in sides] == ["a", "a"]


def test_same_log_same_filter_is_refused() -> None:
    with pytest.raises(HTTPException) as exc:
        _decode_sides(_encode([{"log": "a", "filter": NORTH}, {"log": "a", "filter": NORTH}]))
    assert exc.value.status_code == 422
    assert "different filter" in str(exc.value.detail)


def test_duplicate_detection_ignores_filter_order() -> None:
    both = [*NORTH, {"field": "amount", "op": "gte", "value": 10}]
    with pytest.raises(HTTPException):
        _decode_sides(_encode([{"log": "a", "filter": both}, {"log": "a", "filter": both[::-1]}]))


def test_empty_filter_differs_from_no_filter() -> None:
    # `[]` = raw log, `None` = the log's committed filter. Two distinct views,
    # so the pair is legal rather than a duplicate.
    sides = _decode_sides(_encode([{"log": "a"}, {"log": "a", "filter": []}]))
    assert sides[0]["filter"] is None
    assert sides[1]["filter"] == []


@pytest.mark.parametrize(
    "raw",
    [
        "",  # nothing selected
        "not-base64!!",
        base64.b64encode(b'{"log": "a"}').decode(),  # object, not a list
        _encode([{"log": ""}]),  # empty id
        _encode([{"log": "a", "filter": [{"field": "x", "op": "nope"}]}]),  # unknown op
        _encode([{"log": "a", "filter": [{"op": "equals", "value": 1}]}]),  # no field
        _encode([{"log": "a", "filter": "region=north"}]),  # filter not a list
        _encode([{"log": f"l{i}"} for i in range(7)]),  # over the side cap
    ],
)
def test_malformed_sides_are_422(raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _decode_sides(raw)
    assert exc.value.status_code == 422


# -- cache identity ---------------------------------------------------------


def test_cache_key_separates_filters_on_one_log() -> None:
    north = [{"log": "a", "filter": NORTH}, {"log": "a", "filter": SOUTH}]
    other = [{"log": "a", "filter": NORTH}, {"log": "a", "filter": []}]
    assert _cache_key("summary", north, [1.0, 1.0]) != _cache_key("summary", other, [1.0, 1.0])


def test_cache_key_stable_and_mtime_sensitive() -> None:
    sides = [{"log": "a", "filter": NORTH}, {"log": "b", "filter": None}]
    assert _cache_key("summary", sides, [1.0, 2.0]) == _cache_key("summary", sides, [1.0, 2.0])
    assert _cache_key("summary", sides, [1.0, 2.0]) != _cache_key("summary", sides, [1.0, 3.0])


# -- routes -----------------------------------------------------------------


def _log(traces: list[list[str]]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2020-01-01")
    for ci, acts in enumerate(traces):
        for ei, a in enumerate(acts):
            rows.append(
                {
                    "case_id": f"c{ci}",
                    "activity": a,
                    "timestamp": base + pd.Timedelta(hours=ci * 24 + ei),
                }
            )
    return pd.DataFrame(rows)


class _Access:
    """Stand-in for EventLogAccess: serves one prepared frame."""

    def __init__(self, df: pd.DataFrame, path: Path) -> None:
        self._df = df
        self.events_path = path

    async def __aenter__(self) -> _Access:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pandas(self) -> pd.DataFrame:
        return self._df


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.store[key] = value


class _Ctx:
    """ctx stub whose ``open_event_log`` serves a frame per (log, filter)."""

    def __init__(self, frames: dict[str, pd.DataFrame], path: Path) -> None:
        self._frames = frames
        self._path = path
        self.cache = _Cache()
        self.log_id = "log-a"
        self.module_id = "process_comparison"
        self.user_id = "user-1"
        self.calls: list[tuple[str, Any]] = []

    async def open_event_log(
        self, log_id: str, filters: list[dict[str, Any]] | None = None
    ) -> _Access:
        self.calls.append((log_id, filters))
        key = json.dumps(filters, sort_keys=True)
        return _Access(self._frames[key], self._path)


def _ctx(tmp_path: Path, north: pd.DataFrame, south: pd.DataFrame) -> _Ctx:
    events = tmp_path / "events.parquet"
    events.write_bytes(b"")
    return _Ctx(
        {json.dumps(NORTH, sort_keys=True): north, json.dumps(SOUTH, sort_keys=True): south},
        events,
    )


def test_route_opens_each_side_with_its_own_filter(tmp_path: Path) -> None:
    """Same log on both sides, one filter each - the cohort comparison."""
    ctx = _ctx(tmp_path, _log([["a", "b", "c"]] * 3), _log([["a", "b", "d"]] * 2))
    mod = ProcessComparisonModule()
    sides = _encode([{"log": "log-a", "filter": NORTH}, {"log": "log-a", "filter": SOUTH}])

    payload = asyncio.run(mod.summary(ctx, sides=sides))  # type: ignore[arg-type]

    assert ctx.calls == [("log-a", NORTH), ("log-a", SOUTH)]
    assert payload["baseline_log_id"] == "log-a"
    assert payload["other_log_id"] == "log-a"
    # The sides ride along, so a reader (panel or AI) can tell the cohorts apart.
    assert payload["sides"][0]["filter"] == NORTH
    kpis = {k["key"]: k for k in payload["kpis"]}
    assert kpis["cases"]["value_a"] == 3
    assert kpis["cases"]["value_b"] == 2


def test_second_call_is_served_from_cache(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _log([["a", "b"]] * 2), _log([["a", "c"]] * 2))
    mod = ProcessComparisonModule()
    sides = _encode([{"log": "log-a", "filter": NORTH}, {"log": "log-a", "filter": SOUTH}])

    asyncio.run(mod.summary(ctx, sides=sides))  # type: ignore[arg-type]
    asyncio.run(mod.summary(ctx, sides=sides))  # type: ignore[arg-type]

    # Ownership is re-checked on every request (open_event_log runs twice per
    # call) but the frames are only crunched once.
    assert len(ctx.calls) == 4
    assert len(ctx.cache.store) == 1


def test_side_filtered_to_nothing_is_422(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _log([["a", "b"]]), _log([]))
    mod = ProcessComparisonModule()
    sides = _encode([{"log": "log-a", "filter": NORTH}, {"log": "log-a", "filter": SOUTH}])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.summary(ctx, sides=sides))  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    assert "Side B" in str(exc.value.detail)


def test_unscoped_call_computes_without_caching(tmp_path: Path) -> None:
    """No `log_id` on the request → unbound cache. Recompute, never 500.

    The platform binds `ctx.cache` to the route's `log_id` scope, not to the
    sides; an unbound cache raises on get/set, which used to surface as a 500
    after the comparison had already been computed.
    """

    class _ExplodingCache:
        async def get(self, key: str) -> Any:
            raise RuntimeError("This handler isn't scoped to a log_id.")

        async def set(self, key: str, value: Any) -> None:
            raise RuntimeError("This handler isn't scoped to a log_id.")

    ctx = _ctx(tmp_path, _log([["a", "b"]]), _log([["a", "c"]]))
    ctx.log_id = ""
    ctx.cache = _ExplodingCache()  # type: ignore[assignment]
    mod = ProcessComparisonModule()
    sides = _encode([{"log": "log-a", "filter": NORTH}, {"log": "log-a", "filter": SOUTH}])

    payload = asyncio.run(mod.summary(ctx, sides=sides))  # type: ignore[arg-type]
    assert payload["kind"] == "summary_delta"


def test_pairwise_route_refuses_three_sides(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _log([["a"]]), _log([["b"]]))
    mod = ProcessComparisonModule()
    sides = _encode(
        [
            {"log": "log-a", "filter": NORTH},
            {"log": "log-a", "filter": SOUTH},
            {"log": "log-b", "filter": None},
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.summary(ctx, sides=sides))  # type: ignore[arg-type]
    assert exc.value.status_code == 422


def test_set_route_needs_two_sides(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _log([["a"]]), _log([["b"]]))
    mod = ProcessComparisonModule()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.variants(ctx, sides=_encode([{"log": "log-a", "filter": NORTH}])))  # type: ignore[arg-type]
    assert exc.value.status_code == 422
