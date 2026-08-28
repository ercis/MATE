"""POST /dfg/layout handler: validation, caching, and the offload seam.

Follows the repo's stub-ctx pattern (see ``test_guidance_payload.py``): the
handler runs against an in-memory cache, a fake event log that materializes a
tiny parquet, and ``run_in_process`` mapped to ``asyncio.to_thread`` with a
call counter — proving the cache short-circuits recomputation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from modules.discovery.layout.model import LAYOUT_VERSION
from modules.discovery.module import (
    DfgLayoutRequest,
    DiscoveryModule,
    _layout_cache_key,
)
from pydantic import ValidationError

from .layout_fixtures import END, START


class _DictCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    async def exists(self, key: str) -> bool:
        return key in self.data

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class _FakeEventLog:
    """Async-CM stand-in whose materialize_parquet hands out a fixed file."""

    def __init__(self, parquet_path: Path) -> None:
        self._path = parquet_path

    async def __aenter__(self) -> _FakeEventLog:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def materialize_parquet(self) -> tuple[str, bool]:
        return str(self._path), False


class _Config:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


class _Ctx:
    def __init__(self, parquet_path: Path) -> None:
        self.cache = _DictCache()
        self.event_log = _FakeEventLog(parquet_path)
        self.config = _Config()
        self.log_id = "log-1"
        self.module_id = "discovery"
        self.user_id = "user-1"
        self.offload_calls = 0

        async def _run_in_process(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
            self.offload_calls += 1
            return await asyncio.to_thread(fn, *args, **kwargs)

        self.run_in_process = _run_in_process


@pytest.fixture()
def ctx(tmp_path: Path) -> _Ctx:
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "case_id": ["c1"] * 3 + ["c2"] * 3 + ["c3"] * 2,
            "activity": ["A", "B", "C", "A", "B", "C", "A", "C"],
            "timestamp": pd.to_datetime(
                [
                    "2026-01-01 09:00",
                    "2026-01-01 10:00",
                    "2026-01-01 11:00",
                    "2026-01-02 09:00",
                    "2026-01-02 10:00",
                    "2026-01-02 11:00",
                    "2026-01-03 09:00",
                    "2026-01-03 10:00",
                ]
            ),
        }
    )
    path = tmp_path / "events.parquet"
    frame.to_parquet(path)
    return _Ctx(path)


def _request(**overrides: Any) -> DfgLayoutRequest:
    base: dict[str, Any] = {
        "algorithm": "backbone",
        "nodes": [
            {"id": "A"},
            {"id": "B"},
            {"id": "C"},
            {"id": START, "width": 112.0, "height": 43.0},
            {"id": END, "width": 112.0, "height": 43.0},
        ],
        "edges": [
            [START, "A"],
            ["A", "B"],
            ["B", "C"],
            ["A", "C"],
            ["C", END],
        ],
        "start_id": START,
        "end_id": END,
    }
    base.update(overrides)
    return DfgLayoutRequest.model_validate(base)


def _call(ctx: _Ctx, body: DfgLayoutRequest | None) -> dict[str, Any]:
    module = DiscoveryModule()
    return asyncio.run(module.dfg_layout(ctx, body=body))  # type: ignore[arg-type]


def test_missing_body_is_422(ctx: _Ctx) -> None:
    with pytest.raises(HTTPException) as excinfo:
        _call(ctx, None)
    assert excinfo.value.status_code == 422


def test_unknown_edge_id_is_422(ctx: _Ctx) -> None:
    body = _request(edges=[["A", "ghost"]], nodes=[{"id": "A"}], start_id=None, end_id=None)
    with pytest.raises(HTTPException) as excinfo:
        _call(ctx, body)
    assert excinfo.value.status_code == 422


def test_unknown_terminal_is_422(ctx: _Ctx) -> None:
    body = _request(start_id="not-a-node")
    with pytest.raises(HTTPException) as excinfo:
        _call(ctx, body)
    assert excinfo.value.status_code == 422


def test_over_cap_is_413(ctx: _Ctx) -> None:
    nodes = [{"id": f"n{i}"} for i in range(401)]
    body = _request(nodes=nodes, edges=[], start_id=None, end_id=None)
    with pytest.raises(HTTPException) as excinfo:
        _call(ctx, body)
    assert excinfo.value.status_code == 413


def test_empty_nodes_short_circuits_without_offload(ctx: _Ctx) -> None:
    body = _request(nodes=[], edges=[], start_id=None, end_id=None)
    response = _call(ctx, body)
    assert response["solver"]["status"] == "empty"
    assert ctx.offload_calls == 0
    assert ctx.cache.data == {}  # nothing worth caching


def test_happy_path_and_cache_hit(ctx: _Ctx) -> None:
    body = _request()
    first = _call(ctx, body)
    assert first["kind"] == "dfg_layout"
    assert first["version"] == LAYOUT_VERSION
    assert set(first["x"]) == {"A", "B", "C", START, END}
    assert first["solver"]["status"] in {"optimal", "fallback_no_solver"}
    assert ctx.offload_calls == 1
    assert len(ctx.cache.data) == 1

    second = _call(ctx, body)
    assert second == first
    assert ctx.offload_calls == 1  # served from cache


def test_param_overrides_rotate_the_cache_key(ctx: _Ctx) -> None:
    _call(ctx, _request())
    _call(ctx, _request(params={"allow_horizontal_edges": False}))
    assert ctx.offload_calls == 2
    assert len(ctx.cache.data) == 2


def test_cache_key_covers_version_algorithm_and_graph() -> None:
    body = _request()
    options = {"algorithm": "backbone", "time_limit_s": 10.0}
    key = _layout_cache_key(body, options)
    assert key.startswith("dfg_layout__")
    assert key == _layout_cache_key(_request(), options)
    assert key != _layout_cache_key(_request(algorithm="sugiyama"), options)
    assert key != _layout_cache_key(
        _request(nodes=[{"id": "A"}], edges=[], start_id=None, end_id=None), options
    )
    assert key != _layout_cache_key(body, {**options, "time_limit_s": 20.0})


def test_unwhitelisted_params_are_ignored() -> None:
    from modules.discovery.module import _layout_options

    options = _layout_options(_Config(), {"algorithm": "hack", "seed": 7, "junk": 1})
    assert "algorithm" not in options
    assert "junk" not in options
    assert options["seed"] == 7


def test_config_values_flow_into_options() -> None:
    from modules.discovery.module import _layout_options

    config = _Config(
        {
            "layout_solver_time_limit_s": 5,
            "layout_allow_horizontal_edges": False,
            "layout_lambda_sq": "2.5",
            "layout_lambda_end": "garbage",
        }
    )
    options = _layout_options(config, None)
    assert options["time_limit_s"] == 5.0
    assert options["allow_horizontal_edges"] is False
    assert options["lambda_sq"] == 2.5
    assert options["lambda_end"] == 1.0  # junk falls back to the default


def test_backbone_v2_is_accepted_and_rotates_the_cache_key(ctx: _Ctx) -> None:
    _call(ctx, _request())
    response = _call(ctx, _request(algorithm="backbone-v2"))
    assert response["algorithm"] == "backbone-v2"
    assert ctx.offload_calls == 2
    assert len(ctx.cache.data) == 2
    # The router's geometry rides along on every non-self-loop edge.
    routed = [edge for edge in response["edges"] if not edge["self_loop"]]
    assert routed and all(edge["path"].startswith("M ") for edge in routed)
    assert response["metrics"]["qm_no_overlap"] == 0.0


def test_unknown_algorithm_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValidationError):
        _request(algorithm="backbone-v3")


def test_over_edge_cap_is_413(ctx: _Ctx) -> None:
    nodes = [{"id": f"n{i}"} for i in range(90)]
    edges = [[f"n{a}", f"n{b}"] for a in range(90) for b in range(90) if a != b]
    body = _request(nodes=nodes, edges=edges[:4001], start_id=None, end_id=None)
    with pytest.raises(HTTPException) as excinfo:
        _call(ctx, body)
    assert excinfo.value.status_code == 413
