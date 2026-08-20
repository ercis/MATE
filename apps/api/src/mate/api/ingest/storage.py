"""On-disk layout for an imported event log (per-user; INSTRUCTIONS.md §3.2).

data/users/{user_id}/event_logs/{log_id}/
├── meta.json          # source format, ingest stats, detected schema, mapping
├── events.parquet     # case-centric: flat event table, sorted by (case_id, timestamp)
├── cases.parquet      # case-centric: cached case-level aggregates
├── original.{ext}     # original upload (audit / re-export)
└── ocel/              # object-centric (OCEL) tables - present iff log_model
    ├── events.parquet     #   == "object_centric". The case-centric root
    ├── objects.parquet     #   events.parquet/cases.parquet are then ABSENT
    ├── relations.parquet   #   (the two models never share storage).
    └── o2o.parquet
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mate.api.config import get_settings


@dataclass(frozen=True)
class LogPaths:
    root: Path
    meta: Path
    events: Path
    cases: Path
    ocel_dir: Path
    # Object-centric (OCEL) tables under ocel_dir. Only written when
    # log_model == "object_centric"; the case-centric events/cases above are
    # then absent.
    ocel_events: Path
    ocel_objects: Path
    ocel_relations: Path
    ocel_o2o: Path

    def exists(self) -> bool:
        return self.root.exists()

    def original_for(self, ext: str) -> Path:
        ext = ext.lstrip(".")
        return self.root / f"original.{ext}"

    def find_original(self) -> Path | None:
        """Locate the retained upload regardless of its extension.

        Used for re-import where the on-disk extension may differ from the
        canonical source_format (e.g. an OCEL log's source_format is "ocel" but
        the file is stored as original.jsonocel/.xmlocel/.sqlite so pm4py can
        pick its reader)."""
        if not self.root.exists():
            return None
        for p in sorted(self.root.glob("original.*")):
            return p
        return None

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.ocel_dir.mkdir(parents=True, exist_ok=True)

    def write_meta(self, meta: dict[str, Any]) -> None:
        self.meta.write_text(json.dumps(meta, indent=2, default=str))

    def read_meta(self) -> dict[str, Any] | None:
        if not self.meta.exists():
            return None
        return json.loads(self.meta.read_text())

    def remove(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)


def log_paths(log_id: str, user_id: str) -> LogPaths:
    root = get_settings().event_logs_dir_for(user_id) / log_id
    ocel_dir = root / "ocel"
    return LogPaths(
        root=root,
        meta=root / "meta.json",
        events=root / "events.parquet",
        cases=root / "cases.parquet",
        ocel_dir=ocel_dir,
        ocel_events=ocel_dir / "events.parquet",
        ocel_objects=ocel_dir / "objects.parquet",
        ocel_relations=ocel_dir / "relations.parquet",
        ocel_o2o=ocel_dir / "o2o.parquet",
    )
