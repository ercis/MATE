"""Filesystem watchdog for `modules/` - dev-only hot reload (§5.3 #7).

Watches every `modules/<id>/manifest.yaml`, `module.py`, and `.dist/` for
changes; on debounced change, calls `ModuleLoader.load_one()` for the
affected module so authors don't need to restart the API to see edits.

Gated on `settings.env == "dev"` because:
  - Production must boot from a known module set; surprise reloads on disk
    twitches would make incidents harder to debug.
  - The 500 ms debounce can't cover all editor save patterns; manual restart
    remains the only deterministic option for prod.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from mate.api.modules.loader import ModuleLoader
from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)


_DEBOUNCE_S = 0.5

# A reload re-installs the venv and re-imports `module.py`, which writes build
# artifacts + bytecode back under `modules/<id>/`. If the watcher reacts to
# those, it schedules another reload, whose own writes trigger the next - an
# infinite loop (seen in the wild when a venv skip-check keeps failing and the
# installer rewrites `.installed-hash` every pass). Match anywhere in the path,
# not just depth 1: a submodule's `__pycache__` lives deeper than `parts[1]`.
_IGNORED_DIR_PARTS = frozenset(
    {".venv", ".dist", "node_modules", "__pycache__", ".git", ".gradle", "build"}
)
_IGNORED_NAMES = frozenset({".installed-hash", "pyproject.toml", ".DS_Store"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _is_ignored(relative: Path) -> bool:
    """Whether a changed path (relative to ``modules/``) is a build artifact we
    must NOT reload on. ``relative.parts[0]`` is the module id; scan the rest for
    an artifact dir, name, or bytecode suffix. See the module docstring for the
    loop this prevents."""
    return (
        any(part in _IGNORED_DIR_PARTS for part in relative.parts[1:])
        or relative.name in _IGNORED_NAMES
        or relative.suffix in _IGNORED_SUFFIXES
    )


class _ModuleEventHandler(FileSystemEventHandler):
    def __init__(self, loader: ModuleLoader, loop: asyncio.AbstractEventLoop) -> None:
        self._loader = loader
        self._loop = loop
        self._pending: dict[str, float] = {}
        self._lock_set: set[str] = set()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # The event path is somewhere under modules/<id>/...
        try:
            relative = (
                Path(event.src_path).resolve().relative_to(self._loader.modules_dir.resolve())
            )
        except (ValueError, OSError):
            return
        parts = relative.parts
        if not parts:
            return
        module_id = parts[0]
        # Skip build artifacts + bytecode the (re)install/​re-import writes back
        # under the module dir; reacting to them loops the watcher forever.
        if _is_ignored(relative):
            return
        self._schedule(module_id)

    def _schedule(self, module_id: str) -> None:
        now = time.monotonic()
        self._pending[module_id] = now
        # Coalesce: fire one reload per module after the debounce window.
        if module_id in self._lock_set:
            return
        self._lock_set.add(module_id)
        asyncio.run_coroutine_threadsafe(self._debounce_and_fire(module_id, now), self._loop)

    async def _debounce_and_fire(self, module_id: str, scheduled_at: float) -> None:
        try:
            while True:
                await asyncio.sleep(_DEBOUNCE_S)
                last = self._pending.get(module_id, 0)
                if last <= scheduled_at:
                    break
                scheduled_at = last
            await self._reload(module_id)
        finally:
            self._lock_set.discard(module_id)
            self._pending.pop(module_id, None)

    async def _reload(self, module_id: str) -> None:
        folder = self._loader.modules_dir / module_id
        manifest_path = folder / "manifest.yaml"
        if not folder.exists() or not manifest_path.exists():
            # Folder deleted → unload (the loader handles missing gracefully).
            try:
                await self._loader.unload_one(module_id)
                log.info("modules.hot_reload.unloaded", module_id=module_id)
            except Exception:
                log.exception("modules.hot_reload.unload_failed", module_id=module_id)
            return
        try:
            manifest = Manifest.load_yaml(manifest_path)
        except ModuleManifestError as exc:
            log.error("modules.hot_reload.manifest_invalid", module_id=module_id, error=str(exc))
            return
        try:
            await self._loader.load_one(folder, manifest)
            log.info("modules.hot_reload.reloaded", module_id=module_id)
        except Exception:
            log.exception("modules.hot_reload.reload_failed", module_id=module_id)


class HotReload:
    """Wrapper for `watchdog.Observer` lifecycle."""

    def __init__(self, loader: ModuleLoader) -> None:
        self._loader = loader
        self._observer: Observer | None = None

    def start(self) -> None:
        modules_dir = self._loader.modules_dir
        if not modules_dir.exists():
            log.info("modules.hot_reload.no_modules_dir", dir=str(modules_dir))
            return
        loop = asyncio.get_running_loop()
        handler = _ModuleEventHandler(self._loader, loop)
        observer = Observer()
        observer.schedule(handler, str(modules_dir), recursive=True)
        observer.start()
        self._observer = observer
        log.info("modules.hot_reload.started", dir=str(modules_dir))

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=5.0)
        except Exception:
            log.exception("modules.hot_reload.stop_failed")
        self._observer = None


def sweep_stale_workdirs(max_age_hours: float = 24.0) -> int:
    """Delete leftover `ff-mod-*` temp dirs older than `max_age_hours`.

    The per-invocation cleanup in `loader._invoke_handler` handles the happy
    path; this sweep catches the rare crash/SIGKILL leak so per-module
    scratch space doesn't accumulate forever on long-lived deployments.
    Returns the number of dirs removed.
    """
    tmp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for entry in tmp_root.glob("ff-mod-*"):
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    if removed:
        log.info("modules.workdir.swept_stale", removed=removed)
    return removed
