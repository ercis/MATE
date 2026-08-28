"""Cache-only ``guidance_payload`` tests for agentsimulator.

The payload must whitelist aggregate fields of the cached ``result`` and must
never expose simulated event rows (the ``preview`` table, the distribution
arrays, or the ``download_csv*`` entries). The ctx stub's ``event_log`` fails
the test on ANY attribute access, mirroring the restricted AI/MCP context.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from modules.agentsimulator.module import RESULT_SCHEMA, AgentSimulatorModule


class _ExplodingEventLog:
    """Fails the test on ANY attribute access - guidance must be cache-only."""

    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"guidance_payload touched ctx.event_log.{name}")


class _DictCache:
    """In-memory stand-in for the platform ResultCache (get/set/exists/delete)."""

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


class _Ctx:
    """Minimal ``ModuleContext`` stand-in for guidance calls."""

    def __init__(self) -> None:
        self.cache = _DictCache()
        self.event_log = _ExplodingEventLog()
        self.object_log = None
        self.log_id = "log-1"
        self.module_id = "agentsimulator"
        self.user_id = "user-1"


def _result() -> dict[str, Any]:
    """A current-schema cached result mirroring what ``simulate`` writes."""
    return {
        "status": "ready",
        "schema": RESULT_SCHEMA,
        "generated_at": "2026-07-12T10:00:00+00:00",
        "runtime_seconds": 123.4,
        "params": {
            "num_simulations": 3,
            "mode": "orchestrated",
            "central_orchestration": True,
            "extr_delays": False,
            "determine_automatically": False,
        },
        "input": {"events": 1000, "cases": 100},
        "metrics": {
            "NGD": {"mean": 0.12, "std": 0.01, "values": [0.11, 0.12, 0.13]},
            "AEDD": {"mean": 4.2, "std": 0.3, "values": [4.0, 4.2, 4.4]},
        },
        "simulation": {"num_logs": 3, "avg_cases": 100.0, "avg_events": 990.0},
        "test": {"cases": 100, "events": 1000, "activities": 8, "resources": 5},
        "cycle_time": {"real": [1.0, 2.0], "sim": [[1.1, 2.1]]},
        "arrivals": {"real": [1.0], "sim": [[1.0]]},
        "circadian": {"real": [0] * 24, "sim": [[0] * 24]},
        "activities": {"real": {"review": 10}, "sim": [{"review": 11}]},
        "handover": {"real": {"u1->u2": 5}, "sim": [{"u1->u2": 4}]},
        "preview": {"columns": ["case_id", "activity"], "rows": [["c1", "review"]], "total": 1},
        "downloads": [{"index": 0, "cases": 100, "events": 990}],
    }


def test_none_on_empty_cache() -> None:
    assert asyncio.run(AgentSimulatorModule().guidance_payload(_Ctx())) is None  # type: ignore[arg-type]


def test_payload_whitelists_aggregates_only() -> None:
    ctx = _Ctx()
    ctx.cache.data["result"] = _result()
    ctx.cache.data["download_csv_0"] = "case_id,activity\nc1,review\n"

    payload = asyncio.run(AgentSimulatorModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload is not None
    assert payload["params"]["num_simulations"] == 3
    assert payload["input"] == {"events": 1000, "cases": 100}
    assert payload["test"]["cases"] == 100
    assert payload["simulation"]["num_logs"] == 3
    assert payload["runtime_seconds"] == 123.4
    # Fidelity keeps mean/std only - the raw per-run values are dropped.
    assert payload["fidelity"]["NGD"] == {"mean": 0.12, "std": 0.01}
    assert payload["fidelity"]["AEDD"] == {"mean": 4.2, "std": 0.3}
    # Per-run download entries reduce to counts.
    assert payload["simulated_runs"] == [{"index": 0, "cases": 100, "events": 990}]
    # The raw-row wall: no event rows, distribution arrays, or CSV text.
    for forbidden in (
        "preview",
        "cycle_time",
        "arrivals",
        "circadian",
        "activities",
        "handover",
        "downloads",
        "download_csv_0",
    ):
        assert forbidden not in payload
    assert "case_id,activity" not in json.dumps(payload)
    assert len(json.dumps(payload)) < 4096


def test_none_on_stale_schema() -> None:
    ctx = _Ctx()
    stale = _result()
    stale["schema"] = RESULT_SCHEMA - 1
    ctx.cache.data["result"] = stale

    assert asyncio.run(AgentSimulatorModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_none_on_partial_result() -> None:
    ctx = _Ctx()
    partial = _result()
    del partial["handover"]  # missing distribution key == stale/partial cache
    ctx.cache.data["result"] = partial

    assert asyncio.run(AgentSimulatorModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_guidance_prompts_defined() -> None:
    assert AgentSimulatorModule.guidance_system_prompt
    assert AgentSimulatorModule.guidance_user_prefix
