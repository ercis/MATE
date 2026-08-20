"""`importlib.abc.MetaPathFinder` for in_process module isolation (§5.4).

Each loaded module gets its own ``.venv/site-packages`` registered with this
finder, keyed by the namespace prefix the loader uses to import the module's
own code (``mate_mod_<id>``). When an import is initiated *from inside*
that module's code, the finder consults the module's site-packages first,
then the platform's stdlib + inherits + SDK (which are already on
``sys.path``).

Identification of the calling module relies on walking the calling frame
chain looking for a ``__name__`` starting with ``mate_mod_``. This is
not bulletproof - frames can be detached or stripped - but it covers the
common case and the alternative (full `importlib` finder per module loaded
under its own meta-path) is heavier than v1 needs.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

NAMESPACE_PREFIX = "mate_mod_"


def module_namespace(module_id: str) -> str:
    return f"{NAMESPACE_PREFIX}{module_id.replace('-', '_')}"


def calling_module_id() -> str | None:
    """Return the manifest id of the module whose code is currently importing,
    or None if the import isn't initiated from a module."""
    frame = sys._getframe(1)
    while frame is not None:
        modname: str = frame.f_globals.get("__name__", "")
        if modname.startswith(NAMESPACE_PREFIX):
            # Strip the prefix and any submodule suffix.
            stripped = modname[len(NAMESPACE_PREFIX) :]
            return stripped.split(".", 1)[0]
        frame = frame.f_back
    return None


class ModuleVenvFinder(MetaPathFinder):
    """Resolves imports from a module's own venv site-packages."""

    def __init__(self) -> None:
        self._sites: dict[str, Path] = {}
        self._inherit: dict[str, frozenset[str]] = {}

    def register(
        self,
        module_id: str,
        site_packages: Path,
        *,
        inherit: list[str] | None = None,
    ) -> None:
        self._sites[module_id] = site_packages
        self._inherit[module_id] = frozenset(name.lower() for name in (inherit or []))
        log.debug("modules.finder.register", module_id=module_id, site=str(site_packages))

    def unregister(self, module_id: str) -> None:
        self._sites.pop(module_id, None)
        self._inherit.pop(module_id, None)

    def find_spec(
        self,
        name: str,
        path: Sequence[str] | None = None,
        target: object | None = None,
    ) -> ModuleSpec | None:
        owner = calling_module_id()
        if owner is None:
            return None
        site = self._sites.get(owner)
        if site is None or not site.exists():
            return None
        # Inherited packages must come from the platform venv to avoid ABI
        # drift between the module's transitively-installed copy and the
        # platform's. Defer to default finders for them.
        top_level = name.split(".", 1)[0].lower()
        if top_level in self._inherit.get(owner, frozenset()):
            log.debug("modules.finder.inherit_defer", module_id=owner, name=name)
            return None
        # For submodule imports, Python passes the parent package's
        # __path__ via the `path` argument. Honour it so PathFinder
        # searches inside the package directory - falling back to `site`
        # would let `PathFinder.find_spec("gast.gast", [site])` resolve
        # to `site/gast/__init__.py` (the package, not `gast/gast.py`),
        # making `from .gast import *` recurse into the same file.
        search_path = list(path) if path else [str(site)]
        spec = PathFinder.find_spec(name, search_path)
        if spec is None:
            return None
        log.debug("modules.finder.resolved_in_module_venv", module_id=owner, name=name)
        return spec


_finder: ModuleVenvFinder | None = None


def get_finder() -> ModuleVenvFinder:
    global _finder
    if _finder is None:
        _finder = ModuleVenvFinder()
        # Insert at the front so module deps win over platform deps for the
        # owning module. The caller-frame check ensures we don't shadow other
        # callers.
        sys.meta_path.insert(0, _finder)
    return _finder


def reset_finder() -> None:
    global _finder
    if _finder is not None and _finder in sys.meta_path:
        sys.meta_path.remove(_finder)
    _finder = None
