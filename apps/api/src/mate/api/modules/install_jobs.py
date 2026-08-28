"""Module install pipeline (§7.6.2).

Modules are installed from an uploaded archive only, implemented as a
`@job`-style handler on the platform's `JobRuntime` so progress flows to the
bottom-left dock and the jobs drawer just like an event-log import:

- ``module.install.upload`` - operator uploaded a zip / tar.gz from the UI
  (`POST /api/v1/modules/install`). The route writes the bytes to a staging
  tmpdir and submits a job carrying the file path.

The handler normally ends with `loader.load_one(folder, manifest)` so the module
becomes available without a restart. When another user already owns a
byte-identical module under the same id, it instead reuses the loaded code and
just records this user's ownership (content-addressed sharing - see
`_stage_validated_upload`).
"""

from __future__ import annotations

import asyncio
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from mate.api.db.engine import get_sessionmaker
from mate.api.jobs.runtime import JobHandle, JobRuntime
from mate.api.modules.content_hash import (
    module_content_hash,
    read_content_hash,
    write_content_hash,
)
from mate.api.modules.installs import module_owned_by_other, record_install
from mate.api.storage.module_archive import archive_module_sync
from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest

if TYPE_CHECKING:
    from mate.api.modules.loader import ModuleLoader

log = structlog.get_logger(__name__)


JOB_TYPE_UPLOAD = "module.install.upload"


async def _record_owner(user_id: str, module_id: str, source: str) -> None:
    """Mark *module_id* as installed for the user who ran the install job, so
    it shows up in their (per-user) module list and only they can uninstall it.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        await record_install(session, user_id, module_id, source)
        await session.commit()


def register_module_install_handlers(runtime: JobRuntime, loader: ModuleLoader) -> None:
    """Wire the module-install job type onto the runtime.

    Called from `main.py` lifespan after the loader and runtime are built.
    Idempotent - re-registration of the same type would raise, so we skip
    silently if it is already registered (helpful for hot-reload tests).
    """
    if JOB_TYPE_UPLOAD in runtime._handlers:  # type: ignore[attr-defined]
        return
    runtime.register(JOB_TYPE_UPLOAD, _install_from_upload(loader))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _install_from_upload(loader: ModuleLoader):
    async def handler(handle: JobHandle) -> None:
        archive_path = Path(handle.payload["archive_path"])
        original_name = handle.payload.get("original_name", archive_path.name)
        try:
            await handle.progress(5, 100, stage="extracting", message=f"Extracting {original_name}")
            staging = Path(tempfile.mkdtemp(prefix="ff-install-"))
            try:
                await asyncio.to_thread(_extract_archive, archive_path, staging)
                await handle.progress(35, 100, stage="validating", message="Validating manifest")
                # Read the manifest up front so we can serialise the whole
                # stage->load->record section behind this id's lock (two uploads
                # of the same new id would otherwise race the folder/venv/mount).
                inner, manifest = _read_staged_manifest(staging)
                lock = await loader.id_lock(manifest.id)
                async with lock:
                    folder, reused = await _stage_validated_upload(
                        loader, handle.user_id, inner, manifest
                    )
                    await handle.progress(
                        60, 100, stage="installing", message="Resolving dependencies"
                    )
                    # `reused` means the identical module is already loaded (and
                    # already archived) under this id by another owner - skip the
                    # disruptive reload + re-archive and only grant ownership.
                    if not reused:
                        await loader.load_one(folder, manifest)
                    await _record_owner(handle.user_id, manifest.id, "upload")
                    if not reused:
                        # S3 mode: archive the source so a fresh VM can
                        # re-materialise it (best-effort + local-mode no-op).
                        await asyncio.to_thread(archive_module_sync, folder, manifest.id)
                await handle.progress(100, 100, stage="ready", message="Module installed")
                handle.payload["module_id"] = manifest.id
                await handle.bus.publish(
                    "module.installed",
                    {"id": manifest.id, "source": "upload", "user_id": handle.user_id},
                )
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        finally:
            # Always clean up the upload temp file regardless of outcome.
            archive_path.unlink(missing_ok=True)

    return handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_archive(archive_path: Path, dest: Path) -> None:
    suffix = "".join(archive_path.suffixes[-2:]).lower()
    if archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extract(zf, dest)
        return
    if suffix in (".tar.gz", ".tgz") or archive_path.suffix.lower() in (".tar",):
        mode = "r:gz" if suffix in (".tar.gz", ".tgz") else "r"
        with tarfile.open(archive_path, mode=mode) as tf:
            _safe_extract_tar(tf, dest)
        return
    raise ValueError(
        f"Unsupported archive format {archive_path.name!r} - accept .zip, .tar, .tar.gz, .tgz."
    )


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for name in zf.namelist():
        target = (dest / name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError(f"Refusing zip path traversal: {name!r}") from exc
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError(f"Refusing tar path traversal: {member.name!r}") from exc
    # `filter="data"` strips device files, absolute paths, and the like -
    # available since Python 3.12, which is our minimum.
    tf.extractall(dest, filter="data")


def _read_staged_manifest(staging: Path) -> tuple[Path, Manifest]:
    """Locate + parse the manifest in a staged extraction *without* moving it.

    Many archives wrap their content in a single top-level folder (e.g.
    GitHub's tarball auto-names with a SHA). We unwrap one level if there's a
    single child directory containing the manifest.
    """
    inner = _resolve_archive_root(staging)
    manifest_path = inner / "manifest.yaml"
    if not manifest_path.exists():
        raise ModuleManifestError(
            f"Archive is missing manifest.yaml at the top level (looked in {inner})."
        )
    return inner, Manifest.load_yaml(manifest_path)


async def _stage_validated_upload(
    loader: ModuleLoader, user_id: str, inner: Path, manifest: Manifest
) -> tuple[Path, bool]:
    """Validate a staged upload and decide reuse vs (re)stage.

    Returns ``(folder, reused)``. ``reused=True`` means the byte-identical module
    is already loaded under this id by another owner - the caller shares it (skips
    ``load_one`` + archiving) and only records this user's ownership row. This is
    content-addressed sharing: module code is shared in-process under a single id,
    so two users can co-own the *same* code, but two *different* code bodies can't
    coexist under one id.

    Rejections (before mutating disk):
    - an id that collides with a built-in default (uploads must never shadow repo
      code);
    - an id owned by another user whose on-disk content *differs* from this upload
      (or can't be verified) - the uploader must rename. Once an id is co-owned,
      this also blocks the original owner from changing its code out from under the
      co-owner (they must have the other owner(s) uninstall first).

    Re-uploading an id the same user solely owns replaces it (hot-reload).
    """
    if manifest.id in loader.default_module_ids:
        raise ModuleManifestError(
            f"Module id {manifest.id!r} is a built-in default module and cannot be "
            "overwritten by an upload. Choose a different id."
        )
    target = loader.uploaded_modules_dir / manifest.id
    # Hash the *pristine* staging tree, before any move/install synthesises a
    # pyproject.toml into it - the persisted sidecar is pristine too, so every
    # comparison is pristine-vs-pristine.
    staged_hash = await asyncio.to_thread(module_content_hash, inner)

    sm = get_sessionmaker()
    async with sm() as session:
        owned_by_other = await module_owned_by_other(session, user_id, manifest.id)

    if owned_by_other:
        existing_hash = read_content_hash(target)
        if existing_hash is None or existing_hash != staged_hash:
            raise ModuleManifestError(
                f"Module id {manifest.id!r} is already used by a different module owned by "
                "another user. Shared module code can't diverge under one id - rename yours "
                "(change `id` in manifest.yaml), or have the other owner(s) uninstall it first."
            )
        # Verified-identical content owned by someone else.
        if manifest.id in loader.loaded:
            return target, True  # already loaded - just share it (caller records owner)
        # Identical but not currently loaded (failed import, or missing after an
        # S3 restore): materialise this user's copy to repair the shared install.
        _restage(inner, target, staged_hash)
        return target, False

    # Not owned by another user: a brand-new id, or this user re-uploading their
    # own module (hot-reload). Replace whatever is there with the fresh upload.
    _restage(inner, target, staged_hash)
    return target, False


def _restage(inner: Path, target: Path, content_hash: str) -> None:
    """Move a validated staged module into the uploads root, replacing any prior
    copy, and persist its pristine content hash alongside for later comparisons."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(inner), str(target))
    write_content_hash(target, content_hash)


def _resolve_archive_root(staging: Path) -> Path:
    """If the archive contained one wrapper directory, descend into it."""
    # `__MACOSX` is metadata cruft macOS adds to zips alongside the real folder.
    entries = [p for p in staging.iterdir() if not p.name.startswith(".") and p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir() and not (staging / "manifest.yaml").exists():
        return entries[0]
    return staging
