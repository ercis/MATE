"""``guidance_payload`` tests for ocel_discovery.

The module computes every view per-request and never writes to ``ctx.cache``,
so its guidance payload is always ``None`` - and it must not try to compute
anything either: the stubs below fail the test on ANY event-log or object-log
attribute access (mirroring the restricted AI/MCP context, which walls both).
"""

from __future__ import annotations

import asyncio
from typing import Any

from modules.ocel_discovery.module import OcelDiscoveryModule


class _ExplodingLog:
    """Fails the test on ANY attribute access - guidance must be cache-only."""

    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"guidance_payload touched a walled log accessor (.{name})")


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


class _GuidanceCtx:
    """Minimal ``ModuleContext`` stand-in for guidance calls."""

    def __init__(self) -> None:
        self.cache = _DictCache()
        self.event_log = _ExplodingLog()
        self.object_log = _ExplodingLog()
        self.log_id = "test-log"
        self.module_id = "ocel_discovery"
        self.user_id = "test-user"


def test_none_on_empty_cache_without_touching_logs() -> None:
    ctx = _GuidanceCtx()
    assert asyncio.run(OcelDiscoveryModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_none_even_with_unrelated_cache_entries() -> None:
    # The module never caches anything itself, so whatever is in the cache
    # namespace is not a discovery artifact - the payload stays None.
    ctx = _GuidanceCtx()
    ctx.cache.data["summary"] = {"object_types": [{"type": "order", "count": 3}]}

    assert asyncio.run(OcelDiscoveryModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_guidance_prompts_defined() -> None:
    assert OcelDiscoveryModule.guidance_system_prompt
    assert OcelDiscoveryModule.guidance_user_prefix
