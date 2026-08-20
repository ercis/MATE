"""Module install pipelines (§7.6.2).

Three sources, each implemented as a `@job`-style handler on the platform's
`JobRuntime` so progress flows to the bottom-left dock and the jobs drawer
just like an event-log import:

- ``module.install.upload`` - operator uploaded a zip / tar.gz from the UI
  (or `POST /api/v1/modules/install`). The route writes the bytes to a
  staging tmpdir and submits a job carrying the file path.
- ``module.install.git`` - clone a git URL (optionally pinned to a ref).
- ``module.install.registry`` - install from PyPI by name. After
  ``uv pip install`` it resolves the new module via its `mate.modules`
  entry point. The ``source: "npm"`` variant raises ``NotImplementedError``
  by design: an npm-only package ships no Python entry point for the loader
  to bind to, so there is nothing to mount in-process.

Every handler ends with `loader.load_one(folder, manifest)` so the module
becomes available without a restart.
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
from mate.api.modules.installs import module_owned_by_other, record_install
from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest

if TYPE_CHECKING:
    from mate.api.modules.loader import ModuleLoader

log = structlog.get_logger(__name__)


JOB_TYPE_UPLOAD = "module.install.upload"
JOB_TYPE_GIT = "module.install.git"
JOB_TYPE_REGISTRY = "module.install.registry"


async def _record_owner(user_id: str, module_id: str, source: str) -> None:
    """Mark *module_id* as installed for the user who ran the install job, so
    it shows up in their (per-user) module list and only they can uninstall it.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        await record_install(session, user_id, module_id, source)
        await session.commit()


def register_module_install_handlers(runtime: JobRuntime, loader: ModuleLoader) -> None:
    """Wire the three install job types onto the runtime.

    Called from `main.py` lifespan after the loader and runtime are built.
    Idempotent - re-registration of the same type would raise, so we skip
    silently if a type is already registered (helpful for hot-reload tests).
    """
    for type_, handler in (
        (JOB_TYPE_UPLOAD, _install_from_upload(loader)),
        (JOB_TYPE_GIT, _install_from_git(loader)),
        (JOB_TYPE_REGISTRY, _install_from_registry(loader)),
    ):
        if type_ in runtime._handlers:  # type: ignore[attr-defined]
            continue
        runtime.register(type_, handler)


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
                folder, manifest = await _stage_validated_upload(loader, handle.user_id, staging)
                await handle.progress(60, 100, stage="installing", message="Resolving dependencies")
                await loader.load_one(folder, manifest)
                await _record_owner(handle.user_id, manifest.id, "upload")
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


def _install_from_git(loader: ModuleLoader):
    async def handler(handle: JobHandle) -> None:
        url: str = handle.payload["url"]
        ref: str | None = handle.payload.get("ref")
        await handle.progress(5, 100, stage="cloning", message=f"Cloning {url}")
        staging = Path(tempfile.mkdtemp(prefix="ff-install-git-"))
        try:
            cmd = ["git", "clone", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [url, str(staging)]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"git clone failed (exit {proc.returncode}): "
                    f"{out.decode('utf-8', errors='replace')[:500]}"
                )
            # The clone leaves a .git directory we don't want - strip it.
            shutil.rmtree(staging / ".git", ignore_errors=True)
            await handle.progress(35, 100, stage="validating", message="Validating manifest")
            folder, manifest = await _stage_validated_upload(loader, handle.user_id, staging)
            await handle.progress(60, 100, stage="installing", message="Resolving dependencies")
            await loader.load_one(folder, manifest)
            await _record_owner(handle.user_id, manifest.id, "git")
            await handle.progress(100, 100, stage="ready", message="Module installed")
            handle.payload["module_id"] = manifest.id
            await handle.bus.publish(
                "module.installed",
                {
                    "id": manifest.id,
                    "source": "git",
                    "url": url,
                    "ref": ref,
                    "user_id": handle.user_id,
                },
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    return handler


def _install_from_registry(loader: ModuleLoader):
    async def handler(handle: JobHandle) -> None:
        source: str = handle.payload["source"]
        pkg: str = handle.payload["id"]
        version: str | None = handle.payload.get("version")

        if source != "pypi":
            # The npm path would mean a JS-only module without a Python
            # entry point - the loader has no place to bind it today, so
            # surface that clearly rather than silently no-op.
            raise NotImplementedError(
                f"Installing from {source!r} is not supported yet. Use PyPI or the Upload / Git tabs."
            )

        spec = f"{pkg}=={version}" if version else pkg
        await handle.progress(5, 100, stage="installing", message=f"pip install {spec}")
        rc, out = await _run(["uv", "pip", "install", "--no-cache", spec])
        if rc != 0:
            raise RuntimeError(f"uv pip install failed (exit {rc}): {out[:800]}")

        await handle.progress(60, 100, stage="discovering", message="Scanning entry points")
        # Re-scan for the new entry point. We can't ask `discover_entry_points()`
        # naively because importlib.metadata caches its view of the installed
        # set per-process; clear the cache so the new package shows up.
        from importlib import metadata as importlib_metadata

        from mate.api.modules.discovery import (
            ENTRY_POINT_GROUP,
            discover_entry_points,
        )

        # In Python 3.12+ entry_points() builds from a freshly-read cache on
        # every call, but the underlying Distribution objects are memoised.
        # Force a fresh read by clearing the modulewide _ep_cache attribute
        # if present (best-effort; the public API is stable across calls).
        cache = getattr(importlib_metadata, "_ep_cache", None)
        if cache is not None:
            cache.clear()

        candidates = discover_entry_points()
        # Pick the entry whose installed Distribution name matches our spec.
        new_modules = [d for d in candidates if _matches_pkg(d, pkg)]
        if not new_modules:
            raise RuntimeError(
                f"Installed {spec!r} but it does not declare a {ENTRY_POINT_GROUP!r} "
                "entry point pointing to a package with a manifest.yaml."
            )

        await handle.progress(80, 100, stage="loading", message="Mounting module")
        loaded_ids: list[str] = []
        for d in new_modules:
            await loader.load_one(d.folder, d.manifest)
            await _record_owner(handle.user_id, d.id, "registry")
            loaded_ids.append(d.id)

        await handle.progress(100, 100, stage="ready", message="Module installed")
        handle.payload["module_id"] = loaded_ids[0] if len(loaded_ids) == 1 else None
        handle.payload["module_ids"] = loaded_ids
        await handle.bus.publish(
            "module.installed",
            {
                "id": loaded_ids[0] if loaded_ids else None,
                "ids": loaded_ids,
                "source": "pypi",
                "package": pkg,
                "user_id": handle.user_id,
            },
        )

    return handler


def _matches_pkg(d, pkg: str) -> bool:
    """Cheap heuristic: an entry point installed by `pip install foo-bar`
    typically lives under a package named `foo_bar` or `foo-bar`. Normalise
    both sides to match either form.
    """
    norm = pkg.replace("-", "_").lower()
    # We don't have the distribution name on DiscoveredModule, so compare the
    # folder name and the manifest id against the requested pkg.
    candidates = {d.id.lower(), d.folder.name.lower()}
    return any(c.replace("-", "_") == norm for c in candidates)


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


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
    loader: ModuleLoader, user_id: str, staging: Path
) -> tuple[Path, Manifest]:
    """Validate a staged upload, then move it into the uploads root.

    Rejects (before touching disk) an id that collides with a built-in default
    - uploads must never overwrite repo code - or one already owned by another
    user, since module code is shared in-process under a single id. Re-uploading
    an id the same user already owns replaces it (hot-reload).
    """
    inner, manifest = _read_staged_manifest(staging)
    if manifest.id in loader.default_module_ids:
        raise ModuleManifestError(
            f"Module id {manifest.id!r} is a built-in default module and cannot be "
            "overwritten by an upload. Choose a different id."
        )
    sm = get_sessionmaker()
    async with sm() as session:
        if await module_owned_by_other(session, user_id, manifest.id):
            raise ModuleManifestError(
                f"Module id {manifest.id!r} is already in use by another user. Choose a unique id."
            )
    target = loader.uploaded_modules_dir / manifest.id
    if target.exists():
        # Replace prior install - keeps the operator's expectations simple
        # ("re-uploading the same id updates it"). The loader will hot-reload.
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(inner), str(target))
    return target, manifest


def _resolve_archive_root(staging: Path) -> Path:
    """If the archive contained one wrapper directory, descend into it."""
    # `__MACOSX` is metadata cruft macOS adds to zips alongside the real folder.
    entries = [p for p in staging.iterdir() if not p.name.startswith(".") and p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir() and not (staging / "manifest.yaml").exists():
        return entries[0]
    return staging
