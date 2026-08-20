"""Per-(user_id, log_id, module_id) result cache (§5.5).

Stores under ``data/users/{user_id}/module_results/{log_id}/{module_id}/{key}.{ext}``.
We serialise dicts/lists/scalars to JSON; pandas DataFrames to Parquet; bytes
verbatim. Module authors decide what to cache; the SDK only mediates the
filesystem layout.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from mate.api.config import get_settings
from mate.api.storage import sync as storage_sync


class ResultCache:
    def __init__(
        self,
        log_id: str,
        module_id: str,
        user_id: str,
        root: Path | None = None,
        variant: str | None = None,
    ) -> None:
        base = root or get_settings().module_results_dir_for(user_id)
        self.dir = base / log_id / module_id
        # ``variant`` isolates an ephemeral dataset view (a dashboard's
        # column/time filter) into its own sub-namespace. Without it a
        # filtered request would collide with - and serve - the unfiltered
        # cache, since the freshness check only watches the (unchanged)
        # parquet mtime. ``None`` keeps the canonical, committed-filter cache
        # at the module root, and ``clear()``'s rmtree still wipes variants.
        if variant:
            self.dir = self.dir / f"_v_{variant}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _candidate(self, key: str) -> Path:
        if "/" in key or ".." in key:
            raise ValueError(f"Invalid cache key {key!r} - must be a flat name.")
        return self.dir / key

    async def get(self, key: str) -> Any:
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> Any:
        # In S3 mode, pull this module's cached outputs down if the local cache
        # is cold (once per dir per process). No-op in local mode / warm cache.
        storage_sync.hydrate_dir_sync(self.dir)
        for ext in ("json", "parquet", "bin"):
            path = self._candidate(f"{key}.{ext}")
            if not path.exists():
                continue
            if ext == "json":
                return json.loads(path.read_text())
            if ext == "parquet":
                import pandas as pd

                return pd.read_parquet(path)
            if ext == "bin":
                return path.read_bytes()
        return None

    async def set(self, key: str, value: Any) -> None:
        await asyncio.to_thread(self._set_sync, key, value)
        # Persist the (now-updated) outputs dir to the S3 primary store.
        await asyncio.to_thread(storage_sync.persist_dir_sync, self.dir)

    def _set_sync(self, key: str, value: Any) -> None:
        # Pick the encoding by type.
        try:
            import pandas as pd
        except ModuleNotFoundError:  # pragma: no cover - pandas is a platform dep
            pd = None  # type: ignore[assignment]

        if pd is not None and hasattr(pd, "DataFrame") and isinstance(value, pd.DataFrame):
            path = self._candidate(f"{key}.parquet")
            value.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
            return
        if isinstance(value, (bytes, bytearray)):
            path = self._candidate(f"{key}.bin")
            path.write_bytes(bytes(value))
            return
        path = self._candidate(f"{key}.json")
        path.write_text(json.dumps(value, default=str))

    async def exists(self, key: str) -> bool:
        return any(self._candidate(f"{key}.{ext}").exists() for ext in ("json", "parquet", "bin"))

    async def delete(self, key: str) -> None:
        for ext in ("json", "parquet", "bin"):
            path = self._candidate(f"{key}.{ext}")
            if path.exists():
                path.unlink()

    def clear(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir)
            self.dir.mkdir(parents=True, exist_ok=True)
        # Drop the mirrored outputs from S3 too, so a cold cache doesn't
        # re-hydrate stale results (no-op in local mode).
        storage_sync.delete_dir_remote_sync(self.dir)
