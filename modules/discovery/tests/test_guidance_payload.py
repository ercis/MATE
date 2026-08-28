"""Cache-only ``guidance_payload`` tests for the discovery module.

The payload must be derived exclusively from ``ctx.cache``: AI/MCP callers
invoke it through a restricted context whose event-log accessors all raise.
The ctx stub's ``event_log`` therefore fails the test on ANY attribute access.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from modules.discovery.module import DiscoveryModule


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
        self.module_id = "discovery"
        self.user_id = "user-1"


def _dfg(n: int = 20) -> dict[str, Any]:
    """A cached DFG mirroring ``serialize_dfg``'s shape (version 3)."""
    return {
        "kind": "dfg",
        "version": 3,
        "activities": [
            {"id": f"act{i}", "label": f"act{i}", "frequency": 100 + i} for i in range(n)
        ],
        "edges": [
            {
                "id": f"act{i}__act{i + 1}",
                "source": f"act{i}",
                "target": f"act{i + 1}",
                "frequency": 200 + i,
            }
            for i in range(n)
        ],
        "start_activities": {f"s{i}": i for i in range(12)},
        "end_activities": {"act19": 42},
    }


def test_none_on_empty_cache() -> None:
    assert asyncio.run(DiscoveryModule().guidance_payload(_Ctx())) is None  # type: ignore[arg-type]


def test_payload_from_cached_dfg_and_petri() -> None:
    ctx = _Ctx()
    ctx.cache.data["dfg"] = _dfg(20)
    ctx.cache.data["petri_net_inductive"] = {
        "kind": "petri_net",
        "places": [{"id": "p0"}, {"id": "p1"}],
        "transitions": [{"id": "t0"}],
        "arcs": [{"id": "p0__t0"}, {"id": "t0__p1"}],
    }

    payload = asyncio.run(DiscoveryModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload is not None
    pm = payload["process_map"]
    assert pm["activity_count"] == 20
    assert pm["edge_count"] == 20
    # Trimmed to the top 10 activities / 15 edges, highest frequency first.
    assert len(pm["top_activities_by_frequency"]) == 10
    assert pm["top_activities_by_frequency"][0] == {"activity": "act19", "frequency": 119}
    assert len(pm["top_edges_by_frequency"]) == 15
    assert pm["top_edges_by_frequency"][0]["frequency"] == 219
    # Start-activity map trimmed to the top 10 of the 12 seeded.
    assert len(pm["start_activities"]) == 10
    assert pm["end_activities"] == {"act19": 42}
    assert payload["petri_net_inductive"] == {"places": 2, "transitions": 1, "arcs": 2}
    # Compact + JSON-serializable (this is what the guidance route ships to the LLM).
    assert len(json.dumps(payload)) < 4096


def test_petri_only_cache_still_yields_payload() -> None:
    ctx = _Ctx()
    ctx.cache.data["petri_net_inductive"] = {"places": [], "transitions": [], "arcs": []}

    payload = asyncio.run(DiscoveryModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload == {"petri_net_inductive": {"places": 0, "transitions": 0, "arcs": 0}}


def test_guidance_prompts_defined() -> None:
    assert DiscoveryModule.guidance_system_prompt
    assert DiscoveryModule.guidance_user_prefix
