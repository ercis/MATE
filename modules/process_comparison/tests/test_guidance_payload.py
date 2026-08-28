"""Cache-only ``guidance_payload`` tests for process_comparison.

Comparison results are cached under ``{view}__{digest}`` keys, so the payload
resolves the newest entry per view by peeking at the cache directory - the
stub cache here is file-backed (like the platform ResultCache) to exercise
that path. The ctx stub's ``event_log`` fails the test on ANY attribute
access, mirroring the restricted AI/MCP context.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from modules.process_comparison.module import ProcessComparisonModule


class _ExplodingEventLog:
    """Fails the test on ANY attribute access - guidance must be cache-only."""

    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"guidance_payload touched ctx.event_log.{name}")


class _FileCache:
    """File-backed stand-in mirroring the platform ResultCache: one
    ``{key}.json`` per entry under ``self.dir`` (guidance peeks at the
    directory to list keys)."""

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
        self.module_id = "process_comparison"
        self.user_id = "user-1"

    async def open_event_log(self, log_id: str, filters: list[dict[str, Any]] | None = None) -> Any:
        raise AssertionError("guidance_payload tried to open another event log")


def _seed(root: Path, key: str, value: dict[str, Any], *, age_s: float = 0.0) -> None:
    path = root / f"{key}.json"
    path.write_text(json.dumps(value))
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


def test_none_on_empty_cache(tmp_path: Path) -> None:
    ctx = _Ctx(_FileCache(tmp_path))
    assert asyncio.run(ProcessComparisonModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_none_when_cache_exposes_no_dir() -> None:
    ctx = _Ctx(_DirlessCache())
    assert asyncio.run(ProcessComparisonModule().guidance_payload(ctx)) is None  # type: ignore[arg-type]


def test_payload_from_cached_comparisons(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "similarity__aaaa",
        {
            "kind": "similarity",
            "log_ids": ["log-a", "log-b"],
            "metrics": {
                "emd": [[0.0, 0.4], [0.4, 0.0]],
                "activity_overlap": [[1.0, 0.8], [0.8, 1.0]],
            },
        },
    )
    _seed(
        tmp_path,
        "summary__aaaa",
        {
            "kind": "summary_delta",
            "baseline_log_id": "log-a",
            "other_log_id": "log-b",
            "kpis": [{"key": "cases", "value_a": 4, "value_b": 5, "delta": 1}],
        },
    )
    _seed(
        tmp_path,
        "activity-deltas__aaaa",
        {
            "kind": "activity_deltas",
            "log_ids": ["log-a", "log-b"],
            "activities": [
                {"activity": f"a{i}", "frequencies": [i, i + 1], "freq_shares": [0.1, 0.2]}
                for i in range(12)
            ],
        },
    )

    ctx = _Ctx(_FileCache(tmp_path))
    payload = asyncio.run(ProcessComparisonModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload is not None
    assert payload["similarity"]["log_ids"] == ["log-a", "log-b"]
    assert payload["similarity"]["metrics"]["emd"][0][1] == 0.4
    assert payload["summary_delta"]["baseline_log_id"] == "log-a"
    assert payload["summary_delta"]["other_log_id"] == "log-b"
    assert payload["summary_delta"]["kpis"][0]["key"] == "cases"
    # Activity rows trimmed to the top 10 of the 12 cached.
    assert len(payload["top_activity_deltas"]["activities"]) == 10
    assert payload["top_activity_deltas"]["log_ids"] == ["log-a", "log-b"]
    assert len(json.dumps(payload)) < 4096


def test_latest_entry_per_view_wins(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "similarity__old",
        {"kind": "similarity", "log_ids": ["log-a", "log-old"], "metrics": {}},
        age_s=100.0,
    )
    _seed(
        tmp_path,
        "similarity__new",
        {"kind": "similarity", "log_ids": ["log-a", "log-new"], "metrics": {}},
    )

    ctx = _Ctx(_FileCache(tmp_path))
    payload = asyncio.run(ProcessComparisonModule().guidance_payload(ctx))  # type: ignore[arg-type]

    assert payload is not None
    assert payload["similarity"]["log_ids"] == ["log-a", "log-new"]
    # Views that were never computed are simply absent.
    assert "summary_delta" not in payload
    assert "top_activity_deltas" not in payload


def test_guidance_prompts_defined() -> None:
    assert ProcessComparisonModule.guidance_system_prompt
    assert ProcessComparisonModule.guidance_user_prefix
