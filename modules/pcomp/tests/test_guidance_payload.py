"""Cache-only ``guidance_payload`` tests for pcomp.

Test results are cached as ``permutation__{other_log_id}`` /
``bootstrap__{other_log_id}``, so the payload lists keys by peeking at the
cache directory - the stub cache is file-backed (like the platform
ResultCache) to exercise that path. The ctx stub's ``event_log`` fails the
test on ANY attribute access, mirroring the restricted AI/MCP context.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from modules.pcomp.module import PcompModule


class _ExplodingEventLog:
    """Fails the test on ANY attribute access - guidance must be cache-only."""

    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"guidance_payload touched ctx.event_log.{name}")


class _FileCache:
    """File-backed stand-in mirroring the platform ResultCache: one
    ``{key}.json`` per entry under ``self.dir``."""

    def __init__(self, root: Path) -> None:
        self.dir = root

    async def get(self, key: str) -> Any:
        path = self.dir / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    async def set(self, key: str, value: Any) -> None:
        (self.dir / f"{key}.json").write_text(json.dumps(value, default=str))

    async def exists(self, key: str) -> bool:
        return (self.dir / f"{key}.json").exists()

    async def delete(self, key: str) -> None:
        (self.dir / f"{key}.json").unlink(missing_ok=True)


class _DirlessCache:
    """A cache without a ``dir`` - key listing must degrade to no entries."""

    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return False

    async def delete(self, key: str) -> None:
        return None


class _Ctx:
    """Minimal ``ModuleContext`` stand-in for guidance calls."""

    def __init__(self, cache: Any) -> None:
        self.cache = cache
        self.event_log = _ExplodingEventLog()
        self.object_log = None
        self.log_id = "log-a"
        self.module_id = "pcomp"
        self.user_id = "user-1"

    async def open_event_log(self, log_id: str) -> Any:
        raise AssertionError("guidance_payload tried to open another event log")


def _permutation_result(other: str, pvalue: float) -> dict[str, Any]:
    """Mirrors what ``permutation_test`` caches under ``permutation__{other}``."""
    return {
        "kind": "pcomp_permutation_test",
        "pvalue": pvalue,
        "baseline_log_id": "log-a",
        "other_log_id": other,
        "distribution_size": 1000,
        "seed": 42,
        "weighted_time_cost": True,
    }


def _bootstrap_result(other: str, pvalue: float) -> dict[str, Any]:
    """Mirrors what ``bootstrap_test`` caches under ``bootstrap__{other}``."""
    return {
        "kind": "pcomp_bootstrap_test",
        "pvalue": pvalue,
        "baseline_log_id": "log-a",
        "other_log_id": other,
        "bootstrapping_dist_size": 1000,
        "resample_size": 1.0,
        "seed": 42,
    }


def _seed(root: Path, key: str, value: dict[str, Any], *, age_s: float = 0.0) -> None:
    path = root / f"{key}.json"
    path.write_text(json.dumps(value))
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


def test_none_on_empty_cache(tmp_path: Path) -> None:
    ctx = _Ctx(_FileCache(tmp_path))
    assert asyncio.run(PcompModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_none_when_cache_exposes_no_dir() -> None:
    ctx = _Ctx(_DirlessCache())
    assert asyncio.run(PcompModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_payload_from_cached_tests(tmp_path: Path) -> None:
    _seed(tmp_path, "permutation__log-b", _permutation_result("log-b", 0.02))
    _seed(tmp_path, "bootstrap__log-b", _bootstrap_result("log-b", 0.31))

    ctx = _Ctx(_FileCache(tmp_path))
    payload = asyncio.run(PcompModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload is not None
    assert payload["permutation_tests"] == [_permutation_result("log-b", 0.02)]
    assert payload["bootstrap_tests"] == [_bootstrap_result("log-b", 0.31)]
    assert len(json.dumps(payload)) < 4096


def test_trims_to_five_newest_per_test_kind(tmp_path: Path) -> None:
    for i in range(7):
        # log-0 is the oldest, log-6 the newest.
        _seed(
            tmp_path,
            f"permutation__log-{i}",
            _permutation_result(f"log-{i}", 0.1 * i),
            age_s=(7 - i) * 60.0,
        )

    ctx = _Ctx(_FileCache(tmp_path))
    payload = asyncio.run(PcompModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload is not None
    entries = payload["permutation_tests"]
    assert len(entries) == 5
    assert entries[0]["other_log_id"] == "log-6"  # newest first
    assert {e["other_log_id"] for e in entries} == {f"log-{i}" for i in range(2, 7)}
    assert payload["bootstrap_tests"] == []


def test_guidance_prompts_defined() -> None:
    assert PcompModule.guidance_system_prompt
    assert PcompModule.guidance_user_prefix
