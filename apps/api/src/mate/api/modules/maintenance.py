"""Uploaded-module disk GC (S3_OFFLOAD.md Phase 2.4).

An uploaded module's ``.venv`` (100 MB-2 GB) + ``.dist`` + source live under
``data/uploaded_modules/{mid}``. The uninstall route removes the dir when the
last owner uninstalls, but a crash mid-uninstall (or a restored-but-unowned dir)
can leak it. This boot-time sweep removes any uploaded-module dir with zero
install rows, reclaiming the venv + bundle + source.

Boot-only by design: it must not race a concurrent upload (which extracts to the
dir before recording its install row). Repo-default modules live under
``modules/`` and are never touched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def gc_orphaned_uploaded_modules(
    uploaded_modules_dir: Path, known_install_ids: set[str]
) -> list[str]:
    """Remove uploaded-module dirs not referenced by any install row.

    ``known_install_ids`` is the set of module ids with at least one
    ``module_installs`` row. Returns the ids removed. Safe to call when the dir
    doesn't exist yet (returns ``[]``).
    """
    if not uploaded_modules_dir.exists():
        return []
    removed: list[str] = []
    for child in sorted(uploaded_modules_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in known_install_ids:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
    if removed:
        log.info("modules.gc.removed_orphans", ids=removed)
    return removed
