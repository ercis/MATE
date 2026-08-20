"""Backend-agnostic listing + staging for watched-folder import sources.

A watched folder points at a location in the *active* storage backend. This
module hides the local-disk vs S3 split behind two operations the scanner needs:

  - ``list_source`` - enumerate the importable files (name + fingerprint) under
    a watch's ``source_path``.
  - ``stage_source`` - copy/download one source file onto local disk so the
    normal import pipeline (which works from a staged ``original.<ext>``) can run.

``source_path`` is interpreted against the backend and used *literally* (no admin
``prefix`` prepended) so a watch can read an existing location an upstream
pipeline already fills. An empty ``source_path`` resolves to the Mate-managed
default ``users/{user_id}/watched/{watch_id}``.

Listing is recursive; ``SourceFile.name`` is the path relative to the source
root (the dedup key in ``watched_folder_files``). Non-log files (READMEs,
``.DS_Store``, …) are filtered out via :func:`detect_format`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from mate.api.config import get_settings
from mate.api.ingest.detect import detect_format
from mate.api.storage import s3
from mate.api.storage.config import is_s3


@dataclass(frozen=True)
class SourceFile:
    """One importable file discovered under a watched folder's source."""

    name: str  # path relative to the source root (the dedup key)
    size: int | None = None
    etag: str | None = None  # S3 only
    mtime: float | None = None  # local only (epoch seconds)


def default_source_path(user_id: str, watch_id: str) -> str:
    """The Mate-managed source location for a watch with no explicit path."""
    return f"users/{user_id}/watched/{watch_id}"


def _is_supported(name: str) -> bool:
    try:
        detect_format(name)
        return True
    except ValueError:
        return False


def _local_root(source_path: str) -> Path:
    """Resolve a watch ``source_path`` to a local directory.

    Absolute paths are used as-is; relative paths resolve under the data dir
    (so the managed default ``users/{uid}/watched/{id}`` lands beside the rest
    of the on-disk tree).
    """
    p = Path(source_path)
    if p.is_absolute():
        return p
    return get_settings().data_dir / source_path


def ensure_managed_dir(source_path: str) -> None:
    """Create the local source dir for a managed watch (no-op in S3 mode).

    Lets the user / pipeline drop files into the watch immediately after it's
    created. In S3 mode prefixes are implicit, so there's nothing to create.
    """
    if is_s3():
        return
    _local_root(source_path).mkdir(parents=True, exist_ok=True)


def list_source(source_path: str) -> list[SourceFile]:
    """List importable files under ``source_path`` on the active backend.

    Raises ``s3.StorageError`` (S3) or ``OSError`` (local) on an unreachable
    source - callers decide whether that's fatal (route validation) or recorded
    (poller).
    """
    if is_s3():
        prefix = source_path.strip("/")
        base = f"{prefix}/" if prefix else ""
        out: list[SourceFile] = []
        for obj in s3.list_objects(source_path):
            name = obj.key[len(base) :] if base and obj.key.startswith(base) else obj.key
            if not name or not _is_supported(name):
                continue
            out.append(SourceFile(name=name, size=obj.size, etag=obj.etag))
        return out

    root = _local_root(source_path)
    if not root.exists():
        return []
    files: list[SourceFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if not _is_supported(name):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        files.append(SourceFile(name=name, size=st.st_size, mtime=st.st_mtime))
    return files


def stage_source(source_path: str, name: str, dest: Path) -> None:
    """Copy/download one source file (``name`` relative to the source) to ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if is_s3():
        prefix = source_path.strip("/")
        key = f"{prefix}/{name}" if prefix else name
        s3.download_object(key, dest)
        return
    src = _local_root(source_path) / name
    shutil.copy2(src, dest)


__all__ = [
    "SourceFile",
    "default_source_path",
    "ensure_managed_dir",
    "list_source",
    "stage_source",
]
