"""Uploaded-module source → S3 archive (durability for S3 mode; S3_OFFLOAD.md).

An uploaded module's *source* (manifest + module.py + frontend src) lives under
``data/uploaded_modules/{mid}``; its ``.venv`` / ``.dist`` / ``.installed-hash``
are derived build artifacts the loader rebuilds. In S3 mode we archive just the
source to ``{prefix}/_system/modules/{mid}.tar.gz`` on install, so a fresh VM can
re-materialise every uploaded module (the loader then rebuilds the venv/bundle
locally). Repo-default modules under ``modules/`` are git-tracked, never archived.

Best-effort throughout: a failed archive/restore is logged, never raised - it
must not break an install, an uninstall, or boot.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
from pathlib import Path

import structlog

from mate.api.storage import s3
from mate.api.storage.config import get_storage_settings, is_s3

log = structlog.get_logger(__name__)

# Derived build artifacts excluded from the source archive - the loader rebuilds
# them per VM (venvs are ABI/machine-specific; bundles are esbuilt on load). Public
# so the module content-hash reuses the exact same set (single source of truth).
BUILD_ARTIFACT_NAMES = {
    ".venv",
    ".dist",
    "node_modules",
    ".installed-hash",
    "__pycache__",
    ".git",
    ".gradle",
}


def _archive_key(module_id: str) -> str:
    prefix = get_storage_settings().prefix.strip("/")
    rel = f"_system/modules/{module_id}.tar.gz"
    return f"{prefix}/{rel}" if prefix else rel


def _modules_prefix() -> str:
    prefix = get_storage_settings().prefix.strip("/")
    return f"{prefix}/_system/modules/" if prefix else "_system/modules/"


def _excluded(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = set(Path(tarinfo.name).parts)
    return None if parts & BUILD_ARTIFACT_NAMES else tarinfo


def archive_module_sync(folder: Path, module_id: str) -> bool:
    """Tar+gzip a module's source (sans build artifacts) and upload to S3.

    Returns True on success; no-op (False) in local mode or if the folder is
    gone. The archive root is ``module_id`` so it restores to
    ``uploaded_modules/{module_id}``.
    """
    if not is_s3() or not folder.exists():
        return False
    work = Path(tempfile.mkdtemp(prefix="ff-modbak-"))
    archive = work / f"{module_id}.tar.gz"
    try:
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(str(folder), arcname=module_id, filter=_excluded)
        s3.upload_object(archive, _archive_key(module_id))
        log.info(
            "storage.module_archive.uploaded", module_id=module_id, bytes=archive.stat().st_size
        )
        return True
    except Exception:
        log.warning("storage.module_archive.failed", module_id=module_id, exc_info=True)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def delete_module_archive_sync(module_id: str) -> None:
    """Remove a module's S3 archive (on last-owner uninstall) so a later boot
    doesn't resurrect it. No-op in local mode; best-effort."""
    if not is_s3():
        return
    try:
        s3.delete_object(_archive_key(module_id))
        log.info("storage.module_archive.deleted", module_id=module_id)
    except Exception:
        log.warning("storage.module_archive.delete_failed", module_id=module_id, exc_info=True)


def restore_missing_modules_sync(uploaded_modules_dir: Path, known_install_ids: set[str]) -> int:
    """Re-materialise *owned* uploaded modules whose source dir is missing locally.

    Lists ``_system/modules/*.tar.gz`` and, for each module that someone still
    has installed (``known_install_ids``) but whose ``uploaded_modules_dir/{mid}``
    is absent, downloads + extracts it. Returns the count restored. No-op in local
    mode. Run at boot BEFORE the loader discovers modules (and after the GC sweep),
    so a fresh VM rebuilds every owned upload's venv/bundle. Scoping to
    ``known_install_ids`` keeps it consistent with the GC sweep - the two never
    fight over a dir.
    """
    if not is_s3():
        return 0
    restored = 0
    try:
        objects = s3.list_objects(_modules_prefix())
    except s3.StorageError:
        log.warning("storage.module_archive.list_failed", exc_info=True)
        return 0
    uploaded_modules_dir.mkdir(parents=True, exist_ok=True)
    for obj in objects:
        name = obj.key.rsplit("/", 1)[-1]
        if not name.endswith(".tar.gz"):
            continue
        module_id = name[: -len(".tar.gz")]
        if module_id not in known_install_ids:
            continue  # nobody owns it - don't resurrect (GC would just remove it)
        target = uploaded_modules_dir / module_id
        if target.exists():
            continue
        if _restore_one(obj.key, module_id, uploaded_modules_dir):
            restored += 1
    if restored:
        log.info("storage.module_archive.restored", count=restored)
    return restored


def _restore_one(key: str, module_id: str, uploaded_modules_dir: Path) -> bool:
    work = Path(tempfile.mkdtemp(prefix="ff-modrestore-"))
    archive = work / f"{module_id}.tar.gz"
    dest_resolved = uploaded_modules_dir.resolve()
    try:
        s3.download_object(key, archive)
        with tarfile.open(archive, "r:gz") as tf:
            # Defend against path traversal in a tampered archive.
            for member in tf.getmembers():
                if not (uploaded_modules_dir / member.name).resolve().is_relative_to(dest_resolved):
                    raise ValueError(f"Refusing archive path traversal: {member.name!r}")
            tf.extractall(uploaded_modules_dir, filter="data")
        return True
    except Exception:
        log.warning("storage.module_archive.restore_failed", module_id=module_id, exc_info=True)
        shutil.rmtree(uploaded_modules_dir / module_id, ignore_errors=True)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
